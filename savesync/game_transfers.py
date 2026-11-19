"""Connect transferred installations to Steam and the save library."""

import http.client
import json
import os
import threading
from pathlib import Path

from .library import checked_id, clean_path, overlap
from .snapshots import SaveError, path_kind, validate_names, write_json
from .steam_shortcuts import add_steam_shortcut, decky_bridge_installed
from .transfer import PORT, Host, catalog, discover_hosts, download, game_files, install_parent, manifest_for, pair

HELPER_EXE_HINTS = (
    "unitycrashhandler", "crashreport", "crashhandler", "unins", "uninstall",
    "vcredist", "dxsetup", "redist", "ue4prereq", "ueprereq", "dotnet", "directx",
)


def rank_executables(names):
    def sort_key(name):
        stem = Path(name).stem.casefold()
        is_helper = any(hint in stem for hint in HELPER_EXE_HINTS)
        depth = name.count("/")
        return (is_helper, depth, name.casefold())

    return sorted(names, key=sort_key)


def queue_dir():
    return Path.home() / ".local/share/savesync/steam-queue"


def save_hint(library, key, game_root):
    if not key:
        return None
    game = library.state()["maps"].get(checked_id(key))
    if game is None:
        raise SaveError("Map the linked game's Windows save path before sharing.")
    path = Path(game["path"])
    if path != game_root and path.is_relative_to(game_root):
        return {"id": key, "base": "game", "path": path.relative_to(game_root).as_posix()}
    user = Path.home().resolve()
    if path != user and path.is_relative_to(user):
        relative = path.relative_to(user)
        for index, name in enumerate(relative.parts[:-1]):
            windows = {"appdata": "AppData", "documents": "Documents", "saved games": "Saved Games"}
            if name.casefold() in windows and (index == 0 or (
                    name.casefold() == "documents" and index == 1 and relative.parts[0].casefold().startswith("onedrive"))):
                parts = list(relative.parts[index:])
                parts[0] = windows[name.casefold()]
                if parts[0] == "AppData":
                    parts[1] = {"roaming": "Roaming", "local": "Local", "locallow": "LocalLow"}.get(
                        parts[1].casefold(), parts[1])
                return {"id": key, "base": "prefix", "path": "/".join(parts)}
    return {"id": key, "base": "", "path": ""}


