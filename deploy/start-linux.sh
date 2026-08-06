#!/usr/bin/env bash
# BMS Medical Endorsement Platform -- one command to install and run.
#
# The first run installs everything (a few minutes, no internet needed).
# Every run after that starts in a few seconds.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="$ROOT/app"

echo
echo " BMS Medical Endorsement Platform"
echo " ================================"
echo

# Python must already be installed; it cannot be bundled.
if ! command -v python3 >/dev/null; then
    echo " Python 3.11 or newer is required but was not found."
    echo "   Debian/Ubuntu:  sudo apt-get install python3 python3-venv"
    exit 1
fi

# First run installs; later runs skip straight to starting.
if [ ! -x "$APP/.venv/bin/python" ]; then
    echo " First run - setting up. This takes a few minutes."
    echo " No internet connection is needed."
    echo
    "$HERE/install.sh"
    echo
fi

PORT="${BMS_PORT:-8000}"
echo " Starting on http://127.0.0.1:$PORT"
echo
echo " Press Ctrl+C to stop the platform."
echo

cd "$APP"
set -a; [ -f "$APP/.env" ] && . "$APP/.env"; set +a
exec .venv/bin/python -m uvicorn bms.web.app:app --host 127.0.0.1 --port "$PORT"
