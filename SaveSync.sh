#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    exec python3 -m savesync --window "$@"
fi
message="SaveSync needs Python 3.10 or newer. The python3 command was missing or too old."
if command -v kdialog >/dev/null 2>&1; then
    kdialog --title SaveSync --error "$message"
else
    printf '%s\n' "$message" >&2
fi
exit 1
