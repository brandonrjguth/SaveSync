#!/usr/bin/env bash
set -euo pipefail

rm -rf "$HOME/.local/share/SaveSync"
rm -f "$HOME/.local/share/applications/savesync.desktop"
rm -f "$HOME/Desktop/SaveSync.desktop"
rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/savesync.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if command -v kdialog >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    kdialog --title "SaveSync Uninstalled" --msgbox "SaveSync has been removed. Your saved configuration in ~/.local/share/savesync was kept."
else
    printf 'SaveSync removed. Configuration in ~/.local/share/savesync was kept.\n'
fi
