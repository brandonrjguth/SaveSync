"""Device configuration and synchronization decisions."""

import json
import os
import platform
import socket
import tempfile
import uuid
from pathlib import Path

from .discovery import find_ludusavi
from .snapshots import (
    ID, SaveError, apply_files, atomic_file, files_at, fingerprint, history, metadata, path_kind, read_snapshot,
    replace_saves, scan, stage_snapshot, write_json, write_snapshot,
)


def data_home():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "SaveSync"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "savesync"


def clean_path(value):
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise SaveError("Enter an absolute path on this device.")
    return path.resolve()


def overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


class Library:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.config = self.directory / "device.json"

    def state(self):
        try:
            return json.loads(self.config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"device": socket.gethostname(), "platform": platform.system(),
                    "shared": "", "ludusavi": "", "maps": {}}

    def default_shared(self):
        folder = self.directory / "shared"
        (folder / "SaveSync-v1").mkdir(parents=True, exist_ok=True)
        return folder

    def settings(self, data):
        state = self.state()
        shared = clean_path(data["shared"]) if data["shared"].strip() else self.default_shared()
        if shared is not None:
            if not shared.is_dir():
                raise SaveError("Create or mount the shared folder, then enter its existing path.")
            if shared != (self.directory / "shared") and overlap(shared, self.directory):
                raise SaveError("Choose a shared folder outside SaveSync's local data folder.")
            for mapping in state["maps"].values():
                if mapping.get("path") and overlap(shared / "SaveSync-v1", Path(mapping["path"])):
                    raise SaveError("The shared folder cannot overlap a configured save location.")
            (shared / "SaveSync-v1").mkdir(exist_ok=True)
        if state["shared"] != (str(shared) if shared else ""):
            for mapping in state["maps"].values():
                mapping.pop("baseline", None)
        state.update(shared=str(shared) if shared else "", device=data["device"].strip()[:80] or socket.gethostname(),
                     ludusavi=data["ludusavi"].strip())
        write_json(self.config, state)

    def store(self, state):
        if not state["shared"]:
            default = self.default_shared()
            state["shared"] = str(default)
            write_json(self.config, state)
        store = Path(state["shared"]) / "SaveSync-v1"
        if not store.is_dir():
            if Path(state["shared"]) == (self.directory / "shared"):
                store.mkdir(parents=True, exist_ok=True)
            else:
                raise SaveError("The shared folder is unavailable. Check its mount or sync provider.")
        return store

    def games(self):
        state = self.state()
        games = {}
        warning = ""
        if state["shared"]:
            try:
                for folder in self.store(state).iterdir():
                    if ID.fullmatch(folder.name) and (folder / "game.json").is_file():
                        game = json.loads((folder / "game.json").read_text(encoding="utf-8"))
                        if (game.get("id") != folder.name or game.get("mode") not in ("file", "directory")
                                or not isinstance(game.get("title"), str)):
                            raise SaveError(f"Invalid shared game entry: {folder.name}")
                        games[folder.name] = game
            except (SaveError, OSError, ValueError) as error:
                warning = str(error)
        for key, mapping in state["maps"].items():
            games.setdefault(key, {"id": key, "title": mapping["title"], "mode": mapping["mode"]})
        return {"device": state["device"], "platform": state["platform"], "shared": state["shared"],
                "ludusavi": state["ludusavi"], "ludusavi_ready": find_ludusavi(state["ludusavi"]) is not None,
                "warning": warning,
                "games": [dict(info, path=state["maps"].get(key, {}).get("path", ""),
                               install=state["maps"].get(key, {}).get("install", ""))
                          for key, info in sorted(games.items(), key=lambda item: item[1]["title"].casefold())]}

    def map_game(self, data):
        state = self.state()
        store = self.store(state)
        game_id = data.get("id")
        if game_id:
            game_dir = store / checked_id(game_id)
            game_file = game_dir / "game.json"
            if game_file.is_file():
                game = json.loads(game_file.read_text(encoding="utf-8"))
                if game["id"] != game_id:
                    raise SaveError("The shared game ID is invalid.")
            elif game_id in state["maps"]:
                existing = state["maps"][game_id]
                game = {"id": game_id, "title": existing["title"], "mode": data.get("mode", existing.get("mode", "auto"))}
            else:
                raise SaveError("The shared game ID is invalid.")
        else:
            title = data["title"].strip()
            if not title or len(title) > 160:
                raise SaveError("Enter a title up to 160 characters.")
            mode = data.get("mode", "auto")
            if mode not in ("file", "directory", "auto"):
                raise SaveError("Choose a file or folder.")
            game = {"id": uuid.uuid4().hex, "title": title, "mode": mode}
            game_dir = store / game["id"]

        path = clean_path(data["path"])
        if (path in (Path(path.anchor), Path.home().resolve())
                or overlap(path, self.directory) or overlap(path, store)):
            raise SaveError("Choose a dedicated save location outside the shared and application folders.")
        for key, mapping in state["maps"].items():
            if key != game["id"] and mapping.get("path") and overlap(path, Path(mapping["path"])):
                raise SaveError("This location overlaps another game's save location.")
        previous = state["maps"].get(game["id"])
        if previous and self.pending(game["id"]).exists():
            raise SaveError("Recover the interrupted restore before changing the save location.")
        if game["mode"] == "auto":
            game["mode"] = "file" if path_kind(path) == "file" else "directory"
        install = data.get("install", "")
        if not isinstance(install, str):
            raise SaveError("Invalid game folder.")
        install = install.strip()
        if install:
            install_path = clean_path(install)
            if path_kind(install_path) != "directory":
                raise SaveError("The game folder must be an existing directory.")
            install = str(install_path)
        files_at(path, game["mode"])
        if not (game_dir / "game.json").is_file():
            write_json(game_dir / "game.json", game)
        entry = previous if previous and previous.get("path") == str(path) else dict(game, path=str(path))
        if install:
            entry["install"] = install
        elif not previous or not previous.get("install"):
            entry.pop("install", None)
        else:
            entry["install"] = previous["install"]
        state["maps"][game["id"]] = entry
        write_json(self.config, state)
        return {"id": game["id"]}

    def ensure_game(self, title, install="", game_id=None):
        """Ensure a transferred game appears in the local (and shared, if connected) save library."""
        state = self.state()
        title = title.strip()[:160]
        if not title:
            return None
        install_str = str(clean_path(install)) if install else ""
        games = self.games()["games"]
        match = None
        if game_id and ID.fullmatch(game_id):
            match = next((entry for entry in games if entry["id"] == game_id), None)
        if match is None:
            match = next((entry for entry in games if entry["title"].casefold() == title.casefold()), None)
        gid = match["id"] if match else (game_id if game_id and ID.fullmatch(game_id) else uuid.uuid4().hex)
        mode = match["mode"] if match else "directory"
        if state["shared"]:
            try:
                store = self.store(state)
                game_file = store / gid / "game.json"
                if not game_file.is_file():
                    write_json(game_file, {"id": gid, "title": title, "mode": mode})
            except (SaveError, OSError):
                pass
        existing = state["maps"].get(gid)
        if existing:
            if install_str:
                existing["install"] = install_str
        else:
            entry = {"id": gid, "title": title, "mode": mode, "path": ""}
            if install_str:
                entry["install"] = install_str
            state["maps"][gid] = entry
        write_json(self.config, state)
        return gid

    def set_ludusavi(self, path):
        state = self.state()
        state["ludusavi"] = str(path)
        write_json(self.config, state)

    def pending(self, game_id):
        return self.directory / "recovery" / game_id / "pending.json"

    def inspect(self, state, game_id):
        game = state["maps"].get(checked_id(game_id))
        if game is None:
            raise SaveError("Set the save location on this device first.")
        folder = self.store(state) / game_id
        if not (folder / "game.json").is_file():
            raise SaveError("This game is missing from the shared folder. Check Settings and the sync provider.")
        versions, heads = history(folder, game_id)
        if any(info["mode"] != game["mode"] or not info["files"] for info in versions.values()):
            raise SaveError("A shared snapshot does not match this game's save location type.")
        baseline = game.get("baseline")
        if baseline and baseline not in versions:
            raise SaveError("Your last synced version is missing. Wait for the shared folder to finish syncing.")
        path = Path(game["path"])
        current = scan(path, game["mode"])
        status = "empty"
        if len(heads) > 1:
            status = "conflict"
        elif not heads:
            if current:
                status = "upload"
        elif current == versions[heads[0]]["files"]:
            status = "current"
        elif not current or (baseline and current == versions[baseline]["files"]):
            status = "download"
        elif baseline == heads[0]:
            status = "upload"
        else:
            status = "conflict"
        token = fingerprint([current, heads, baseline, game["path"], state["shared"]])
        return game, folder, versions, heads, current, status, token

    def review(self, game_id):
        checked_id(game_id)
        pending = self.pending(game_id)
        if pending.is_file():
            return {"status": "interrupted", "versions": [], "count": 0, "token": ""}
        _, _, versions, heads, current, status, token = self.inspect(self.state(), game_id)
        return {"status": status, "token": token, "count": len(current),
                "versions": [version_summary(info, key in heads)
                             for key, info in sorted(versions.items(), key=lambda item: item[1]["created"], reverse=True)]}

    def sync(self, data):
        state = self.state()
        game_id = checked_id(data["id"])
        pending = self.pending(game_id)
        if pending.is_file():
            raise SaveError("Recover the interrupted restore first.")
        game, folder, versions, heads, current, status, token = self.inspect(state, game_id)
        if data.get("token") != token:
            raise SaveError("The local saves or shared versions changed. Check again before syncing.")
        choice = data.get("choice", "auto")
        if choice == "auto":
            if status == "empty":
                return "No save files yet. Launch the game and make a save, then try again."
            if status == "conflict":
                raise SaveError("Choose which version to keep.")
            choice = "local" if status == "upload" else heads[0]

        path = Path(game["path"])
        if choice == "local":
            if not current:
                raise SaveError("This device has no save files to publish.")
            info = metadata(game, state["device"], heads, current)
            write_snapshot(folder, info, files_at(path, game["mode"]))
            message = "Published this device’s saves."
        else:
            if choice not in versions:
                raise SaveError("The selected version is unavailable.")
            info = versions[choice]
            if current != info["files"] or heads != [choice]:
                with tempfile.TemporaryDirectory(dir=self.directory) as temp:
                    sources = stage_snapshot(folder / (choice + ".zip"), info, Path(temp))
                    replace_saves(path, game, current, info["files"], sources, pending, state["device"])
                    if heads != [choice]:
                        info = metadata(game, state["device"], heads, info["files"])
                        write_snapshot(folder, info, sources)
            message = "Saves are up to date." if status == "current" and choice == heads[0] else "Applied the selected saves."
        game["baseline"] = info["id"]
        write_json(self.config, state)
        return message

    def recover(self, game_id):
        state = self.state()
        game = state["maps"].get(checked_id(game_id))
        pending = self.pending(game_id)
        if not game or not pending.is_file():
            raise SaveError("No interrupted restore is pending.")
        archive = pending.parent / (checked_id(json.loads(pending.read_bytes())["backup"]) + ".zip")
        info = read_snapshot(archive, game_id)
        path = Path(game["path"])
        with tempfile.TemporaryDirectory(dir=self.directory) as temp:
            original = stage_snapshot(archive, info, Path(temp))
            apply_files(path, game["mode"], scan(path, game["mode"]), info["files"], original)
        pending.unlink()
        return "Restored the local copy from before the interruption."

    def auto_publish_all(self):
        """Publish any non-conflicting local save changes across mapped games into SaveSync-v1."""
        state = self.state()
        for key, mapping in list(state["maps"].items()):
            if not mapping.get("path") or self.pending(key).is_file():
                continue
            try:
                _, _, _, _, _, status, token = self.inspect(self.state(), key)
                if status == "upload":
                    self.sync({"id": key, "token": token, "choice": "auto"})
            except (SaveError, OSError, ValueError, KeyError, TypeError):
                continue

    def auto_apply_all(self):
        """Apply any non-conflicting downloaded snapshots across mapped games."""
        applied = 0
        state = self.state()
        for key, mapping in list(state["maps"].items()):
            if not mapping.get("path") or self.pending(key).is_file():
                continue
            try:
                _, _, _, _, _, status, token = self.inspect(self.state(), key)
                if status in ("download", "current"):
                    self.sync({"id": key, "token": token, "choice": "auto"})
                    if status == "download":
                        applied += 1
            except (SaveError, OSError, ValueError, KeyError, TypeError):
                continue
        return applied

    def list_store_snapshots(self):
        """Return all games and snapshot IDs in the local SaveSync-v1 store after auto-publishing."""
        self.auto_publish_all()
        store = self.store(self.state())
        entries = []
        for folder in sorted(store.iterdir()):
            game_file = folder / "game.json"
            if not (ID.fullmatch(folder.name) and game_file.is_file()):
                continue
            try:
                game = json.loads(game_file.read_text(encoding="utf-8"))
                if (game.get("id") != folder.name or game.get("mode") not in ("file", "directory")
                        or not isinstance(game.get("title"), str)):
                    continue
                versions, _ = history(folder, folder.name)
                # Topological / creation-time order so parents arrive before children
                ordered = [k for k, _ in sorted(versions.items(), key=lambda item: (len(item[1]["parents"]), item[1]["created"]))]
                entries.append({"game": {"id": game["id"], "title": game["title"], "mode": game["mode"]},
                                "snapshots": ordered})
            except (SaveError, OSError, ValueError, KeyError, TypeError):
                continue
        return entries

    def snapshot_path(self, game_id, snap_id):
        store = self.store(self.state())
        path = store / checked_id(game_id) / (checked_id(snap_id) + ".zip")
        if path_kind(path) != "file":
            raise SaveError("Snapshot not found.")
        return path

    def import_snapshot(self, game_info, snap_id, raw_bytes):
        """Validate and store a snapshot .zip received over LAN."""
        if not isinstance(game_info, dict):
            raise SaveError("Invalid game metadata.")
        game_id = checked_id(game_info.get("id", ""))
        snap_id = checked_id(snap_id)
        title = str(game_info.get("title", "")).strip()[:160]
        mode = game_info.get("mode")
        if not title or mode not in ("file", "directory"):
            raise SaveError("Invalid game metadata.")
        store = self.store(self.state())
        folder = store / game_id
        folder.mkdir(parents=True, exist_ok=True)
        game_file = folder / "game.json"
        if not game_file.is_file():
            write_json(game_file, {"id": game_id, "title": title, "mode": mode})
        self._reconcile_title_mapping(store, game_id, title, mode)
        target = folder / (snap_id + ".zip")
        if target.is_file():
            return False
        with tempfile.TemporaryDirectory(dir=folder) as temp_dir:
            staged = Path(temp_dir) / (snap_id + ".zip")
            staged.write_bytes(raw_bytes)
            info = read_snapshot(staged, game_id)
            if info["id"] != snap_id or info["mode"] != mode:
                raise SaveError("Snapshot metadata did not match.")
            os.replace(staged, target)
        return True

    def _reconcile_title_mapping(self, store, game_id, title, mode):
        """If this device mapped the same game title under a local-only ID with no snapshots, merge it to game_id."""
        state = self.state()
        if game_id in state["maps"] and state["maps"][game_id].get("path"):
            return
        for other_id, mapping in list(state["maps"].items()):
            if other_id == game_id or mapping.get("title", "").casefold() != title.casefold():
                continue
            other_folder = store / other_id
            other_zips = list(other_folder.glob("*.zip")) if other_folder.is_dir() else []
            if not other_zips:
                merged = dict(mapping, id=game_id, title=title, mode=mode)
                merged.pop("baseline", None)
                state["maps"][game_id] = merged
                state["maps"].pop(other_id, None)
                if other_folder.is_dir():
                    try:
                        (other_folder / "game.json").unlink(missing_ok=True)
                        other_folder.rmdir()
                    except OSError:
                        pass
                write_json(self.config, state)
                return


def checked_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SaveError("Invalid game or snapshot ID.")
    return value


def version_summary(info, head):
    return {key: info[key] for key in ("id", "device", "created")} | {"count": len(info["files"]), "head": head}
