"""Private, persistent SaveSync cloud service. Run behind an HTTPS reverse proxy."""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .library import checked_id, version_summary
from .snapshots import MAX_BYTES, SaveError, encode, read_snapshot


MAX_ARCHIVE = MAX_BYTES + 128 * 1024**2
INVITE_TTL = 600
TOKEN_LENGTH = 43
INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
INVITE_LENGTH = 8
PAIR_WINDOW = 60
PAIR_FAILURE_LIMIT = 30


def secret_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class CloudStore:
    def __init__(self, directory, bootstrap):
        self.directory = directory
        self.bootstrap = bootstrap
        directory.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS invites (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pair_failures (id INTEGER PRIMARY KEY, expires INTEGER NOT NULL, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    game TEXT NOT NULL REFERENCES games(id), id TEXT NOT NULL,
                    summary TEXT NOT NULL, parents TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (game, id)
                );
            """)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.directory / "cloud.db", timeout=30)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db

    def authenticate(self, token):
        if len(token) != TOKEN_LENGTH:
            return None
        with self.connection() as db:
            row = db.execute("SELECT id FROM devices WHERE token=?", (secret_digest(token),)).fetchone()
        return row[0] if row else None

    def invite_digest(self, code):
        normalized = "".join(code.split()).replace("-", "").upper()
        if len(normalized) == INVITE_LENGTH and all(char in INVITE_ALPHABET for char in normalized):
            return hmac.new(self.bootstrap.encode(), normalized.encode(), hashlib.sha256).hexdigest()
        # Accept outstanding invites from before the shorter format until they expire.
        return secret_digest(code)

    def enroll(self, code, name):
        if not isinstance(code, str) or not 1 <= len(code) <= 256:
            return None
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
            raise SaveError("Enter a device name of up to 80 characters.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            failures = db.execute("SELECT expires, count FROM pair_failures WHERE id=1").fetchone()
            if failures and failures[0] > now and failures[1] >= PAIR_FAILURE_LIMIT:
                raise PairingLimited("Too many incorrect pairing attempts. Wait a minute and try again.")
            first_device = not db.execute("SELECT 1 FROM devices LIMIT 1").fetchone()
            if first_device:
                accepted = hmac.compare_digest(secret_digest(code), secret_digest(self.bootstrap))
            else:
                accepted = db.execute("DELETE FROM invites WHERE digest=? AND expires>?",
                                      (self.invite_digest(code), now)).rowcount != 0
            if not accepted:
                expires, count = failures if failures and failures[0] > now else (now + PAIR_WINDOW, 0)
                db.execute("INSERT OR REPLACE INTO pair_failures VALUES (1, ?, ?)", (expires, count + 1))
                return None
            device = secrets.token_hex(16)
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO devices VALUES (?, ?, ?)", (device, secret_digest(token), name))
            return {"id": device, "token": token}

    def invite(self):
        expires = int(time.time()) + INVITE_TTL
        with self.connection() as db:
            db.execute("DELETE FROM invites WHERE expires<=?", (time.time(),))
            if db.execute("SELECT count(*) FROM invites").fetchone()[0] >= 32:
                raise SaveError("Too many active pairing codes. Wait for an unused code to expire.")
            while True:
                code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))
                if db.execute("INSERT OR IGNORE INTO invites VALUES (?, ?)",
                              (self.invite_digest(code), expires)).rowcount:
                    break
        return {"code": f"{code[:4]}-{code[4:]}", "expires": expires}

    def unpair(self, device):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM devices").fetchone()[0] <= 1:
                raise SaveError("Pair another device before disconnecting the last device.")
            db.execute("DELETE FROM devices WHERE id=?", (device,))

    def games(self):
        with self.connection() as db:
            rows = db.execute("SELECT id, title, mode FROM games ORDER BY title COLLATE NOCASE").fetchall()
        return [dict(id=gid, title=title, mode=mode) for gid, title, mode in rows]

    def game(self, key):
        with self.connection() as db:
            game = db.execute("SELECT title,mode FROM games WHERE id=?", (key,)).fetchone()
            if not game:
                return None
            rows = db.execute("SELECT summary,parents FROM snapshots WHERE game=? ORDER BY rowid", (key,)).fetchall()
        versions = [json.loads(summary) for summary, _ in rows]
        parents = {parent for _, ancestry in rows for parent in json.loads(ancestry)}
        for info in versions:
            info["head"] = info["id"] not in parents
        return {"game": dict(id=key, title=game[0], mode=game[1]), "versions": versions}

    def archive(self, key, snapshot):
        with self.connection() as db:
            row = db.execute("SELECT digest FROM snapshots WHERE game=? AND id=?", (key, snapshot)).fetchone()
        return (self.directory / "archives" / key / (snapshot + ".zip"), row[0]) if row else None

    def store(self, key, snapshot, path, digest, device):
        info = read_snapshot(path, key)
        if not info["files"] or not 1 <= len(info["title"]) <= 160 or not 1 <= len(info["device"]) <= 80:
            raise SaveError("Invalid snapshot metadata.")
        if info.get("device_id"):
            checked_id(info["device_id"])
            # Snapshot origin is preserved when another paired client republishes a cached archive.
        timestamp = datetime.fromisoformat(info["created"])
        if timestamp.tzinfo is None:
            raise SaveError("A snapshot must specify its time zone.")
        summary = version_summary(info, True)
        summary["created"] = timestamp.astimezone(timezone.utc).isoformat()
        summary["received"] = datetime.now(timezone.utc).isoformat()
        summary["uploader_id"] = device
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest FROM snapshots WHERE game=? AND id=?", (key, snapshot)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise SaveError("A different archive already uses this snapshot ID.")
                return
            game = db.execute("SELECT mode FROM games WHERE id=?", (key,)).fetchone()
            if game and game[0] != info["mode"]:
                raise SaveError("This game's cloud saves use a different location type.")
            for parent in set(info["parents"]):
                if not db.execute("SELECT 1 FROM snapshots WHERE game=? AND id=?", (key, parent)).fetchone():
                    raise SaveError("Upload parent snapshots before their descendants.")
            destination = self.directory / "archives" / key / (snapshot + ".zip")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, destination)
            db.execute("INSERT OR IGNORE INTO games VALUES (?,?,?)", (key, info["title"], info["mode"]))
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                       (key, snapshot, json.dumps(summary), json.dumps(info["parents"]), digest))


class PairingLimited(SaveError):
    pass


class CloudHandler(BaseHTTPRequestHandler):
    timeout = 60

    def log_message(self, format, *args):
        # Keep device credentials and game identifiers out of access logs.
        pass

    def json_reply(self, status, data):
        body = encode(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def receive(self, limit):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= limit or self.headers.get("Transfer-Encoding"):
            raise SaveError("Invalid request size.")
        return size

    def device(self):
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        key = self.server.store.authenticate(token) if scheme == "Bearer" else None
        if key is None:
            self.json_reply(401, {"error": "This device is not paired to the cloud server."})
        return key

    def do_GET(self):
        if self.path == "/health":
            self.json_reply(200, {"ok": True})
            return
        if not self.device():
            return
        store = self.server.store
        try:
            if self.path == "/v1/games":
                self.json_reply(200, store.games())
                return
            parts = self.path.split("/")
            if parts[:3] == ["", "v1", "games"]:
                if len(parts) == 4 and (game := store.game(checked_id(parts[3]))):
                    self.json_reply(200, game)
                    return
                if len(parts) == 5 and (archive := store.archive(checked_id(parts[3]), checked_id(parts[4]))):
                    path, digest = archive
                    with path.open("rb") as data:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Length", str(os.fstat(data.fileno()).st_size))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-SaveSync-SHA256", digest)
                        self.end_headers()
                        shutil.copyfileobj(data, self.wfile, length=1024**2)
                    return
            self.json_reply(404, {"error": "Cloud snapshot or game was not found."})
        except SaveError as error:
            self.json_reply(400, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (OSError, sqlite3.Error):
            self.json_reply(503, {"error": "Cloud storage is temporarily unavailable."})

    def do_POST(self):
        store = self.server.store
        try:
            if self.path == "/v1/pair":
                payload = json.loads(self.rfile.read(self.receive(4096)))
                paired = store.enroll(payload["code"], payload["name"])
                self.json_reply(200 if paired else 403, paired or {"error": "Pairing code is invalid, used or expired."})
                return
            device = self.device()
            if not device:
                return
            if self.path == "/v1/invite":
                self.json_reply(200, store.invite())
                return
            if self.path == "/v1/disconnect":
                store.unpair(device)
                self.json_reply(200, {"ok": True})
                return
            self.json_reply(404, {"error": "Unknown cloud request."})
        except PairingLimited as error:
            self.json_reply(429, {"error": str(error)})
        except (SaveError, ValueError, KeyError, TypeError) as error:
            self.json_reply(400, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self.json_reply(503, {"error": "Cloud storage is temporarily unavailable."})

    def do_PUT(self):
        device = self.device()
        if not device:
            return
        try:
            parts = self.path.split("/")
            if len(parts) != 5 or parts[:3] != ["", "v1", "games"]:
                self.json_reply(404, {"error": "Unknown cloud request."})
                return
            key, snapshot = map(checked_id, parts[3:])
            remaining = self.receive(MAX_ARCHIVE)
            digest = hashlib.sha256()
            with tempfile.TemporaryDirectory(dir=self.server.store.directory) as temp:
                path = Path(temp) / (snapshot + ".zip")
                with path.open("wb") as archive:
                    while remaining:
                        block = self.rfile.read(min(remaining, 1024**2))
                        if not block:
                            raise SaveError("Upload ended before the archive was complete.")
                        archive.write(block)
                        digest.update(block)
                        remaining -= len(block)
                    archive.flush()
                    os.fsync(archive.fileno())
                self.server.store.store(key, snapshot, path, digest.hexdigest(), device)
            self.json_reply(200, {"id": snapshot})
        except (SaveError, ValueError, KeyError, TypeError) as error:
            self.json_reply(400, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self.json_reply(503, {"error": "Cloud storage is temporarily unavailable."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--bootstrap-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=47836)
    args = parser.parse_args()
    code = args.bootstrap_file.read_text(encoding="utf-8").strip()
    if len(code) < 32 or len(code) > 256:
        parser.error("Use a random bootstrap code between 32 and 256 characters.")
    with ThreadingHTTPServer(("127.0.0.1", args.port), CloudHandler) as server:
        server.store = CloudStore(args.data_dir, code)
        server.serve_forever()


if __name__ == "__main__":
    main()
