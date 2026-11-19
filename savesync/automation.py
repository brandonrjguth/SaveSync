"""Safe launch/exit actions shared by the Windows wrapper and Decky."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .cloud_sync import CloudSync
from .library import checked_id, clean_path
from .snapshots import SaveError, write_json


def action(library, key, operation):
    key = checked_id(key)
    cloud = CloudSync(library)
    if not cloud.settings():
        raise SaveError("Pair this device with SaveSync Cloud first.")
    queue = library.directory / "pending-upload" / (key + ".json")
    if operation == "retry" and not queue.is_file():
        return {"message": ""}
    review = cloud.review(key)
    remote = review["cloud"]
    if operation == "status":
        latest = next((v for v in review["versions"] if v["id"] == remote.get("latest")), None)
        return {"latest": latest, "pending": queue.is_file(), "error": remote.get("error")}
    if operation == "retry":
        if json.loads(queue.read_bytes())["content"] != review.get("content_token"):
            queue.unlink(missing_ok=True)
            return {"message": "Local progress changed. Upload after closing the game."}
        operation = "upload"
    if operation not in ("upload", "download"):
        raise SaveError("Unknown save action.")
    if library.save_detection(key).is_file() or review["status"] == "interrupted":
        raise SaveError("Finish save location detection or recovery first.")
    if review["status"] == "conflict" or remote.get("conflict"):
        raise SaveError("Save conflict. Choose a version in SaveSync.")
    if remote.get("error"):
        if operation == "download":
            raise SaveError(remote["error"])
        if review["status"] == "upload":
            library.sync({"id": key, "token": review["token"], "choice": "local"},
                         device_id=cloud.settings()["id"])
        if review["status"] in ("upload", "current"):
            write_json(queue, {"content": review["content_token"]})
            return {"message": "Cloud unavailable. Local snapshot kept for retry."}
        return {"message": "No local changes to upload."}
    choice = "local" if operation == "upload" else remote["latest"]
    if not choice or (choice == "local" and not review["count"]):
        return {"message": "No save available for this action."}
    if remote["current"]:
        queue.unlink(missing_ok=True)
        return {"message": "Up to date with the cloud."}
    try:
        message = cloud.sync({"id": key, "choice": choice, "token": review["token"],
                              "cloud_token": remote["token"]}, automatic=True)
    except SaveError:
        if operation == "upload":
            write_json(queue, {"content": review["content_token"]})
        raise
    queue.unlink(missing_ok=True)
    return {"message": message}


def launcher_path(library, key):
    return library.directory / "launchers" / (checked_id(key) + ".json")


def windows_script(script, payload, timeout):
    process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command",
                              "$ErrorActionPreference='Stop';" + script], input=json.dumps(payload), text=True,
                             encoding="utf-8", capture_output=True, timeout=timeout,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise SaveError(process.stderr.strip().splitlines()[0] if process.stderr.strip()
                        else "Windows could not complete the request.")
    return process.stdout.strip()


POWERSHELL_INPUT = """
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
"""


def browse_executable(library, key):
    if os.name != "nt":
        raise SaveError("The executable picker is available on Windows.")
    game = library.state()["maps"].get(checked_id(key))
    if not game:
        raise SaveError("Choose a game from the SaveSync library first.")
    install = game.get("install")
    starting_folder = install if install and Path(install).is_dir() else str(Path.home())
    script = POWERSHELL_INPUT + """
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$picker = New-Object System.Windows.Forms.OpenFileDialog
try {
    $picker.Title = 'Choose the game executable'
    $picker.InitialDirectory = $p.folder
    $picker.Filter = 'Windows executables (*.exe)|*.exe'
    $picker.CheckFileExists = $true
    if ($picker.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($picker.FileName)
    }
} finally {
    $picker.Dispose()
    $owner.Dispose()
}
"""
    return {"path": windows_script(script, {"folder": starting_folder}, timeout=300)}


def browse_game_folder(folder):
    if os.name != "nt":
        raise SaveError("The folder picker is available on Windows.")
    starting_folder = Path.home()
    if folder.strip():
        try:
            candidate = clean_path(folder)
            if candidate.is_dir():
                starting_folder = candidate
        except (SaveError, OSError):
            pass
    script = POWERSHELL_INPUT + """
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$picker = New-Object System.Windows.Forms.FolderBrowserDialog
try {
    $picker.Description = 'Choose the Windows game folder'
    $picker.SelectedPath = $p.folder
    $picker.ShowNewFolderButton = $false
    if ($picker.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($picker.SelectedPath)
    }
} finally {
    $picker.Dispose()
    $owner.Dispose()
}
"""
    return {"path": windows_script(script, {"folder": str(starting_folder)}, timeout=300)}


def create_shortcut(library, data):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise SaveError("Install the Windows SaveSync release to create game shortcuts.")
    key = checked_id(data["id"])
    game = library.state()["maps"].get(key)
    if not game or not game.get("path") or not CloudSync(library).settings():
        raise SaveError("Set a save location and pair this device with the cloud first.")
    exe = clean_path(data["exe"])
    if not exe.is_file() or exe.suffix.lower() != ".exe":
        raise SaveError("Choose the game's Windows executable.")
    game_args = data.get("args", "")
    if not isinstance(game_args, str) or len(game_args) > 4000 or any(ord(char) < 32 for char in game_args):
        raise SaveError("Enter valid game arguments.")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", game["title"])[:75].strip(" .")
    arguments = subprocess.list2cmdline(["--data-dir", str(library.directory), "--launch-game", key])
    payload = {"target": sys.executable, "args": arguments, "name": f"SaveSync - {name} ({key[:8]}).lnk",
               "icon": str(exe)}
    script = POWERSHELL_INPUT + """
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('DesktopDirectory', 'Create')
if (!$desktop) { throw 'Windows could not locate your Desktop folder.' }
$path = Join-Path $desktop $p.name
$link = $shell.CreateShortcut($path)
$link.TargetPath = $p.target
$link.Arguments = $p.args
$link.IconLocation = $p.icon + ',0'
$link.Save()
[Console]::WriteLine($path)
"""
    path = windows_script(script, payload, timeout=15)
    if not path or not Path(path).is_file():
        raise SaveError("Windows could not create the desktop shortcut.")
    write_json(launcher_path(library, key), {"exe": str(exe), "args": game_args})
    return {"message": "Desktop shortcut created: " + path}


def launch(library, key, invoke):
    config = json.loads(launcher_path(library, key).read_bytes())
    exe = Path(config["exe"])
    if not exe.is_file() or exe.suffix.lower() != ".exe":
        raise SaveError("The game executable moved. Recreate its SaveSync shortcut.")
    log = library.directory / "launcher.log"
    with log.open("w", encoding="utf-8") as output:
        def sync(operation):
            try:
                result = invoke(key, operation)
                print(result["message"], file=output, flush=True)
                return True
            except (SaveError, OSError, ValueError) as error:
                print(str(error), file=output, flush=True)
                return False
        if sync("retry"):
            sync("download")
        command = subprocess.list2cmdline([str(exe)]) + " " + config["args"]
        game = subprocess.Popen(command, executable=str(exe), cwd=exe.parent)
        result = game.wait()
        sync("upload")
        return result
