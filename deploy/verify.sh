#!/usr/bin/env bash
# Verify the installation on THIS machine.
#
# Checks the supplied insurer workbooks are unaltered, then runs the full test
# suite. Test tooling is not part of the runtime install -- a production
# environment should not carry pytest -- so this installs it into the same
# virtual environment first, from the offline bundle when one is present.
#
# Run it after installing, and again after any upgrade or restore.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/../app" && pwd)"
cd "$APP"

if [ ! -x .venv/bin/python ]; then
    echo "No virtual environment found. Run deploy/install.sh first." >&2
    exit 1
fi

echo "==> Test tooling"
WHEELS="$HERE/wheelhouse"
if [ -d "$WHEELS" ]; then
    echo "    installing from the offline bundle"
    .venv/bin/python -m pip install --quiet --no-index --find-links "$WHEELS" \
        -r requirements-dev.txt
else
    echo "    downloading from PyPI"
    .venv/bin/python -m pip install --quiet -r requirements-dev.txt
fi

echo
echo "==> Source integrity"
# Missing entries under 04_DEVELOPMENT_EXAMPLES are expected: those files are
# advisory and are deliberately excluded from the release package.
.venv/bin/python tools/verify_sources.py

echo
echo "==> Test suite"
.venv/bin/python -m pytest tests -q

echo
echo "Verification complete. If both sections passed, this installation is sound."
