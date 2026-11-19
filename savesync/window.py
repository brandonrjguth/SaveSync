"""Open a dedicated SaveSync window using an isolated Chromium profile."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .snapshots import SaveError


def browser_command():
    if os.name == "nt":
        if getattr(sys, "frozen", False):
            # PyInstaller changes DLL lookup for this process and any programs it starts.
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        for executable in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            for root in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
                base = os.environ.get(root)
                if base and (path := Path(base) / executable).is_file():
                    return [str(path)], None
        raise SaveError("Install Microsoft Edge or Google Chrome to open the SaveSync window.")

    flatpak = shutil.which("flatpak")
    if flatpak:
        for app in ("com.google.Chrome", "org.chromium.Chromium"):
            if subprocess.run([flatpak, "info", app], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0:
                cache = Path.home() / ".var/app" / app / "cache"
                cache.mkdir(parents=True, exist_ok=True)
                return [flatpak, "run", app], cache
    for executable in ("chromium", "chromium-browser", "google-chrome"):
        if path := shutil.which(executable):
            return [path], None
    raise SaveError("Install Google Chrome or Chromium from Discover, then reopen SaveSync.")


def run_window(server, url, log):
    command, cache = browser_command()
    server.timeout = 0.25
    with tempfile.TemporaryDirectory(prefix="savesync-window-", dir=cache, ignore_cleanup_errors=True) as profile:
        args = [
            *command, "--app=" + url, "--user-data-dir=" + profile,
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
            "--disable-background-networking", "--start-maximized",
        ]
        if os.name != "nt" and os.environ.get("DISPLAY"):
            args.append("--ozone-platform=x11")
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log) as window:
            try:
                while not server.exit_requested and window.poll() is None:
                    server.handle_request()
                    server.service_actions()
                if not server.exit_requested and window.returncode:
                    raise SaveError(f"The SaveSync window closed with code {window.returncode}.")
            finally:
                if window.poll() is None:
                    window.terminate()
                    try:
                        window.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        window.kill()


def show_error(message):
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "SaveSync", 0x10)
    elif dialog := shutil.which("kdialog"):
        subprocess.run([dialog, "--title", "SaveSync", "--error", message], check=False)
    else:
        print(message, file=sys.stderr)
