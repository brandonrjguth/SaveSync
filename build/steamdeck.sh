#!/usr/bin/env bash
# Build the standalone Steam Deck / Linux x86_64 SaveSync installer archive.
# Run on Ubuntu 22.04 (glibc 2.35) or similar for broad SteamOS compatibility.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m pip install --disable-pip-version-check --upgrade cryptography pyinstaller

python3 -m PyInstaller --noconfirm --clean --onedir --name SaveSync \
    --add-data "savesync/ui:savesync/ui" \
    SaveSync.pyw

"$ROOT/dist/SaveSync/SaveSync" --help >/dev/null

STAGE="$ROOT/dist/SaveSync-SteamDeck"
rm -rf "$STAGE" "$ROOT/dist/SaveSync-SteamDeck.tar.gz"
mkdir -p "$STAGE/assets"

cp -a "$ROOT/dist/SaveSync" "$STAGE/app"
cp -f "$ROOT/assets/icon.png" "$STAGE/assets/icon.png"
cp -f "$ROOT/installer/steamdeck-install.sh" "$STAGE/install.sh"
cp -f "$ROOT/installer/steamdeck-uninstall.sh" "$STAGE/uninstall.sh"

cat > "$STAGE/Install SaveSync.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Install SaveSync
Comment=Install SaveSync and add it to your Desktop and Application Menu
Exec=sh -c 'exec "$(dirname -- "$1")/install.sh"' _ %k
Icon=system-software-install
Terminal=false
Categories=Utility;
EOF

chmod +x "$STAGE/install.sh" "$STAGE/uninstall.sh" "$STAGE/Install SaveSync.desktop" "$STAGE/app/SaveSync"

tar -C "$ROOT/dist" -czf "$ROOT/dist/SaveSync-SteamDeck.tar.gz" SaveSync-SteamDeck
echo "Steam Deck installer written to $ROOT/dist/SaveSync-SteamDeck.tar.gz"
