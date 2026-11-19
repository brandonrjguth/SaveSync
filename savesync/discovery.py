"""Save-location discovery with Ludusavi, including automatic setup."""

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .snapshots import SaveError, files_at, validate_names
from .steam_shortcuts import steam_root
from .window import clean_subprocess_env

RELEASE_URL = "https://api.github.com/repos/mtkennerly/ludusavi/releases/latest"
ASSET_NAME = {"nt": re.compile(r"^ludusavi-v[\d.]+-win64\.zip$"),
              "posix": re.compile(r"^ludusavi-v[\d.]+-linux\.tar\.gz$")}
BINARY_NAME = {"nt": "ludusavi.exe", "posix": "ludusavi"}
MAX_DOWNLOAD = 256 * 1024**2


def tls_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for cafile in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if Path(cafile).is_file():
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def bundled_ludusavi():
    candidate = Path(__file__).with_name("tools") / BINARY_NAME[os.name]
    return candidate if candidate.is_file() else None


def find_ludusavi(configured):
    if configured and configured.strip():
        candidate = Path(configured.strip()).expanduser()
        return candidate if candidate.is_file() else None
    bundled = bundled_ludusavi()
    if bundled is not None:
        return bundled
    return shutil.which("ludusavi")


SAVE_DIR_NAMES = {
    "save", "saves", "savedata", "savegame", "savegames", "savefiles",
    "userdata", "profile", "profiles", "slots", "saveslots",
}
SAVE_FILE_EXTS = {".sav", ".save", ".slot", ".srm", ".sol", ".es3"}
SKIP_DIR_NAMES = {
    "savesync-v1", "_commonredist", "redist", "directx", "vcredist",
    "engine", "monobleedingedge", "managed", "resources", "node_modules",
    ".git", "shader", "shaders", "movies", "videos", "audio", "music", "textures",
}


def _strip_quotes(value):
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1].strip()
    return text


def scan_game_folder(folder, title=""):
    """Look inside a game's install folder for portable save folders or save files."""
    root = Path(_strip_quotes(str(folder))).expanduser()
    if not root.is_dir():
        return []
    found = []
    seen = set()
    visited_dirs = 0
    for current, directories, files in os.walk(root):
        visited_dirs += 1
        if visited_dirs > 400 or len(found) >= 80:
            break
        base = Path(current)
        depth = len(base.relative_to(root).parts)
        if depth >= 4:
            directories[:] = []
        else:
            directories[:] = [
                name for name in sorted(directories)
                if name.casefold() not in SKIP_DIR_NAMES and not name.startswith(".")
            ]
        in_save_dir = any(part.casefold() in SAVE_DIR_NAMES for part in base.relative_to(root).parts)
        for name in sorted(files):
            file_path = base / name
            suffix = file_path.suffix.lower()
            if in_save_dir or suffix in SAVE_FILE_EXTS:
                # Skip huge binary assets (> 64 MiB) even if named .sav/.dat
                try:
                    if not file_path.is_file() or file_path.stat().st_size > 64 * 1024**2:
                        continue
                except OSError:
                    continue
                resolved = str(file_path)
                if resolved not in seen:
                    seen.add(resolved)
                    found.append({"path": resolved, "folder": str(base)})
                    if len(found) >= 80:
                        break
    if not found:
        return []
    label = title.strip() or re.sub(r"-[0-9a-f]{8}$", "", root.name)
    return [{"title": label, "files": found}]


def _savesync_prefixes():
    base = Path.home() / ".local/share/savesync-prefixes"
    if not base.is_dir():
        return []
    prefixes = []
    try:
        for entry in sorted(base.iterdir()):
            pfx = entry / "pfx"
            if (pfx / "drive_c").is_dir():
                prefixes.append(pfx)
    except OSError:
        pass
    return prefixes


def _steam_prefixes():
    root = steam_root()
    if root is None:
        return
    libraries = {root.resolve()}
    try:
        config = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8-sig")
        for value in re.findall(r'^\s*"path"\s*"((?:\\.|[^"\\])*)"', config, re.MULTILINE | re.IGNORECASE):
            path = Path(re.sub(r'\\([\\"])', r'\1', value))
            if path.is_absolute():
                libraries.add(path.resolve())
    except (OSError, ValueError):
        pass
    for library in sorted(libraries):
        try:
            for entry in (library / "steamapps/compatdata").iterdir():
                if entry.name.isascii() and entry.name.isdecimal():
                    yield entry / "pfx"
        except OSError:
            continue


