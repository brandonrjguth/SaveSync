"""Snapshots and conflict detection for a directory or a single save file."""

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath


class SaveError(Exception):
    pass


ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
SINGLE_FILE = "save"
MAX_FILES = 20000
MAX_BYTES = 8 * 1024**3


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


@contextmanager
def atomic_file(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".savesync-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(name)
    try:
        yield temp
        with temp.open("r+b") as stream:
            os.fsync(stream.fileno())
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, value):
    with atomic_file(path) as temp:
        temp.write_bytes(encode(value))


def path_kind(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise SaveError(f"Save folders cannot contain links or junctions: {path}")
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    raise SaveError(f"Unsupported file type: {path}")


def validate_names(names, mode):
    if mode not in ("file", "directory"):
        raise SaveError("Invalid save location type.")
    if mode == "file" and set(names) - {SINGLE_FILE}:
        raise SaveError("Invalid single-file snapshot.")
    seen = {}
    files = set()
    for name in names:
        parts = name.split("/")
        for index, part in enumerate(parts):
            if (not part or part in (".", "..") or part.endswith((" ", "."))
                    or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part)
                    or PureWindowsPath(part).is_reserved()):
                raise SaveError(f"Filename cannot be synced across devices: {name}")
            prefix = "/".join(parts[:index + 1])
            folded = prefix.casefold()
            if seen.setdefault(folded, prefix) != prefix:
                raise SaveError(f"Save paths differ only by letter case: {name}")
            if index == len(parts) - 1:
                files.add(prefix)
    for name in files:
        while "/" in name:
            name = name.rsplit("/", 1)[0]
            if name in files:
                raise SaveError(f"A save path is both a folder and a file: {name}")


def files_at(path, mode):
    for parent in path.parents:
        if path_kind(parent) not in (None, "directory"):
            raise SaveError(f"A file blocks the save location: {parent}")
    kind = path_kind(path)
    if kind is None:
        return {}
    if kind != mode:
        raise SaveError(f"The configured save location is expected to be a {mode}: {path}")
    if mode == "file":
        return {SINGLE_FILE: path}

    files = {}

    def walk_error(error):
        raise error

    for folder, dirs, filenames in os.walk(path, onerror=walk_error):
        for dirname in dirs:
            if path_kind(Path(folder) / dirname) != "directory":
                raise SaveError("The save folder changed during scanning.")
        for filename in filenames:
            entry = Path(folder) / filename
            if path_kind(entry) != "file":
                raise SaveError("The save folder changed during scanning.")
            files[entry.relative_to(path).as_posix()] = entry
            if len(files) > MAX_FILES:
                raise SaveError("This folder contains too many files. Choose a smaller save folder.")
    validate_names(files, mode)
    return files


def digest_stream(source, destination=None, limit=MAX_BYTES):
    hasher = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise SaveError("The save snapshot exceeds its size limit.")
        hasher.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return [hasher.hexdigest(), size]


def scan(path, mode):
    result = {}
    remaining = MAX_BYTES
    for name, entry in sorted(files_at(path, mode).items()):
        with entry.open("rb") as stream:
            result[name] = digest_stream(stream, limit=remaining)
        remaining -= result[name][1]
    return result


def metadata(game, device, parents, files):
    return {
        "format": 1, "id": uuid.uuid4().hex, "game": game["id"],
        "title": game["title"], "mode": game["mode"], "device": device,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parents": sorted(parents), "files": files,
    }


def write_snapshot(folder, info, sources):
    path = folder / (info["id"] + ".zip")
    with atomic_file(path) as temp:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            archive.writestr("snapshot.json", encode(info))
            for name, (expected_hash, expected_size) in info["files"].items():
                with sources[name].open("rb") as source:
                    with archive.open("files/" + name, "w", force_zip64=True) as output:
                        actual = digest_stream(source, output, expected_size)
                if actual != [expected_hash, expected_size]:
                    raise SaveError("Save files changed during sync. Close the game and try again.")
    return path


