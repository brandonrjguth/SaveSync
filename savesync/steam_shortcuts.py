"""Add Non-Steam game shortcuts directly to Steam on SteamOS / Linux."""

import os
import re
import shlex
import struct
import tempfile
import zlib
from pathlib import Path


def decky_bridge_installed():
    return (Path.home() / "homebrew/plugins/SaveSync Bridge").is_dir()


def steam_root():
    for candidate in (
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ):
        if (candidate / "userdata").is_dir():
            return candidate
    return None


def shortcut_appid(exe_quoted, title):
    return (zlib.crc32((exe_quoted + title).encode("utf-8")) | 0x80000000) & 0xFFFFFFFF


def _read_cstring(data, offset):
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def parse_vdf(data):
    """Parse Steam binary VDF bytes into nested dicts."""
    if not data:
        return {}

    def read_map(offset):
        result = {}
        while offset < len(data):
            kind = data[offset]
            offset += 1
            if kind == 0x08:
                return result, offset
            key, offset = _read_cstring(data, offset)
            if kind == 0x00:
                value, offset = read_map(offset)
            elif kind == 0x01:
                value, offset = _read_cstring(data, offset)
            elif kind == 0x02:
                value = struct.unpack_from("<I", data, offset)[0]
                offset += 4
            else:
                raise ValueError(f"Unsupported binary VDF field type: {kind:#x}")
            result[key] = value
        return result, offset

    parsed, _ = read_map(0)
    return parsed


def dump_vdf(mapping):
    """Serialize nested dicts into Steam binary VDF bytes."""
    chunks = []

    def write_map(obj):
        for key, value in obj.items():
            encoded_key = str(key).encode("utf-8") + b"\x00"
            if isinstance(value, dict):
                chunks.append(b"\x00" + encoded_key)
                write_map(value)
                chunks.append(b"\x08")
            elif isinstance(value, int):
                chunks.append(b"\x02" + encoded_key + struct.pack("<I", value & 0xFFFFFFFF))
            else:
                chunks.append(b"\x01" + encoded_key + str(value).encode("utf-8") + b"\x00")

    write_map(mapping)
    chunks.append(b"\x08")
    return b"".join(chunks)


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _set_proton_experimental(root, appid):
    config_path = root / "config/config.vdf"
    if not config_path.is_file():
        return
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if f'"{appid}"' in text:
        return
    match = re.search(r'("CompatToolMapping"\s*\{)', text)
    if not match:
        return
    entry = (
        f'\n\t\t\t\t\t"{appid}"\n'
        '\t\t\t\t\t{\n'
        '\t\t\t\t\t\t"name"\t\t"proton_experimental"\n'
        '\t\t\t\t\t\t"config"\t\t""\n'
        '\t\t\t\t\t\t"priority"\t\t"250"\n'
        '\t\t\t\t\t}'
    )
    updated = text[:match.end()] + entry + text[match.end():]
    try:
        _atomic_write(config_path, updated.encode("utf-8"))
    except OSError:
        pass


def add_steam_shortcut(title, executable, prefix):
    """Add a Non-Steam shortcut directly to every Steam user's shortcuts.vdf.

    Returns the Steam shortcut appid on success, or None if no Steam userdata folder was found.
    """
    root = steam_root()
    if root is None:
        return None
    userdata = root / "userdata"
    user_dirs = [d for d in sorted(userdata.iterdir()) if d.is_dir() and d.name.isdigit() and d.name != "0"]
    if not user_dirs:
        return None

    exe_path = Path(executable)
    exe_quoted = f'"{exe_path}"'
    start_quoted = f'"{exe_path.parent}"'
    launch_options = f"STEAM_COMPAT_DATA_PATH={shlex.quote(str(prefix))} %command%"
    appid = shortcut_appid(exe_quoted, title)

    wrote_any = False
    for user_dir in user_dirs:
        vdf_path = user_dir / "config/shortcuts.vdf"
        try:
            data = parse_vdf(vdf_path.read_bytes()) if vdf_path.is_file() else {}
        except (OSError, ValueError, struct.error):
            continue
        shortcuts = data.get("shortcuts")
        if not isinstance(shortcuts, dict):
            shortcuts = {}
            data = {"shortcuts": shortcuts}

        # Check if already present by Exe path
        existing_key = None
        for key, item in shortcuts.items():
            if isinstance(item, dict) and str(item.get("Exe", "")).strip('"') == str(exe_path):
                existing_key = key
                break

        if existing_key is None:
            numeric = [int(k) for k in shortcuts if str(k).isdigit()]
            next_index = str(max(numeric) + 1) if numeric else "0"
            shortcuts[next_index] = {
                "appid": appid,
                "AppName": title,
                "Exe": exe_quoted,
                "StartDir": start_quoted,
                "icon": "",
                "ShortcutPath": "",
                "LaunchOptions": launch_options,
                "IsHidden": 0,
                "AllowDesktopConfig": 1,
                "AllowOverlay": 1,
                "OpenVR": 0,
                "Devkit": 0,
                "DevkitGameID": "",
                "DevkitOverrideAppID": 0,
                "LastPlayTime": 0,
                "FlatpakAppID": "",
                "tags": {},
            }
            _atomic_write(vdf_path, dump_vdf(data))
            wrote_any = True
        else:
            existing_appid = shortcuts[existing_key].get("appid")
            if isinstance(existing_appid, int) and existing_appid:
                appid = existing_appid
            wrote_any = True

    if wrote_any:
        _set_proton_experimental(root, appid)
        return appid
    return None