class GameTransfers:
    def __init__(self, library):
        self.library = library
        self.role = "host" if os.name == "nt" else "deck" if not os.environ.get("TERMUX_VERSION") else ""
        self.host = Host(library.directory)
        self.job = {}
        self.stopping = threading.Event()

    @property
    def receipts(self):
        return self.library.directory / "game-installs"

    def status(self):
        result = {"role": self.role, "job": self.job.copy()}
        if self.role == "host":
            result.update(self.host.status())
        if self.role == "deck":
            result["default_parent"] = str(Path.home() / "Games")
            result["installs"] = []
            for path in sorted(self.receipts.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                record = json.loads(path.read_bytes())
                if not Path(record["target"]).is_dir():
                    continue
                item = {"id": record["id"], "title": record["title"], "target": record["target"],
                        "steam": record["steam"], "save_message": record.get("save_message", ""),
                        "exe": record.get("exe", "")}
                item["needs_exe"] = bool(item["steam"]) and not item["exe"]
                file = queue_dir() / (record["id"] + ".json")
                item["shortcut"] = json.loads(file.read_bytes()).get("status", "pending") if file.is_file() else ""
                hint = record.get("save")
                item["save_id"] = hint.get("id", "") if isinstance(hint, dict) else ""
                prefix = Path.home() / ".local/share/savesync-prefixes" / record["id"] / "pfx"
                if isinstance(hint, dict) and hint.get("base") == "prefix" and (prefix / "drive_c").is_dir():
                    item["prefix"] = str(prefix)
                result["installs"].append(item)
        return result

    def only(self, role):
        if self.role != role:
            raise SaveError("This action is available on Windows." if role == "host"
                            else "This action is available on Steam Deck.")

    def action(self, action, data):
        if action == "status":
            return self.status()
        if action in ("start", "stop", "scan", "share", "share_library", "remove"):
            self.only("host")
        else:
            self.only("deck")
        if action == "start":
            self.host.start(data["address"])
            self.share_library_games()
        elif action == "stop":
            if self.job.get("running"):
                raise SaveError("Wait for the game folder scan to finish.")
            self.host.stop()
        elif action == "scan":
            sources, _ = game_files(self.source_path(data["folder"]), exclude=self.excluded_paths())
            return {"executables": [name for name in sorted(sources) if Path(name).suffix.lower() == ".exe"][:300]}
        elif action == "remove":
            self.host.remove(data["id"])
        elif action == "share":
            self.share(data)
        elif action == "share_library":
            game_id = data.get("id")
            self.share_library_games({checked_id(game_id)} if game_id else None)
        elif action == "discover":
            return {"hosts": discover_hosts()}
        elif action == "connect":
            return self.connect(data)
        elif action == "exes":
            return {"executables": self.list_executables(checked_id(data["id"]))}
        elif action == "download":
            self.fetch(data)
        elif action == "finish":
            exe = data.get("exe", "")
            if not isinstance(exe, str):
                raise SaveError("Invalid executable choice.")
            self.finish(checked_id(data["id"]), exe.strip())
        else:
            raise SaveError("Unknown transfer action.")
        return self.status()

    def excluded_paths(self):
        paths = [self.library.directory]
        shared = self.library.state()["shared"]
        if shared:
            paths.append(Path(shared) / "SaveSync-v1")
        return paths

    def source_path(self, value):
        root = clean_path(value)
        if path_kind(root) == "file":
            raise SaveError(f"Choose the game’s folder rather than a file (for example: {root.parent}).")
        if root in (Path(root.anchor), Path.home().resolve()):
            raise SaveError("Choose a specific game folder rather than an entire drive or your home folder.")
        if root.is_relative_to(self.library.directory):
            raise SaveError("The game folder cannot be inside SaveSync’s data folder.")
        shared = self.library.state()["shared"]
        if shared and root.is_relative_to(Path(shared) / "SaveSync-v1"):
            raise SaveError("The game folder cannot be inside SaveSync’s shared snapshot folder (SaveSync-v1).")
        return root

    def start_job(self, label, run):
        if self.job.get("running"):
            raise SaveError("Another game transfer task is already running.")
        self.job = {"running": True, "label": label}

        def worker():
            try:
                result = run()
                self.job = {"running": False, "label": label, **result}
            except (SaveError, OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
                self.job = {"running": False, "label": label, "error": str(error)}
            except Exception as error:
                self.job = {"running": False, "label": label, "error": f"Transfer failed: {error}"}

        threading.Thread(target=worker, name=label, daemon=True).start()

    def share(self, data):
        if self.host.server is None:
            raise SaveError("Start sharing on the LAN first.")
        if self.job.get("running"):
            raise SaveError("Another game transfer task is already running.")
        root = self.source_path(data["folder"])
        key = data.get("save_id")
        hint = save_hint(self.library, key, root)
        if key:
            review = self.library.review(key)
            if review["status"] == "upload":
                self.library.sync({"id": key, "token": review["token"], "choice": "auto"})
            elif review["status"] == "conflict":
                raise SaveError("Resolve the linked game's save conflict before sharing it.")
            elif review["status"] == "interrupted":
                raise SaveError("Recover the linked game's interrupted restore before sharing it.")
        title, executable = data["title"].strip(), data["exe"].strip()

        def prepare():
            manifest = manifest_for(root, title, executable, hint, exclude=self.excluded_paths())
            self.host.add(root, manifest)
            return {"message": "Game is ready to download on the Deck."}

        self.start_job("Preparing game folder", prepare)

    def share_library_games(self, game_ids=None):
        if self.role != "host" or self.host.server is None or self.job.get("running"):
            return
        state = self.library.state()
        entries = []
        for key, mapping in sorted(state["maps"].items(), key=lambda item: item[1].get("title", "").casefold()):
            if game_ids is not None and key not in game_ids:
                continue
            install = (mapping.get("install") or "").strip()
            if install:
                entries.append((key, mapping.get("title", "").strip(), install))
        if not entries:
            return

        def prepare():
            shared_count = 0
            skipped = []
            excluded = self.excluded_paths()
            for key, title, install in entries:
                if self.stopping.is_set() or self.host.server is None:
                    break
                try:
                    root = self.source_path(install)
                    hint = save_hint(self.library, key, root)
                    try:
                        review = self.library.review(key)
                        if review["status"] == "upload":
                            self.library.sync({"id": key, "token": review["token"], "choice": "auto"})
                    except SaveError:
                        pass
                    sources, _ = game_files(root, exclude=excluded)
                    exes = [name for name in sorted(sources) if Path(name).suffix.lower() == ".exe"]
                    executable = exes[0] if len(exes) == 1 else ""
                    manifest = manifest_for(root, title, executable, hint, exclude=excluded)
                    self.host.add(root, manifest)
                    shared_count += 1
                except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
                    skipped.append(f"{title}: {error}")
            if shared_count and not skipped:
                noun = "game is" if shared_count == 1 else "games are"
                return {"message": f"{shared_count} library {noun} ready to download on the Deck."}
            if shared_count and skipped:
                return {"message": f"{shared_count} library game(s) ready. Skipped: {'; '.join(skipped[:3])}"}
            if skipped:
                return {"error": f"Could not share library game(s): {'; '.join(skipped[:3])}"}
            return {}

        label = "Preparing library game" if len(entries) == 1 else f"Preparing {len(entries)} library games"
        self.start_job(label, prepare)

    def share_library_game_if_active(self, game_id):
        if self.role == "host" and self.host.server is not None and not self.job.get("running"):
            self.share_library_games({game_id})

    def connect(self, data):
        raw_invite = str(data.get("invite") or "").strip()
        if "#" in raw_invite:
            return {"games": catalog(raw_invite), "invite": raw_invite}
        code = str(data.get("code") or raw_invite).strip()
        address = str(data.get("address") or "").strip()
        raw_port = data.get("port")
        port = PORT
        if address and ":" in address:
            host_part, _, port_part = address.rpartition(":")
            if port_part.isdigit():
                address = host_part.strip()
                port = int(port_part)
        if raw_port not in (None, ""):
            try:
                port = int(raw_port)
            except (ValueError, TypeError) as error:
                raise SaveError("Invalid PC port.") from error
        if not address:
            hosts = discover_hosts()
            if not hosts:
                raise SaveError("No sharing PC was found on this network. Make sure Start sharing is active on the PC, or enter its IP address below.")
            if len(hosts) > 1:
                raise SaveError("Multiple PCs are sharing on this network. Choose your PC from the list above.")
            address = hosts[0]["address"]
            port = hosts[0]["port"]
        invite = pair(address, code, port)
        return {"games": catalog(invite), "invite": invite, "host": address}

    def fetch(self, data):
        parent = install_parent(data["folder"], self.library)
        key = checked_id(data["id"])
        receipt = self.receipts / (key + ".json")
        if receipt.is_file() and Path(json.loads(receipt.read_bytes())["target"]).is_dir():
            raise SaveError("This game is already downloaded. Its setup can be retried below.")
        invite, steam = data["invite"], data.get("steam") is True

        def report(done, total):
            if self.stopping.is_set():
                raise SaveError("Download cancelled while closing SaveSync.")
            self.job.update(done=done, total=total)

        def record_install(manifest, target):
            record = {"id": key, "title": manifest["title"], "exe": manifest["exe"], "target": str(target),
                      "save": manifest.get("save"), "steam": steam, "save_message": ""}
            write_json(receipt, record)

        def install():
            manifest, _ = download(invite, key, parent, report, record_install)
            message = "Game downloaded."
            if steam and not manifest["exe"]:
                message = "Game downloaded. Choose its executable below to add it to Steam."
            return {"message": message, "installed": key}

        self.start_job("Downloading game", install)

    def list_executables(self, key):
        receipt = self.receipts / (key + ".json")
        if not receipt.is_file():
            raise SaveError("The game has not been downloaded.")
        target = Path(json.loads(receipt.read_bytes())["target"])
        sources, _ = game_files(target)
        exes = [name for name in sources if Path(name).suffix.lower() == ".exe"]
        return rank_executables(exes)[:300]

    def finish(self, key, exe=""):
        receipt = self.receipts / (key + ".json")
        if not receipt.is_file():
            raise SaveError("The game has not been downloaded.")
        record = json.loads(receipt.read_bytes())
        target = Path(record["target"])
        if exe:
            if "\\" in exe or exe.startswith("/") or ".." in exe.split("/") or Path(exe).suffix.lower() != ".exe":
                raise SaveError("Choose a Windows .exe inside the game folder.")
            record["exe"] = exe
        executable = target / record["exe"] if record["exe"] else None
        if executable is not None and path_kind(executable) != "file":
            raise SaveError("The downloaded executable is missing.")
        prefix = Path.home() / ".local/share/savesync-prefixes" / key

        # Set up the save location and library entry before making the shortcut visible to Steam.
        record["save_message"] = self.link_saves(record, target, prefix)
        write_json(receipt, record)
        if record["steam"] and executable is not None:
            shortcut = queue_dir() / (key + ".json")
            if not shortcut.exists() or exe:
                if any(char in str(executable) for char in ('"', chr(36), chr(96), "\r", "\n")):
                    raise SaveError("The executable path has characters Steam cannot safely launch.")
                prefix.mkdir(parents=True, exist_ok=True)
                if decky_bridge_installed():
                    write_json(shortcut, {"title": record["title"], "exe": str(executable), "status": "pending"})
                else:
                    appid = add_steam_shortcut(record["title"], executable, prefix)
                    if appid is not None:
                        write_json(shortcut, {"title": record["title"], "exe": str(executable),
                                              "status": "added_direct", "appid": appid})
                    else:
                        write_json(shortcut, {"title": record["title"], "exe": str(executable), "status": "pending"})

    def link_saves(self, record, target, prefix):
        hint = record.get("save")
        hint_id = hint.get("id") if isinstance(hint, dict) else None
        try:
            ensured_id = self.library.ensure_game(record["title"], install=str(target), game_id=hint_id)
            if not hint:
                return "Added to Game library. Use Find local saves to link its save folder."
            key = checked_id(hint["id"]) if hint_id else ensured_id
            games = self.library.games()["games"]
            game = next((entry for entry in games if entry["id"] == key), None)
            if game is None:
                return "Added to Game library. Connect the same shared save folder as the PC, then retry setup."
            if not game["path"]:
                if hint["base"] not in ("game", "prefix") or not hint["path"]:
                    return "Added to Game library. Open it there and use Find local saves."
                if hint["base"] == "prefix" and not record["steam"]:
                    return "Added to Game library. Map the save location after adding the game to Steam."
                if not self.library.state()["shared"]:
                    return "Added to Game library. Set a shared folder in Settings, then retry setup."
                validate_names([hint["path"]], "directory")
                root = target if hint["base"] == "game" else prefix / "pfx/drive_c/users/steamuser"
                self.library.map_game({"id": key, "path": str(root / hint["path"]), "install": str(target)})
            if not self.library.state()["shared"]:
                return "Added to Game library. Set a shared folder in Settings to sync saves."
            review = self.library.review(key)
            if review["status"] in ("download", "current"):
                self.library.sync({"id": key, "token": review["token"], "choice": "auto"})
                return "Linked and up to date. Use the Game library for later syncs."
            if review["status"] == "conflict":
                return "Save versions differ. Choose the progress to keep in Game library."
            if review["status"] == "upload":
                return "Local saves are ready to upload from Game library."
            return "Save location linked. Upload from the PC when saves are available."
        except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
            return f"Save setup needs attention: {error}"

    def complete_pending(self):
        key = self.job.get("installed")
        if not key or self.job.get("finished"):
            return
        self.job["finished"] = True
        try:
            self.finish(key)
        except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
            self.job["error"] = f"Game downloaded. Setup needs attention: {error}"

    def close(self):
        self.stopping.set()
        self.host.stop()
