"""Authenticated, local-network game folder transfers."""

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import shutil
import socket
import ssl
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .library import checked_id, clean_path, overlap
from .snapshots import HASH, SaveError, digest_stream, encode, fingerprint, path_kind, validate_names

PORT = 47835
MAX_MANIFEST = 32 * 1024**2
MAX_FILES = 200000
NETS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")))
DISCOVER_REQUEST = b"SAVESYNC-DISCOVER-V1"
PAIR_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PAIR_LENGTH = 6
MAX_PAIR_FAILURES = 5
PAIR_NONCE_TTL = 60


def generate_pair_code():
    raw = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LENGTH))
    return f"{raw[:3]}-{raw[3:]}"


def normalize_pair_code(value):
    if not isinstance(value, str):
        raise SaveError("Enter the 6-character pairing code shown on the PC.")
    cleaned = "".join(char for char in value.upper() if char not in " -_\t\r\n")
    if len(cleaned) != PAIR_LENGTH or any(char not in PAIR_ALPHABET for char in cleaned):
        raise SaveError("Enter the 6-character pairing code shown on the PC (for example, K7M-4P9).")
    return cleaned


def pair_key(code_raw, pin, server_nonce, client_nonce):
    salt = f"savesync-pair-v2:{pin}:{server_nonce}:{client_nonce}".encode("ascii")
    return hashlib.scrypt(code_raw.encode("ascii"), salt=salt, n=16384, r=8, p=1, dklen=32)


def pair_proof(key, role):
    return hmac.new(key, role, hashlib.sha256).hexdigest()


def is_hex(value, length):
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def lan_address(value):
    try:
        ip = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise SaveError("Enter the PC’s private IPv4 address.") from error
    if not any(ip in network for network in NETS):
        raise SaveError("Use a private LAN IPv4 address.")
    return str(ip)


def local_addresses():
    addresses = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = entry[4][0]
            if not ip.startswith("127."):
                try:
                    addresses.add(lan_address(ip))
                except SaveError:
                    pass
    except OSError:
        pass
    return sorted(addresses)


def game_files(root, exclude=()):
    if path_kind(root) != "directory":
        raise SaveError("Choose an existing game folder.")
    excluded = {Path(item).resolve() for item in exclude if item}
    result = {}
    empty_dirs = []

    def fail(error):
        raise error

    for folder, directories, files in os.walk(root, onerror=fail):
        base = Path(folder)
        if excluded:
            directories[:] = [name for name in directories if (base / name).resolve() not in excluded]
        if base != root and not directories and not files:
            empty_dirs.append(base.relative_to(root).as_posix())
        for name in directories:
            if path_kind(base / name) != "directory":
                raise SaveError("The game folder changed while scanning.")
        for name in files:
            file = base / name
            if path_kind(file) != "file":
                raise SaveError("The game folder changed while scanning.")
            result[file.relative_to(root).as_posix()] = file
        if len(result) + len(empty_dirs) > MAX_FILES:
            raise SaveError("The game folder contains too many files or directories.")
    validate_names([*result, *empty_dirs], "directory")
    return result, sorted(empty_dirs)


def safe_file(root, name):
    target = root / name
    for part in (target, *target.parents):
        if path_kind(part) != ("file" if part == target else "directory"):
            raise SaveError("The game files changed on the PC. Share the folder again.")
    return target


def manifest_for(root, title, executable, hint, exclude=()):
    sources, empty_dirs = game_files(root, exclude=exclude)
    executable = executable.replace("\\", "/")
    if executable and (executable not in sources or Path(executable).suffix.lower() != ".exe"):
        raise SaveError("Choose the Windows .exe inside the game folder.")
    if not title or len(title) > 160 or any(ord(char) < 32 for char in title):
        raise SaveError("Enter a game title of up to 160 characters.")
    files = {}
    for name in sorted(sources):
        target = safe_file(root, name)
        length = target.stat().st_size
        with target.open("rb") as stream:
            files[name] = digest_stream(stream, limit=length)
        if files[name][1] != length:
            raise SaveError("Game files changed while preparing the share.")
    game_id = fingerprint([socket.gethostname(), str(root).casefold()])[:32]
    manifest = {"id": game_id, "title": title, "exe": executable, "save": hint,
                "files": files, "empty_dirs": empty_dirs}
    if len(encode(manifest)) > MAX_MANIFEST:
        raise SaveError("This game’s file list is too large.")
    return manifest


