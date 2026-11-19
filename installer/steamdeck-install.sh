#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$SCRIPT_DIR/app"
ICON_SOURCE="$SCRIPT_DIR/assets/icon.png"

INSTALL_ROOT="$HOME/.local/share/SaveSync"
APP_DIR="$INSTALL_ROOT/app"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="$HOME/Desktop"

notify() {
    local message="$1"
    if command -v kdialog >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        kdialog --title "SaveSync Installer" --msgbox "$message"
    else
        printf '%s\n' "$message"
    fi
}

fail() {
    local message="$1"
    if command -v kdialog >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        kdialog --title "SaveSync Installer" --error "$message"
    else
        printf 'Error: %s\n' "$message" >&2
    fi
    exit 1
}

if [ ! -x "$APP_SOURCE/SaveSync" ]; then
    fail "Could not find the bundled SaveSync binary in $APP_SOURCE. Extract the entire archive before running the installer."
fi

mkdir -p "$INSTALL_ROOT" "$ICON_DIR" "$APPS_DIR"

# Replace only the app binary folder so user settings in ~/.local/share/savesync stay intact.
rm -rf "$APP_DIR"
cp -a "$APP_SOURCE" "$APP_DIR"
chmod +x "$APP_DIR/SaveSync"

if [ -f "$ICON_SOURCE" ]; then
    cp -f "$ICON_SOURCE" "$ICON_DIR/savesync.png"
    cp -f "$ICON_SOURCE" "$INSTALL_ROOT/icon.png"
fi

if [ -f "$SCRIPT_DIR/uninstall.sh" ]; then
    cp -f "$SCRIPT_DIR/uninstall.sh" "$INSTALL_ROOT/uninstall.sh"
    chmod +x "$INSTALL_ROOT/uninstall.sh"
fi

cat > "$APPS_DIR/savesync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SaveSync
Comment=Sync PC game saves and transfer games between Windows and Steam Deck
Exec=$APP_DIR/SaveSync
Path=$APP_DIR
Icon=$INSTALL_ROOT/icon.png
Terminal=false
Categories=Game;Utility;
StartupNotify=true
EOF
chmod +x "$APPS_DIR/savesync.desktop"

if [ -d "$DESKTOP_DIR" ]; then
    cp -f "$APPS_DIR/savesync.desktop" "$DESKTOP_DIR/SaveSync.desktop"
    chmod +x "$DESKTOP_DIR/SaveSync.desktop"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 >/dev/null 2>&1 || true
fi

if command -v kdialog >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    if kdialog --title "SaveSync Installed" --yesno "SaveSync has been installed!\n\nYou can launch it from your Desktop or Application Menu.\n\nTip: To open SaveSync in Gaming Mode, right-click SaveSync in the Application Menu and choose \"Add to Steam\".\n\nLaunch SaveSync now?"; then
        nohup "$APP_DIR/SaveSync" >/dev/null 2>&1 &
    fi
else
    printf 'SaveSync installed to %s\n' "$APP_DIR"
    printf 'Desktop & Application Menu shortcuts created.\n'
    printf 'Tip: Right-click SaveSync in the Application Menu -> Add to Steam for Gaming Mode.\n'
fi
