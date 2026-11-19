"""Launch the local SaveSync interface."""

import argparse
import http.client
import json
import os
import secrets
import subprocess
import sys
import traceback
import webbrowser
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .discovery import find_ludusavi, install_ludusavi, scan_saves
from .game_transfers import GameTransfers
from .library import Library, data_home
from .snapshots import SaveError
from .window import run_window, show_error


UI = Path(__file__).with_name("ui")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/app.css": ("app.css", "text/css"),
          "/transfers.js": ("transfers.js", "text/javascript")}


@contextmanager
def device_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "device.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SaveError("SaveSync is already running with this device configuration.") from error
        yield


class LocalServer(HTTPServer):
    def service_actions(self):
        self.transfers.complete_pending()


class Handler(BaseHTTPRequestHandler):
    timeout = 5

    def reply(self, status, content, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                         "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def json_reply(self, status, data):
        self.reply(status, json.dumps(data).encode(), "application/json")

    def check_host(self):
        return self.headers.get("Host") == self.server.host

    def do_GET(self):
        if not self.check_host():
            self.send_error(403)
            return
        asset = STATIC.get(self.path)
        if asset is None:
            self.send_error(404)
            return
        filename, content_type = asset
        content = (UI / filename).read_text(encoding="utf-8-sig")
        if self.path == "/":
            content = content.replace("__SESSION_TOKEN__", self.server.token)
            content = content.replace("__WINDOW_MODE__", str(self.server.window_mode).lower())
        self.reply(200, content.encode(), content_type)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if (not self.check_host() or origin not in (None, "http://" + self.server.host)
                or not secrets.compare_digest(self.headers.get("X-SaveSync-Token", ""), self.server.token)):
            self.send_error(403)
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise SaveError("Expected a JSON request.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise SaveError("Invalid request size.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise SaveError("Invalid request data.")
            library = self.server.library
            actions = {
                "/api/state": lambda: library.games(),
                "/api/settings": lambda: library.settings(data),
                "/api/map": lambda: library.map_game(data),
                "/api/review": lambda: library.review(data["id"]),
                "/api/sync": lambda: {"message": library.sync(data)},
                "/api/recover": lambda: {"message": library.recover(data["id"])},
                "/api/discover": lambda: {"results": scan_saves(
                    library.state()["ludusavi"], data.get("title", "").strip(), data.get("prefix", "").strip())},
                "/api/ludusavi": lambda: ludusavi_action(library, data),
            }
            if self.path == "/api/exit" and self.server.window_mode:
                self.json_reply(200, {})
                self.server.exit_requested = True
                close = getattr(self.server, "close_window", None)
                if close is not None:
                    close()
                return
            if self.path.startswith("/api/transfer/"):
                self.json_reply(200, self.server.transfers.action(self.path.removeprefix("/api/transfer/"), data))
                return
            if self.path not in actions:
                self.send_error(404)
                return
            response = actions[self.path]()
            status = 200
        except (SaveError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, http.client.HTTPException) as error:
            status, response = 400, {"error": str(error)}
        except Exception:
            traceback.print_exc(file=self.server.log)
            status, response = 500, {"error": "SaveSync encountered an error. Check the launch log or terminal for details."}
        self.json_reply(status, response or {})

    def log_message(self, format, *args):
        pass


def ludusavi_action(library, data):
    action = data.get("action", "status")
    current = find_ludusavi(library.state()["ludusavi"])
    if action == "status":
        return {"ready": current is not None, "path": str(current) if current else ""}
    if action == "install":
        if current is None:
            current = install_ludusavi(library.directory)
            library.set_ludusavi(str(current))
        return {"path": str(current)}
    raise SaveError("Unknown Ludusavi action.")


def main(window_default=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_home(), help="Local device configuration folder")
    parser.add_argument("--port", type=int, default=0, help="Loopback port (default: available port)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-browser", action="store_true", help="Print the address without opening it")
    mode.add_argument("--window", action="store_true", help="Open a dedicated SaveSync window")
    args = parser.parse_args()
    library = Library(args.data_dir)
    window_mode = args.window or (window_default and not args.no_browser)
    log_path = library.directory / "window.log"
    transfers = GameTransfers(library)
    try:
        with device_lock(library.directory), LocalServer(("127.0.0.1", args.port), Handler) as server:
            server.socket.settimeout(None)
            server.host = f"127.0.0.1:{server.server_port}"
            server.token = secrets.token_urlsafe(32)
            server.library = library
            server.transfers = transfers
            server.window_mode = window_mode
            server.exit_requested = False
            server.log = sys.stderr
            url = "http://" + server.host + "/"
            if window_mode:
                with log_path.open("w", encoding="utf-8") as log:
                    server.log = log
                    print(f"SaveSync is running at {url}", file=log, flush=True)
                    run_window(server, url, log)
                return 0
            print(f"SaveSync is running at {url}", flush=True)
            if not args.no_browser:
                if os.environ.get("TERMUX_VERSION"):
                    subprocess.Popen(["termux-open-url", url])
                else:
                    webbrowser.open(url)
            print("Press Ctrl+C here to stop.", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (SaveError, OSError) as error:
        if window_mode:
            show_error(f"{error}\n\nWindow log: {log_path}")
        else:
            print(f"SaveSync: {error}", file=sys.stderr)
        return 1
    finally:
        transfers.close()


if __name__ == "__main__":
    sys.exit(main())
