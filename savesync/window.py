"""Open the SaveSync window in a native view, with a browser fallback."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .controller import steam_session
from .snapshots import SaveError


def clean_subprocess_env():
    """Restore the original library path when spawning external programs from a frozen Linux binary."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and os.name != "nt":
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def run_window(server, url, log):
    if webview_window(server, url, log):
        return
    browser_window(server, url, log)


def webview_window(server, url, log):
    try:
        import webview
    except ImportError:
        return False
    window = webview.create_window("SaveSync", url, width=1180, height=780,
                                   min_size=(760, 500), background_color="#111420")

    def close_window():
        try:
            window.destroy()
        except Exception:
            pass

    server.close_window = close_window
    window.events.closed += [lambda: setattr(server, "exit_requested", True)]

    def pump():
        try:
            server.serve_forever()
        except Exception as error:
            print(f"SaveSync server error: {error}", file=log, flush=True)
            close_window()

    threading.Thread(target=pump, name="SaveSync server", daemon=True).start()
    try:
        webview.start()
    except Exception as error:
        print(f"SaveSync native window failed, falling back to a browser window: {error}",
              file=log, flush=True)
        server.exit_requested = True
        server.shutdown()
        server.exit_requested = False
        server.close_window = None
        return False
    server.exit_requested = True
    server.close_window = None
    server.shutdown()
    return True


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
                    return [str(path)], None, "chromium"
        raise SaveError("Install Microsoft Edge or Google Chrome to open the SaveSync window.")

    env = clean_subprocess_env()
    flatpak = shutil.which("flatpak")
    if flatpak:
        for app, kind in (
            ("com.google.Chrome", "chromium"),
            ("org.chromium.Chromium", "chromium"),
            ("com.microsoft.Edge", "chromium"),
            ("com.brave.Browser", "chromium"),
            ("com.vivaldi.Vivaldi", "chromium"),
            ("org.mozilla.firefox", "firefox"),
        ):
            if subprocess.run([flatpak, "info", app], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env, check=False).returncode == 0:
                cache = Path.home() / ".var/app" / app / "cache"
                cache.mkdir(parents=True, exist_ok=True)
                return [flatpak, "run", app], cache, kind
    for executable, kind in (
        ("chromium", "chromium"),
        ("chromium-browser", "chromium"),
        ("google-chrome", "chromium"),
        ("microsoft-edge", "chromium"),
        ("brave-browser", "chromium"),
        ("firefox", "firefox"),
    ):
        if path := shutil.which(executable):
            return [path], None, kind
    raise SaveError("Install Google Chrome, Chromium, or Firefox from Discover, then reopen SaveSync.")


def browser_window(server, url, log):
    command, cache, kind = browser_command()
    server.timeout = 0.25
    with tempfile.TemporaryDirectory(prefix="savesync-window-", dir=cache, ignore_cleanup_errors=True) as profile:
        if kind == "firefox":
            mode = "--kiosk" if steam_session() else "--new-window"
            args = [*command, mode, "--no-remote", "--profile", profile, url]
        else:
            args = [
                *command, "--app=" + url, "--user-data-dir=" + profile,
                "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                "--disable-background-networking", "--start-maximized",
            ]
            if os.name != "nt" and os.environ.get("DISPLAY"):
                args.append("--ozone-platform=x11")
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                              env=clean_subprocess_env()) as window:
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
        subprocess.run([dialog, "--title", "SaveSync", "--error", message],
                       env=clean_subprocess_env(), check=False)
    elif dialog := shutil.which("zenity"):
        subprocess.run([dialog, "--error", "--title=SaveSync", "--text=" + message],
                       env=clean_subprocess_env(), check=False)
    else:
        print(message, file=sys.stderr)