def _windows_relative(root, parts):
    for part in parts:
        exact = root / part
        if exact.exists():
            root = exact
        else:
            matches = [child for child in root.iterdir() if child.name.casefold() == part.casefold()]
            if len(matches) != 1:
                return None
            root = matches[0]
    return root.resolve()


def observe_prefix_saves(relative, mode, prefix=""):
    """Read save-file metadata across custom and Steam-managed Proton prefixes."""
    validate_names([relative], "directory")
    parts = PurePosixPath(relative).parts
    if prefix:
        path = Path(_strip_quotes(prefix)).expanduser()
        if not path.is_absolute():
            return []
        prefixes = [path]
    else:
        prefixes = [*_savesync_prefixes(), *_steam_prefixes()]
    matches = {}
    for pfx in prefixes:
        try:
            users = list((pfx / "drive_c/users").iterdir())
        except OSError:
            continue
        for user in users:
            try:
                if not user.is_dir():
                    continue
                candidate = _windows_relative(user, parts)
                if candidate is None or str(candidate) in matches:
                    continue
                files = {}
                for path in files_at(candidate, mode).values():
                    info = path.stat()
                    files[str(path)] = [info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino]
                if files:
                    matches[str(candidate)] = files
            except (OSError, SaveError, ValueError):
                continue
    return matches


def find_prefix_saves(title, relative, mode, prefix=""):
    matches = []
    for location, files in observe_prefix_saves(relative, mode, prefix).items():
        latest = max(files, key=lambda name: (files[name][0], name))
        folder = location if mode == "directory" else str(Path(location).parent)
        matches.append({"path": latest, "folder": folder, "modified": files[latest][0] // 1_000_000})
    matches.sort(key=lambda match: (-match["modified"], match["path"]))
    return [{"title": title, "files": matches}] if matches else []


def _run_ludusavi(command, title="", prefix=""):
    args = [str(command), "backup", "--preview", "--api", "--no-cloud-sync"]
    if prefix:
        folder = Path(_strip_quotes(str(prefix))).expanduser()
        if not folder.is_absolute() or not (folder / "drive_c").is_dir():
            raise SaveError("Enter a Wine or Proton prefix with a drive_c folder, such as compatdata/<id>/pfx.")
        args.extend(["--wine-prefix", str(folder)])
    if title:
        args.extend(["--", title])
    try:
        process = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=180, stdin=subprocess.DEVNULL,
                                 env=clean_subprocess_env(),
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError as error:
        raise SaveError("Ludusavi could not be found. Check the executable path in Settings.") from error
    except subprocess.TimeoutExpired as error:
        raise SaveError("Ludusavi did not finish within three minutes. Try one title at a time.") from error
    try:
        report = json.loads(process.stdout)
    except ValueError:
        raise SaveError(process.stderr.strip()[-1500:] or "Ludusavi did not return a scan report.") from None
    unknown = bool(report.get("errors", {}).get("unknownGames") and title)
    if process.returncode and not unknown:
        raise SaveError(process.stderr.strip()[-1500:] or "Ludusavi could not complete the save scan.")
    results = []
    for name, entry in sorted(report.get("games", {}).items(), key=lambda pair: pair[0].casefold()):
        known = []
        untagged = []
        for filename, details in entry.get("files", {}).items():
            if details.get("ignored") or details.get("failed") or not Path(filename).is_file():
                continue
            tags = details.get("tags", [])
            if "save" in tags:
                known.append(filename)
            elif not tags:
                untagged.append(filename)
        if known or untagged:
            results.append({
                "title": name,
                "files": [{"path": filename, "folder": str(Path(filename).parent)}
                          for filename in sorted(known) + sorted(untagged)],
            })
    return results, unknown


def _merge_results(groups):
    merged = {}
    for group in groups:
        for item in group:
            key = item["title"].casefold()
            if key not in merged:
                merged[key] = {"title": item["title"], "files": [], "_seen": set()}
            target = merged[key]
            for entry in item["files"]:
                if entry["path"] not in target["_seen"]:
                    target["_seen"].add(entry["path"])
                    target["files"].append(entry)
    return [{"title": item["title"], "files": item["files"]}
            for item in sorted(merged.values(), key=lambda entry: entry["title"].casefold())
            if item["files"]]


def scan_saves(executable, title="", prefix="", install=""):
    folder_groups = []
    if install and install.strip():
        install_path = Path(_strip_quotes(install)).expanduser()
        if not install_path.is_dir():
            raise SaveError("The game install folder does not exist on this device.")
        folder_groups.append(scan_game_folder(install_path, title))
    elif os.name != "nt":
        games_dir = Path.home() / "Games"
        if games_dir.is_dir():
            try:
                for entry in sorted(games_dir.iterdir()):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    if title and title.casefold() not in entry.name.casefold():
                        continue
                    folder_groups.append(scan_game_folder(entry, title if title else ""))
            except OSError:
                pass

    command = find_ludusavi(executable)
    if not command:
        merged = _merge_results(folder_groups)
        if merged:
            return merged
        raise SaveError("Ludusavi is not installed. Select Download Ludusavi to set it up automatically, "
                        "or enter the save location manually.")

    ludusavi_groups = []
    primary, unknown = _run_ludusavi(command, title=title, prefix=prefix)
    ludusavi_groups.append(primary)
    if os.name != "nt" and not prefix:
        for pfx in _savesync_prefixes():
            try:
                extra, _ = _run_ludusavi(command, title=title, prefix=str(pfx))
                ludusavi_groups.append(extra)
            except SaveError:
                pass

    merged = _merge_results([*ludusavi_groups, *folder_groups])
    if not merged and unknown and title:
        raise SaveError("Ludusavi did not recognize that title and no save files were found in the game folder. "
                        "Try its exact store name, provide the game folder, or enter a save path manually.")
    return merged


def safe_member(name):
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return bool(parts) and not name.startswith(("/", "\\")) and ".." not in parts and len(name) <= 400


def download_asset(url, expected_digest, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "SaveSync"})
    hasher = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=60, context=tls_context()) as response, destination.open("wb") as output:
            while chunk := response.read(512 * 1024):
                done += len(chunk)
                if done > MAX_DOWNLOAD:
                    raise SaveError("The Ludusavi download exceeded its size limit.")
                hasher.update(chunk)
                output.write(chunk)
    except (urllib.error.URLError, OSError) as error:
        raise SaveError(f"Could not download Ludusavi: {error}") from error
    if expected_digest and hasher.hexdigest() != expected_digest:
        raise SaveError("The Ludusavi download did not match its published checksum.")


