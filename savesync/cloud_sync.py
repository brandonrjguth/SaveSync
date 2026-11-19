"""Cloud pairing and explicit save exchange for the local game library."""

import hashlib
import http.client
import json
import tempfile
from contextlib import contextmanager
from graphlib import TopologicalSorter
from pathlib import Path
from urllib.parse import urlsplit

from .cloud_server import MAX_ARCHIVE, TOKEN_LENGTH
from .discovery import tls_context
from .library import checked_id
from .snapshots import SaveError, encode, fingerprint, history, write_json


def server_url(value):
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.port == 0):
        raise SaveError("Enter the SaveSync server's HTTPS address, such as https://saves.example.com.")
    return "https://" + parsed.netloc


class CloudClient:
    def __init__(self, url, token=""):
        address = urlsplit(server_url(url))
        self.host = address.hostname
        self.port = address.port or 443
        self.token = token

    @contextmanager
    def request(self, method, endpoint, body=None, headers=None, allow_missing=False):
        headers = dict(headers or {})
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=20, context=tls_context())
        try:
            connection.request(method, endpoint, body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status == 404 and allow_missing:
                    yield None
                elif response.status != 200:
                    try:
                        message = json.loads(response.read(4096)).get("error")
                    except (ValueError, AttributeError):
                        message = None
                    raise SaveError(message or f"The cloud server returned HTTP {response.status}.")
                else:
                    yield response
        except (OSError, http.client.HTTPException) as error:
            raise SaveError(f"The cloud server could not be reached securely: {error}") from error
        finally:
            connection.close()

    def json(self, method, endpoint, data=None, **options):
        headers = {"Content-Type": "application/json"} if data is not None else {}
        with self.request(method, endpoint, encode(data) if data is not None else None, headers, **options) as response:
            if response is None:
                return None
            body = response.read(32 * 1024**2 + 1)
            if len(body) > 32 * 1024**2:
                raise SaveError("The cloud history exceeded the response limit.")
            return json.loads(body)

    def stored_game(self, game_id):
        return self.json("GET", f"/v1/games/{checked_id(game_id)}", allow_missing=True)

    def download(self, game_id, snapshot_id, destination):
        endpoint = f"/v1/games/{checked_id(game_id)}/{checked_id(snapshot_id)}"
        with self.request("GET", endpoint) as response:
            size = int(response.getheader("Content-Length", "0"))
            if not 0 < size <= MAX_ARCHIVE:
                raise SaveError("Invalid cloud archive size.")
            digest = hashlib.sha256()
            with destination.open("xb") as archive:
                while size:
                    chunk = response.read(min(size, 1024**2))
                    if not chunk:
                        raise SaveError("The save download ended early.")
                    archive.write(chunk)
                    digest.update(chunk)
                    size -= len(chunk)
            if digest.hexdigest() != response.getheader("X-SaveSync-SHA256"):
                raise SaveError("The save download did not match its checksum.")

    def upload(self, game_id, snapshot_id, archive_path):
        endpoint = f"/v1/games/{checked_id(game_id)}/{checked_id(snapshot_id)}"
        with archive_path.open("rb") as archive:
            headers = {"Content-Type": "application/zip", "Content-Length": str(archive_path.stat().st_size)}
            with self.request("PUT", endpoint, archive, headers):
                pass


class CloudSync:
    def __init__(self, library):
        self.library = library
        self.config = library.directory / "cloud.json"

    def settings(self):
        try:
            return json.loads(self.config.read_bytes())
        except FileNotFoundError:
            return {}

    def action(self, data):
        action = data["action"]
        stored = self.settings()
        if action == "pair":
            url = server_url(data["url"])
            name = self.library.state()["device"]
            paired = CloudClient(url).json("POST", "/v1/pair", {"code": data["code"].strip(), "name": name})
            key = checked_id(paired["id"])
            if not isinstance(paired["token"], str) or len(paired["token"]) != TOKEN_LENGTH:
                raise SaveError("The cloud server returned an invalid pairing credential.")
            write_json(self.config, {"url": url, "id": key, "token": paired["token"]})
            return {"message": "This device is paired to the cloud."}
        if action == "disconnect":
            if stored:
                CloudClient(stored["url"], stored["token"]).json("POST", "/v1/disconnect")
            self.config.unlink(missing_ok=True)
            return {"message": "Cloud connection removed from this device."}
        if action == "invite" and stored:
            return CloudClient(stored["url"], stored["token"]).json("POST", "/v1/invite")
        raise SaveError("Pair this device with the cloud server first.")

    def refresh_index(self):
        stored = self.settings()
        if not stored:
            return {"paired": False}
        try:
            warning = ""
            client = CloudClient(stored["url"], stored["token"])
            entries = client.json("GET", "/v1/games")
            for entry in entries:
                try:
                    self.library.register_snapshot_game(entry)
                except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
                    warning = str(error)
            return {"paired": True, "url": stored["url"], "online": True, "warning": warning}
        except (SaveError, OSError, ValueError, KeyError, TypeError) as error:
            return {"paired": True, "url": stored["url"], "online": False, "warning": str(error)}

    def review(self, game_id):
        review = self.library.review(game_id)
        stored = self.settings()
        if not stored:
            return review
        try:
            cloud = CloudClient(stored["url"], stored["token"]).stored_game(game_id)
        except (SaveError, OSError, ValueError) as error:
            return dict(review, cloud={"error": str(error)})
        versions = cloud["versions"] if cloud else []
        combined = {item["id"]: dict(item, uploaded=False) for item in review["versions"]}
        for version in versions:
            combined[version["id"]] = dict(version, uploaded=True)
        this_device = self.library.state()["device"]
        for item in combined.values():
            item["this_device"] = (item["device_id"] == stored["id"] if item.get("device_id")
                                   else item["device"] == this_device)
        ordered = sorted(combined.values(),
                         key=lambda item: (item["created"], item.get("received", ""), item["id"]), reverse=True)
        cloud_heads = [item["id"] for item in ordered if item["uploaded"] and item["head"]]
        local_heads = [item["id"] for item in review["versions"] if item["head"]]
        return dict(review, versions=ordered, cloud={
            "token": cloud_revision(versions), "latest": cloud_heads[0] if len(cloud_heads) == 1 else None,
            "other": next((item["id"] for item in ordered if item["uploaded"] and not item["this_device"]), None),
            "current": len(cloud_heads) == 1 and cloud_heads == local_heads and review["status"] == "current",
            "conflict": len(cloud_heads) > 1 or review["status"] == "conflict",
        })

    def sync(self, data, *, automatic=False):
        stored = self.settings()
        if not stored:
            return self.library.sync(data)
        game_id = checked_id(data["id"])
        before = self.library.review(game_id)
        if before["token"] != data.get("token"):
            raise SaveError("The local saves changed. Check again before syncing.")
        if self.library.save_detection(game_id).is_file() or before["status"] == "interrupted":
            raise SaveError("Finish save detection or recovery before syncing.")
        client = CloudClient(stored["url"], stored["token"])
        remote = client.stored_game(game_id)
        versions = remote["versions"] if remote else []
        if data.get("cloud_token") != cloud_revision(versions):
            raise SaveError("Cloud history changed. Check again and choose which save to use.")
        remote_ids = {item["id"] for item in versions}
        local_ids = {item["id"] for item in before["versions"]}
        choice = data.get("choice")
        if choice != "local" and choice not in (remote_ids | local_ids):
            raise SaveError("Choose a local or cloud save version.")
        try:
            if remote_ids - local_ids:
                with tempfile.TemporaryDirectory(dir=self.library.directory) as temp:
                    for version in versions:
                        snap_id = version["id"]
                        if snap_id in local_ids:
                            continue
                        archive = Path(temp) / (checked_id(snap_id) + ".zip")
                        client.download(game_id, snap_id, archive)
                        self.library.import_snapshot_file(remote["game"], archive)
                        archive.unlink()
            review = self.library.review(game_id)
            if before["content_token"] != review["content_token"]:
                raise SaveError("Local saves changed while cloud history was downloading. Check again before syncing.")
            if automatic and review["status"] == "conflict":
                raise SaveError("Save conflict. Choose a version in SaveSync.")
            if automatic and choice != "local" and review["status"] != "download":
                return "Local progress kept."
            if automatic and choice == "local" and review["status"] in ("empty", "download"):
                return "No local changes to upload."
            if choice == "local":
                if not review["count"]:
                    raise SaveError("This device has no save files to upload.")
                if review["status"] == "current":
                    choice = "auto"
            self.library.sync({"id": game_id, "token": review["token"], "choice": choice}, device_id=stored["id"])
            folder = self.library.store(self.library.state()) / game_id
            entries, _ = history(folder, game_id)
            graph = TopologicalSorter({key: info["parents"] for key, info in entries.items()})
            for snapshot in graph.static_order():
                if snapshot not in remote_ids:
                    client.upload(game_id, snapshot, folder / (snapshot + ".zip"))
            return ("Saved to the cloud." if data["choice"] == "local"
                    else "Applied the selected save and updated cloud history.")
        except SaveError as error:
            raise SaveError(str(error) + " Local snapshots are kept for retry.") from error


def cloud_revision(versions):
    return fingerprint(sorted(item["id"] for item in versions))