def tls_server(directory, address):
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError as error:
        raise SaveError("The PC transfer host needs the cryptography Python package. "
                        "Run the SaveSync installer, or install it with: python -m pip install cryptography") from error
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SaveSync LAN")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=7))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]),
                                  critical=False).sign(key, hashes.SHA256()))
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        combined = Path(temp) / "identity.pem"
        combined.write_bytes(certificate.public_bytes(serialization.Encoding.PEM) +
                             key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(combined)
    return context, certificate.fingerprint(hashes.SHA256()).hex()


class LANServer(ThreadingHTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(15)
        return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), address

    def handle_error(self, request, address):
        # Disconnected clients and failed TLS handshakes are normal on a LAN.
        pass


class LANHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def send_body(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_body(encode(data), "application/json", status=status)

    def do_POST(self):
        server = self.server
        if server.stopped.is_set() or self.headers.get("Host") != server.peer:
            self.send_error(403)
            return
        if self.path.startswith("/v1/saves/"):
            auth = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(auth, b"Bearer " + server.secret.encode()) or server.library is None:
                self.send_error(403)
                return
            parts = self.path.split("/")
            if len(parts) != 5 or parts[:3] != ["", "v1", "saves"]:
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 512 * 1024**2:
                    raise SaveError("Invalid snapshot size.")
                raw_bytes = self.rfile.read(size)
                game_meta = json.loads(self.headers.get("X-SaveSync-Game", "{}"))
                with server.lock:
                    imported = server.library.import_snapshot(game_meta, parts[4], raw_bytes)
                    server.library.auto_apply_all()
                self.send_json({"imported": imported})
            except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
                self.send_json({"error": str(error)}, status=400)
            return
        if self.path not in ("/v1/pair/start", "/v1/pair/finish"):
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, OSError):
            self.send_json({"error": "Invalid pairing request."}, status=400)
            return

        now = time.monotonic()
        if self.path == "/v1/pair/start":
            client_nonce = payload.get("client_nonce")
            if not is_hex(client_nonce, 32):
                self.send_json({"error": "Invalid pairing request."}, status=400)
                return
            with server.lock:
                if server.pair_locked:
                    self.send_json({"error": "Too many incorrect codes. End the session on the PC and start a new one."}, status=429)
                    return
                server.pending_nonces = {
                    key: value for key, value in server.pending_nonces.items()
                    if now - value[1] < PAIR_NONCE_TTL
                }
                if len(server.pending_nonces) >= 32:
                    oldest = min(server.pending_nonces, key=lambda key: server.pending_nonces[key][1])
                    server.pending_nonces.pop(oldest, None)
                server_nonce = secrets.token_hex(16)
                server.pending_nonces[server_nonce] = (client_nonce, now)
            self.send_json({"server_nonce": server_nonce})
            return

        client_nonce = payload.get("client_nonce")
        server_nonce = payload.get("server_nonce")
        proof = payload.get("proof")
        if not (is_hex(client_nonce, 32) and is_hex(server_nonce, 32) and is_hex(proof, 64)):
            self.send_json({"error": "Invalid pairing request."}, status=400)
            return
        with server.lock:
            if server.pair_locked:
                self.send_json({"error": "Too many incorrect codes. End the session on the PC and start a new one."}, status=429)
                return
            pending = server.pending_nonces.pop(server_nonce, None)
            code_raw = server.pair_code_raw
            pin = server.pin
        if pending is None or now - pending[1] >= PAIR_NONCE_TTL or not hmac.compare_digest(pending[0], client_nonce):
            self.send_json({"error": "Pairing session expired. Try connecting again."}, status=403)
            return
        key = pair_key(code_raw, pin, server_nonce, client_nonce)
        expected = pair_proof(key, b"client")
        if not hmac.compare_digest(proof, expected):
            with server.lock:
                server.failed_pairs += 1
                locked = server.failed_pairs >= MAX_PAIR_FAILURES
                if locked:
                    server.pair_locked = True
            time.sleep(0.5)
            message = ("Too many incorrect codes. End the session on the PC and start a new one." if locked
                       else "Incorrect pairing code. Check the 6-character code on the PC.")
            self.send_json({"error": message}, status=429 if locked else 403)
            return
        with server.lock:
            server.failed_pairs = 0
            token = server.secret
        self.send_json({"proof": pair_proof(key, b"server"), "token": token})

    def do_GET(self):
        server = self.server
        auth = self.headers.get("Authorization", "").encode("utf-8")
        if (server.stopped.is_set() or self.headers.get("Host") != server.peer
                or not hmac.compare_digest(auth, b"Bearer " + server.secret.encode())):
            self.send_error(403)
            return

        with server.lock:
            offers = dict(server.offers)
        if self.path == "/v1/games":
            fields = ("id", "title", "exe", "save")
            games = [dict((field, item[field]) for field in fields) | {"bytes": item["bytes"]}
                     for _, item, _ in offers.values()]
            self.send_body(encode(games), "application/json")
            return

        if self.path == "/v1/saves":
            if server.library is None:
                self.send_body(encode([]), "application/json")
                return
            with server.lock:
                entries = server.library.list_store_snapshots()
            self.send_body(encode(entries), "application/json")
            return

        parts = self.path.split("/")
        if len(parts) == 5 and parts[:3] == ["", "v1", "saves"] and server.library is not None:
            try:
                snap_file = server.library.snapshot_path(parts[3], parts[4])
                data = snap_file.read_bytes()
            except (SaveError, OSError):
                self.send_error(404)
                return
            self.send_body(data, "application/zip")
            return

        if len(parts) not in (4, 5) or parts[:3] != ["", "v1", "games"] or parts[3] not in offers:
            self.send_error(404)
            return

        root, manifest, names = offers[parts[3]]
        if len(parts) == 4:
            data = dict(manifest)
            del data["bytes"]
            self.send_body(encode(data), "application/json")
            return

        index = parts[4]
        if not index.isascii() or not index.isdecimal() or len(index) > 6 or int(index) >= len(names):
            self.send_error(404)
            return
        name = names[int(index)]
        _, size = manifest["files"][name]
        try:
            path = safe_file(root, name)
            stream = path.open("rb")
        except (OSError, SaveError):
            self.send_error(409, "Game files changed. Share the folder again.")
            return
        with stream:
            if os.fstat(stream.fileno()).st_size != size:
                self.send_error(409, "Game files changed. Share the folder again.")
                return
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            remaining = size
            while remaining and not server.stopped.is_set():
                chunk = stream.read(min(1024**2, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
            if remaining:
                self.close_connection = True


class DiscoveryResponder:
    def __init__(self, address, server_port, listen_port=PORT):
        self.stopped = threading.Event()
        self.payload = encode({
            "app": "SaveSync",
            "v": 2,
            "name": socket.gethostname()[:120],
            "address": address,
            "port": server_port,
        })
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("", listen_port))
            self.sock.settimeout(0.5)
        except OSError:
            self.sock.close()
            self.sock = None
            return
        threading.Thread(target=self.serve, name="SaveSync LAN discovery", daemon=True).start()

    def serve(self):
        while not self.stopped.is_set() and self.sock is not None:
            try:
                data, sender = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == DISCOVER_REQUEST:
                try:
                    self.sock.sendto(self.payload, sender)
                except OSError:
                    pass

    def stop(self):
        self.stopped.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def discover_hosts(port=PORT, timeout=1.2):
    hosts = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        sock.settimeout(0.25)
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                sock.sendto(DISCOVER_REQUEST, (target, port))
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.25, remaining))
            try:
                data, sender = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data)
                if not isinstance(info, dict) or info.get("app") != "SaveSync":
                    continue
                address = lan_address(info.get("address") or sender[0])
                host_port = int(info.get("port", port))
                if not 1 <= host_port <= 65535:
                    continue
                name = str(info.get("name") or address)[:120]
                hosts[(address, host_port)] = {"name": name, "address": address, "port": host_port}
            except (ValueError, TypeError, SaveError):
                continue
    finally:
        sock.close()
    return sorted(hosts.values(), key=lambda item: (item["name"].casefold(), item["address"], item["port"]))


