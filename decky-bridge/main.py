"""Read SaveSync's local installer queue as the Deck user."""

import json
import os
import re
import shlex
import tempfile
from pathlib import Path

import decky


QUEUE = Path(decky.DECKY_USER_HOME) / ".local/share/savesync/steam-queue"


def entry(key):
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("Invalid installation ID.")
    return QUEUE / (key + ".json")


class Plugin:
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
        handle, name = tempfile.mkstemp(dir=QUEUE)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=True)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
