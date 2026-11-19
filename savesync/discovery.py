"""Read-only integration with Ludusavi's save-location scanner."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from .snapshots import SaveError


def scan_saves(executable, title="", prefix=""):
    command = Path(executable).expanduser() if executable else shutil.which("ludusavi")
    if not command:
        raise SaveError("Install Ludusavi and set its executable under Settings to use discovery.")
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