class Host:
    def __init__(self, directory, library=None):
        self.directory = directory
        self.library = library
        self.server = None
        self.discovery = None

    def status(self):
        server = self.server
        if server is None:
            return {"invite": "", "code": "", "address": "", "port": 0, "locked": False,
                    "games": [], "addresses": local_addresses()}
        with server.lock:
            return {"invite": server.invite, "code": server.pair_code, "address": server.address,
                    "port": server.server_port, "locked": server.pair_locked, "addresses": [],
                    "games": [{"id": item["id"], "title": item["title"]}
                              for _, item, _ in server.offers.values()]}

    def start(self, address, port=PORT, discovery_port=None):
        if self.server:
            raise SaveError("Stop the current LAN share first.")
        address = lan_address(address)
        context, pin = tls_server(self.directory, address)
        try:
            server = LANServer((address, port), LANHandler)
        except OSError as error:
            raise SaveError(f"Cannot listen on {address}:{port}. Check the address and port.") from error
        code = generate_pair_code()
        server.context = context
        server.library = self.library
        server.address = address
        server.peer = f"{address}:{server.server_port}"
        server.pin = pin
        server.secret = secrets.token_hex(24)
        server.pair_code = code
        server.pair_code_raw = code.replace("-", "")
        server.failed_pairs = 0
        server.pair_locked = False
        server.pending_nonces = {}
        server.invite = f"{server.peer}#{pin}#{server.secret}"
        server.offers = {}
        server.lock = threading.Lock()
        server.stopped = threading.Event()
        self.server = server
        self.discovery = DiscoveryResponder(
            address, server.server_port,
            PORT if discovery_port is None and port == PORT else (discovery_port or server.server_port),
        )
        threading.Thread(target=server.serve_forever, name="SaveSync LAN host", daemon=True).start()

    def add(self, root, manifest):
        server = self.server
        if server is None:
            raise SaveError("Start sharing on the LAN first.")
        size = sum(entry[1] for entry in manifest["files"].values())
        with server.lock:
            if len(server.offers) >= 100 and manifest["id"] not in server.offers:
                raise SaveError("This session can share up to 100 games.")
            server.offers[manifest["id"]] = (root, dict(manifest, bytes=size), sorted(manifest["files"]))

    def remove(self, game_id):
        if self.server:
            with self.server.lock:
                self.server.offers.pop(checked_id(game_id), None)

    def stop(self):
        if self.discovery:
            discovery, self.discovery = self.discovery, None
            discovery.stop()
        if self.server:
            server, self.server = self.server, None
            server.stopped.set()
            server.shutdown()
            server.server_close()