def read_snapshot(path, game_id):
    if path_kind(path) != "file":
        raise SaveError(f"Snapshot is unavailable: {path.name}")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo("snapshot.json").file_size > 32 * 1024**2:
                raise ValueError
            info = json.loads(archive.read("snapshot.json"))
            if (info["format"] != 1 or info["id"] != path.stem or info["game"] != game_id
                    or not all(isinstance(info[key], str) for key in ("title", "device", "created"))
                    or not isinstance(info["parents"], list)
                    or not all(ID.fullmatch(parent) and parent != info["id"] for parent in info["parents"])
                    or not isinstance(info["files"], dict) or len(info["files"]) > MAX_FILES):
                raise ValueError
            datetime.fromisoformat(info["created"])
            validate_names(info["files"], info["mode"])
            total = 0
            expected = {"snapshot.json"}
            for name, entry in info["files"].items():
                if (not isinstance(entry, list) or len(entry) != 2 or not HASH.fullmatch(entry[0])
                        or type(entry[1]) is not int or not 0 <= entry[1] <= MAX_BYTES):
                    raise ValueError
                member = "files/" + name
                if archive.getinfo(member).file_size != entry[1]:
                    raise ValueError
                expected.add(member)
                total += entry[1]
            if total > MAX_BYTES or sorted(archive.namelist()) != sorted(expected):
                raise ValueError
    except (ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as error:
        raise SaveError(f"Snapshot {path.name} is invalid or still transferring. Check the shared folder.") from error
    return info


def history(folder, game_id):
    if not folder.is_dir():
        return {}, []
    versions = {
        path.stem: read_snapshot(path, game_id)
        for path in folder.iterdir()
        if path.suffix == ".zip" and ID.fullmatch(path.stem)
    }
    parents = {parent for info in versions.values() for parent in info["parents"]}
    if parents - versions.keys():
        raise SaveError("Some snapshot history is missing. Wait for the shared folder to finish syncing.")
    # All revisions must descend from a root. This also rejects loops in damaged metadata.
    pending = {key: set(info["parents"]) for key, info in versions.items()}
    children = {key: [] for key in versions}
    for key, wanted in pending.items():
        for parent in wanted:
            children[parent].append(key)
    ready = [key for key, wanted in pending.items() if not wanted]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for child in children[key]:
            pending[child].remove(key)
            if not pending[child]:
                ready.append(child)
    if visited != len(versions):
        raise SaveError("The snapshot history contains an invalid revision chain.")
    return versions, sorted(versions.keys() - parents)


def stage_snapshot(archive_path, info, staging):
    sources = {}
    with zipfile.ZipFile(archive_path) as archive:
        for index, (name, expected) in enumerate(info["files"].items()):
            staged = staging / str(index)
            with archive.open("files/" + name) as source, staged.open("wb") as output:
                if digest_stream(source, output, expected[1]) != expected:
                    raise SaveError(f"Snapshot checksum failed: {name}")
            sources[name] = staged
    return sources


def destination(path, mode, name):
    return path if mode == "file" else path / name


def apply_files(path, mode, before, after, sources):
    validate_names(before.keys() | after.keys(), mode)
    for name in after:
        target = destination(path, mode, name)
        # Validate every destination before touching any live files.
        for parent in [target, *target.parents]:
            kind = path_kind(parent)
            if kind is not None and kind != ("file" if parent == target else "directory"):
                raise SaveError(f"A file or folder blocks restoration: {parent}")
    for name, checksum in after.items():
        if before.get(name) != checksum:
            target = destination(path, mode, name)
            with atomic_file(target) as temp:
                shutil.copyfile(sources[name], temp)
    for name in before.keys() - after.keys():
        destination(path, mode, name).unlink()


def replace_saves(path, game, before, after, sources, pending, device):
    if before == after:
        return
    mode = game["mode"]
    recovery = pending.parent
    backup = metadata(game, device, [], before)
    archive = write_snapshot(recovery, backup, files_at(path, mode))
    if scan(path, mode) != before:
        raise SaveError("Save files changed during sync. Close the game and try again.")
    write_json(pending, {"backup": backup["id"]})
    try:
        apply_files(path, mode, before, after, sources)
    except (OSError, SaveError) as error:
        try:
            with tempfile.TemporaryDirectory(dir=recovery) as temporary:
                original = stage_snapshot(archive, backup, Path(temporary))
                apply_files(path, mode, scan(path, mode), before, original)
        except (OSError, SaveError, zipfile.BadZipFile) as rollback_error:
            raise SaveError(f"Restore was interrupted. Your original saves are in {archive}. "
                            f"Automatic recovery failed: {rollback_error}") from error
        pending.unlink()
        raise SaveError(f"Restore failed and the original saves were preserved: {error}") from error
    pending.unlink()
