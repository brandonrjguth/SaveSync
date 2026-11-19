"""Save-location discovery with Ludusavi, including automatic setup."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .snapshots import SaveError
from .window import clean_subprocess_env

RELEASE_URL = "https://api.github.com/repos/mtkennerly/ludusavi/releases/latest"
ASSET_NAME = {"nt": re.compile(r"^ludusavi-v[\d.]+-win64\.zip$"),
              "posix": re.compile(r"^ludusavi-v[\d.]+-linux\.tar\.gz$")}
BINARY_NAME = {"nt": "ludusavi.exe", "posix": "ludusavi"}
MAX_DOWNLOAD = 256 * 1024**2


def find_ludusavi(configured):
    if configured and configured.strip():
        candidate = Path(configured.strip()).expanduser()
        return candidate if candidate.is_file() else None
    return shutil.which("ludusavi")


def scan_saves(executable, title="", prefix=""):
    command = find_ludusavi(executable)
    if not command:
        raise SaveError("Ludusavi is not installed. Select Download Ludusavi to set it up automatically, "
                        "or enter the save location manually.")
    args = [str(command), "backup", "--preview", "--api", "--no-cloud-sync"]
    if prefix:
        folder = Path(prefix).expanduser()
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
    if report.get("errors", {}).get("unknownGames") and title:
        raise SaveError("Ludusavi did not recognize that title. Try its exact store name or enter a save path manually.")
    if process.returncode:
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
    return results


def safe_member(name):
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return bool(parts) and not name.startswith(("/", "\\")) and ".." not in parts and len(name) <= 400


def download_asset(url, expected_digest, destination):
    request = urllib.request.Request(url, headers={"User-Agent": "SaveSync"})
    hasher = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
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
        with urllib.request.urlopen(request, timeout=30) as response:
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
