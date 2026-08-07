#!/usr/bin/env bash
# Install the BMS endorsement platform on a Linux host.
#
# Creates a virtual environment, installs dependencies, applies migrations and
# checks what the host can actually do. Safe to re-run: it upgrades in place.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/../app" && pwd)"
cd "$APP"

echo "==> Python"
python3 --version
python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
PY

echo "==> Virtual environment"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

# If the offline bundle is present, install from it and never touch the network.
# --no-index makes pip refuse to contact PyPI at all, so an incomplete bundle
# fails loudly here rather than silently reaching out from a host that is not
# supposed to have internet access.
WHEELS="$HERE/wheelhouse"
if [ -d "$WHEELS" ]; then
    echo "    offline bundle found -- installing without network access"
    python -m pip install --quiet --no-index --find-links "$WHEELS" -r requirements.txt
else
    echo "    no offline bundle -- downloading from PyPI"
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
fi

echo "==> Configuration"
if [ ! -f "$APP/.env" ]; then
    cp "$HERE/.env.example" "$APP/.env"
    echo "    created app/.env"
else
    echo "    app/.env already present, left unchanged"
fi

# Generate the session signing key rather than asking someone to do it by hand.
# Left as CHANGE_ME it would be a shared, published secret; left unset entirely a
# new key is minted at every start and everyone is signed out on every restart.
if grep -q '^BMS_SECRET_KEY=CHANGE_ME' "$APP/.env" 2>/dev/null; then
    KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    python - "$APP/.env" "$KEY" <<'PY'
import sys, pathlib
path, key = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = path.read_text().splitlines(keepends=True)
path.write_text("".join(
    f"BMS_SECRET_KEY={key}\n" if line.startswith("BMS_SECRET_KEY=CHANGE_ME") else line
    for line in lines
))
PY
    echo "    generated a unique BMS_SECRET_KEY"
fi

# An earlier .env.example shipped a literal seed password. Neutralise it, so the
# platform generates a strong one and prints it once instead of every install
# sharing the same known credential.
if grep -q '^BMS_SEED_PASSWORD=CHANGE_ME' "$APP/.env" 2>/dev/null; then
    python - "$APP/.env" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1])
path.write_text("".join(
    "# BMS_SEED_PASSWORD=   # unset: a strong one is generated and printed once\n"
    if line.startswith("BMS_SEED_PASSWORD=CHANGE_ME") else line
    for line in path.read_text().splitlines(keepends=True)
))
PY
    echo "    removed the placeholder BMS_SEED_PASSWORD (one will be generated)"
fi

echo "==> Database"
set -a; [ -f "$APP/.env" ] && . "$APP/.env"; set +a
python -m alembic upgrade head

echo "==> Host capabilities"
python - <<'PY'
from bms.intake import scanning
from bms.ocr.text import TextPipeline
from bms.outputs import recalc

engines = TextPipeline().available_engines()
print(f"    OCR engines        : {', '.join(engines) or 'NONE -- scans must be typed in by hand'}")
scan = scanning.describe_host()
print(f"    Virus scanning     : {scan['engine'] or 'NONE -- uploads recorded as unscanned'}")
calc = recalc.describe_host()
print(f"    Excel recalculation: {'yes' if calc['excel_recalculation'] else 'no -- ' + calc['reason']}")
PY

echo
echo "Install complete."
echo "  Start:  ./deploy/start-linux.sh"
echo "  Or:     cd app && .venv/bin/python -m uvicorn bms.web.app:app --host 127.0.0.1 --port 8000"
echo "  Or:     sudo cp deploy/bms-endorsements.service /etc/systemd/system/ && sudo systemctl enable --now bms-endorsements"
