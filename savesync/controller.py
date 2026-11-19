"""Read Steam Input from the host, where Flatpak browsers may not see it."""

import os
import sys
import time


def steam_session():
    return sys.platform.startswith("linux") and (
        bool(os.environ.get("SteamGameId"))
        or bool(os.environ.get("SteamAppId"))
        or bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY"))
        or "gamescope" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    )


def open_controller():
    if not steam_session():
        return None
    try:
        import evdev
    except ImportError:
        return None
    return Controller(evdev)


class Controller:
    def __init__(self, evdev):
        self.evdev = evdev
        self.codes = evdev.ecodes
        codes = self.codes
        self.buttons = {
            codes.BTN_SOUTH: "A", codes.BTN_EAST: "B",
            codes.BTN_DPAD_UP: "ArrowUp", codes.BTN_DPAD_DOWN: "ArrowDown",
            codes.BTN_DPAD_LEFT: "ArrowLeft", codes.BTN_DPAD_RIGHT: "ArrowRight",
        }
        self.hats = {
            codes.ABS_HAT0X: ("ArrowLeft", "ArrowRight"),
            codes.ABS_HAT0Y: ("ArrowUp", "ArrowDown"),
        }
        self.devices = {}
        self.scan_at = 0

    def scan(self):
        for path in self.evdev.list_devices():
            if path in self.devices:
                continue
            try:
                device = self.evdev.InputDevice(path)
            except OSError:
                continue
            try:
                caps = device.capabilities(absinfo=False)
                keys = caps.get(self.codes.EV_KEY, [])
                axes = set(caps.get(self.codes.EV_ABS, []))
                if self.codes.BTN_SOUTH in keys and self.codes.BTN_EAST in keys:
                    self.devices[path] = (device, axes.intersection(self.hats))
                    continue
            except OSError:
                pass
            device.close()

    @staticmethod
    def steam_virtual(device):
        return (device.info.vendor, device.info.product) == (0x28DE, 0x11FF)

    def poll(self):
        now = time.monotonic()
        if now >= self.scan_at:
            self.scan()
            self.scan_at = now + 1
        prefer_steam = any(self.steam_virtual(device) for device, _ in self.devices.values())
        pressed, held = set(), set()
        recent = time.time() - 0.25
        for path, (device, axes) in list(self.devices.items()):
            active = not prefer_steam or self.steam_virtual(device)
            try:
                for _ in range(4):
                    try:
                        for event in device.read():
                            if active and event.timestamp() >= recent:
                                if event.type == self.codes.EV_KEY and event.value == 1 and event.code in self.buttons:
                                    pressed.add(self.buttons[event.code])
                                if event.type == self.codes.EV_ABS and event.code in self.hats and event.value:
                                    pressed.add(self.hats[event.code][event.value > 0])
                    except BlockingIOError:
                        break
                if active:
                    held.update(self.buttons[key] for key in device.active_keys() if key in self.buttons)
                    for axis in axes:
                        value = device.absinfo(axis).value
                        if value:
                            held.add(self.hats[axis][value > 0])
            except OSError:
                device.close()
                del self.devices[path]
                self.scan_at = 0
        return {"connected": bool(self.devices), "pressed": sorted(pressed), "held": sorted(held)}

    def close(self):
        for device, _ in self.devices.values():
            device.close()
        self.devices.clear()