def pair(address, code, port=PORT):
    address = lan_address(address.strip())
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SaveError("Invalid PC transfer port.")
    code_raw = normalize_pair_code(code)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(address, port, timeout=15, context=context)

    def post_json(path, payload):
        body = encode(payload)
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(8192)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if response.status != 200:
            message = data.get("error") if isinstance(data, dict) else ""
            raise SaveError(message or f"Could not pair with the PC (HTTP {response.status}).")
        return data

    try:
        with closing(connection):
            connection.connect()
            pin = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            client_nonce = secrets.token_hex(16)
            started = post_json("/v1/pair/start", {"client_nonce": client_nonce})
            server_nonce = started.get("server_nonce")
            if not is_hex(server_nonce, 32):
                raise SaveError("The PC sent an invalid pairing challenge.")
            key = pair_key(code_raw, pin, server_nonce, client_nonce)
            finished = post_json("/v1/pair/finish", {
                "client_nonce": client_nonce,
                "server_nonce": server_nonce,
                "proof": pair_proof(key, b"client"),
            })
            server_proof = finished.get("proof")
            token = finished.get("token")
            if (not is_hex(server_proof, 64)
                    or not hmac.compare_digest(server_proof, pair_proof(key, b"server"))
                    or not is_hex(token, 48)):
                raise SaveError("The PC could not verify the pairing code.")
            return f"{address}:{port}#{pin}#{token}"
    except SaveError:
        raise
    except (OSError, http.client.HTTPException) as error:
        raise SaveError(f"Could not reach the PC at {address}:{port}. Make sure sharing is started and both devices are on the same network.") from error


