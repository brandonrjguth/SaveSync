"""Read SaveSync's local installer queue as the Deck user."""

import asyncio
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

import decky


QUEUE = Path(decky.DECKY_USER_HOME) / ".local/share/savesync/steam-queue"
DATA = QUEUE.parent
CONFIG = DATA / "deck-automation.json"
APP = Path(decky.DECKY_USER_HOME) / ".local/share/SaveSync/app/SaveSync"


def read(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def entry(key):
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("Invalid installation ID.")
    return QUEUE / (key + ".json")


class Plugin:
    async def game_sync_state(self):
        maps = read(DATA / "device.json", {}).get("maps", {})
        games = {key: info["title"] for key, info in maps.items() if info.get("path")}
        cached = read(CONFIG, {})
        bindings = {}
        for path in (DATA / "game-installs").glob("*.json"):
            try:
                record = read(path, {})
                key = (record.get("save") or {}).get("id")
                appid = read(entry(path.stem), {}).get("appid")
                if key in games and isinstance(appid, int) and appid:
                    bindings[str(appid)] = {"id": key}
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        for appid, settings in cached.items():
            if settings.get("id") in games:
                bindings[appid] = settings
        for settings in bindings.values():
            settings["pending"] = (DATA / "pending-upload" / (settings["id"] + ".json")).is_file()
        return {"bindings": bindings, "games": games}

    async def game_sync_config(self, appid, game_id, auto, download):
        if type(appid) is not int or not 0 < appid <= 0xffffffff:
            raise ValueError("Invalid Steam app ID.")
        current = await self.game_sync_state()
        if game_id not in current["games"]:
            raise ValueError("Map this game's save location in SaveSync first.")
        config = read(CONFIG, {})
        config[str(appid)] = {"id": game_id, "auto": bool(auto), "download": bool(download)}
        save(CONFIG, config)

    async def game_sync_action(self, game_id, action):
        state = await self.game_sync_state()
        if game_id not in state["games"] or not re.fullmatch(r"[0-9a-f]{32}", game_id):
            return {"error": "Map this game's save location in SaveSync first."}
        if action not in ("status", "upload", "download", "retry"):
            return {"error": "Unknown save action."}
        if not APP.is_file():
            return {"error": "Install the matching Steam Deck SaveSync update."}
        process = await asyncio.create_subprocess_exec(str(APP), "--data-dir", str(DATA), "--automation", action,
                                                       game_id, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=240)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"error": "SaveSync timed out. Check the game in the SaveSync app."}
        try:
            return json.loads(output)
        except ValueError:
            return {"error": "SaveSync did not return a result. Check that both updates are installed."}

    async def jobs(self):
        results = []
        for path in sorted(QUEUE.glob("*.json")):
            try:
                if entry(path.stem) != path or path.is_symlink() or path.stat().st_size > 16384:
                    continue
                item = json.loads(path.read_bytes())
                executable = Path(item["exe"])
                if (not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != ".exe"
                        or any(char in str(executable) for char in ('"', chr(36), chr(96), "\r", "\n"))):
                    continue
                prefix = Path(decky.DECKY_USER_HOME) / ".local/share/savesync-prefixes" / path.stem
                result = {key: item[key] for key in ("title", "exe", "status")}
                result["start"] = str(executable.parent)
                result["options"] = f"STEAM_COMPAT_DATA_PATH={shlex.quote(str(prefix))} %command%"
                results.append(dict(result, id=path.stem, appid=item.get("appid", 0), error=item.get("error", "")))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return results

    async def update(self, key, status, appid=0, error=""):
        path = entry(key)
        if status not in ("adding", "added", "error") or type(appid) is not int or not 0 <= appid <= 0xffffffff:
            raise ValueError("Invalid Steam shortcut result.")
        if path.is_symlink():
            raise ValueError("Invalid shortcut queue file.")
        data = json.loads(path.read_bytes())
        data.update(status=status, appid=appid, error=str(error)[:500])
        save(path, data)