def extract_binary(archive, destination):
    wanted = BINARY_NAME[os.name]
    found = []
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if member.endswith(("/", "\\")):
                    continue
                normalized = member.replace("\\", "/")
                if safe_member(normalized) and PurePosixPath(normalized).name == wanted:
                    found.append((member, normalized))
            for member, normalized in found:
                target = destination / PurePosixPath(normalized)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                normalized = member.name.rstrip("/")
                if member.isreg() and safe_member(normalized) and PurePosixPath(normalized).name == wanted:
                    found.append((member.name, normalized))
                    target = destination / PurePosixPath(normalized)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    if not found:
        raise SaveError("The Ludusavi archive did not contain the expected program.")
    best = min(found, key=lambda pair: (pair[1].count("/"), len(pair[1])))
    return destination / PurePosixPath(best[1])


def install_ludusavi(data_directory):
    tools = data_directory / "tools" / "ludusavi"
    try:
        request = urllib.request.Request(RELEASE_URL, headers={"User-Agent": "SaveSync"})
        with urllib.request.urlopen(request, timeout=30, context=tls_context()) as response:
            release = json.loads(response.read(4 * 1024**2))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise SaveError(f"Could not reach GitHub for the Ludusavi release: {error}") from error
    wanted = ASSET_NAME[os.name]
    asset = next((item for item in release.get("assets", [])
                  if isinstance(item, dict) and isinstance(item.get("name"), str)
                  and wanted.fullmatch(item["name"])), None)
    if asset is None or not isinstance(asset.get("browser_download_url"), str):
        raise SaveError("No Ludusavi release matching this platform was found.")
    digest = asset.get("digest", "")
    digest = digest[7:] if isinstance(digest, str) and digest.startswith("sha256:") else ""
    tools.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tools) as temp:
        archive = Path(temp) / asset["name"]
        download_asset(asset["browser_download_url"], digest, archive)
        staged = extract_binary(archive, Path(temp))
        binary = staged.replace(tools / BINARY_NAME[os.name])
    if os.name != "nt":
        binary.chmod(0o755)
    try:
        process = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL,
                                 env=clean_subprocess_env(),
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SaveError(f"The downloaded Ludusavi could not be started: {error}") from error
    if process.returncode:
        raise SaveError("The downloaded Ludusavi did not run correctly on this device.")
    return binary