class LANClient(http.client.HTTPSConnection):
    def __init__(self, invite):
        try:
            endpoint, self.pin, self.secret = invite.strip().split("#")
            address, port = endpoint.split(":")
            address, port = lan_address(address), int(port)
            if not 1 <= port <= 65535 or not HASH.fullmatch(self.pin):
                raise ValueError
            if len(self.secret) != 48 or any(char not in "abcdef0123456789" for char in self.secret):
                raise ValueError
        except (ValueError, SaveError) as error:
            raise SaveError("Enter the full connection code displayed by SaveSync on the PC.") from error
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(address, port, timeout=20, context=context)

    def connect(self):
        super().connect()
        actual = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(self.pin, actual):
            self.close()
            raise SaveError("The PC identity did not match the connection code. Copy a fresh code from the PC.")

    def get(self, path):
        self.request("GET", path, headers={"Authorization": "Bearer " + self.secret})
        response = self.getresponse()
        if response.status != 200:
            status = response.status
            response.close()
            self.close()
            raise SaveError(f"The PC could not provide the requested game data (HTTP {status}).")
        return response

    def get_json(self, path):
        with self.get(path) as response:
            data = response.read(MAX_MANIFEST + 1)
        if len(data) > MAX_MANIFEST:
            raise SaveError("The PC sent a file list that was too large.")
        return json.loads(data)

    def post_snapshot(self, game_meta, snap_id, raw_bytes):
        headers = {
            "Authorization": "Bearer " + self.secret,
            "Content-Type": "application/zip",
            "X-SaveSync-Game": encode(game_meta).decode("ascii"),
        }
        self.request("POST", f"/v1/saves/{game_meta['id']}/{snap_id}", body=raw_bytes, headers=headers)
        response = self.getresponse()
        body = response.read(8192)
        if response.status != 200:
            self.close()
            raise SaveError(f"Could not send save snapshot to the PC (HTTP {response.status}).")
        return json.loads(body) if body else {}


def sync_saves_lan(invite, library):
    """Two-way save snapshot sync between the Steam Deck and the PC over LAN."""
    library.auto_publish_all()
    local_before = {entry["game"]["id"]: set(entry["snapshots"]) for entry in library.list_store_snapshots()}
    pulled = 0
    pushed = 0
    with closing(LANClient(invite)) as client:
        remote_list = client.get_json("/v1/saves")
        if not isinstance(remote_list, list):
            raise SaveError("The PC returned an invalid save catalog.")
        remote_map = {}
        for item in remote_list:
            if not isinstance(item, dict) or not isinstance(item.get("game"), dict):
                continue
            game_meta = item["game"]
            gid = checked_id(game_meta.get("id", ""))
            snaps = [checked_id(s) for s in item.get("snapshots", []) if isinstance(s, str)]
            remote_map[gid] = set(snaps)
            have = local_before.get(gid, set())
            for snap_id in snaps:
                if snap_id in have:
                    continue
                with client.get(f"/v1/saves/{gid}/{snap_id}") as response:
                    raw = response.read(512 * 1024**2 + 1)
                if len(raw) > 512 * 1024**2:
                    raise SaveError("A save snapshot exceeded the transfer limit.")
                if library.import_snapshot(game_meta, snap_id, raw):
                    pulled += 1

        local_after = library.list_store_snapshots()
        for entry in local_after:
            game_meta = entry["game"]
            gid = game_meta["id"]
            remote_have = remote_map.get(gid, set())
            for snap_id in entry["snapshots"]:
                if snap_id in remote_have:
                    continue
                raw = library.snapshot_path(gid, snap_id).read_bytes()
                client.post_snapshot(game_meta, snap_id, raw)
                pushed += 1
    return {"pulled": pulled, "pushed": pushed}


def catalog(invite):
    with closing(LANClient(invite)) as client:
        games = client.get_json("/v1/games")
    if not isinstance(games, list) or len(games) > 100:
        raise SaveError("The PC returned an invalid game catalog.")
    for item in games:
        checked_id(item["id"])
        if not isinstance(item["title"], str) or type(item["bytes"]) is not int or item["bytes"] < 0:
            raise SaveError("The PC returned an invalid game catalog.")
    return games


def check_manifest(manifest, game_id):
    files = manifest["files"]
    directories = manifest["empty_dirs"]
    title = manifest["title"]
    executable = manifest["exe"]
    if (manifest["id"] != game_id or not isinstance(title, str) or not 0 < len(title) <= 160
            or any(ord(char) < 32 for char in title) or not isinstance(files, dict) or not isinstance(directories, list)
            or len(files) + len(directories) > MAX_FILES
            or any(not isinstance(directory, str) for directory in directories)
            or not files.keys().isdisjoint(directories) or len(set(directories)) != len(directories)
            or (executable and (executable not in files or Path(executable).suffix.lower() != ".exe"))):
        raise SaveError("The PC returned an invalid game manifest.")
    validate_names([*files, *directories], "directory")
    for item in files.values():
        if (not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str)
                or not HASH.fullmatch(item[0]) or type(item[1]) is not int or item[1] < 0):
            raise SaveError("The PC returned invalid file checksums or sizes.")
    return sum(item[1] for item in files.values())


def download(invite, game_id, parent, report, prepare_install=None):
    checked_id(game_id)
    with closing(LANClient(invite)) as client:
        base = "/v1/games/" + game_id
        manifest = client.get_json(base)
        total = check_manifest(manifest, game_id)
        stem = "".join(char if char.isalnum() or char in " -_" else "_" for char in manifest["title"])
        target = parent / (stem[:70].strip() + "-" + game_id[:8])
        if path_kind(target) is not None:
            raise SaveError(f"A game is already installed at {target}. Choose another install directory.")
        if shutil.disk_usage(parent).free < total:
            raise SaveError("The selected install drive does not have enough free space.")
        done = 0
        with tempfile.TemporaryDirectory(prefix=".savesync-download-", dir=parent) as temp:
            stage = Path(temp) / "game"
            stage.mkdir()
            report(done, total)
            for index, (name, expected) in enumerate(sorted(manifest["files"].items())):
                file = stage / name
                file.parent.mkdir(parents=True, exist_ok=True)
                with client.get(f"{base}/{index}") as response, file.open("xb") as output:
                    if response.getheader("Content-Length") != str(expected[1]):
                        raise SaveError(f"File size changed on the PC: {name}")
                    digest = hashlib.sha256()
                    remaining = expected[1]
                    while remaining:
                        chunk = response.read(min(1024**2, remaining))
                        if not chunk:
                            raise SaveError(f"Download ended early: {name}")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                        done += len(chunk)
                        report(done, total)
                    if digest.hexdigest() != expected[0]:
                        raise SaveError(f"Checksum did not match: {name}. Share the game again on the PC.")
            for directory in manifest["empty_dirs"]:
                (stage / directory).mkdir(parents=True)
            if path_kind(target) is not None:
                raise SaveError("The install directory appeared during download. Choose another directory.")
            if prepare_install:
                prepare_install(manifest, target)
            stage.rename(target)
    return manifest, target


def install_parent(value, library):
    path = clean_path(value)
    state = library.state()
    if (path == Path(path.anchor) or overlap(path, library.directory)
            or state["shared"] and overlap(path, Path(state["shared"]))
            or any(path.is_relative_to(Path(mapping["path"])) for mapping in state["maps"].values())
            or any(char in str(path) for char in '"\r\n')):
        raise SaveError("Choose a separate install folder, such as ~/Games.")
    path.mkdir(parents=True, exist_ok=True)
    return path
