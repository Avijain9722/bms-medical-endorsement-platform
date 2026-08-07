#!/usr/bin/env bash
# Build the offline wheelhouse for a Windows BMS host.
#
# One script rather than a recipe in a document, because the recipe and what was
# actually run drifted apart once already and cost a failed installation on the
# BMS host. CI runs this exact script, so what ships is what was tested.
#
#   deploy/build-wheelhouse.sh [python-version] [destination]
#   deploy/build-wheelhouse.sh 3.14 deploy/wheelhouse
set -euo pipefail

PYTHON_VERSION="${1:-3.14}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${2:-$HERE/wheelhouse}"
APP="$(cd "$HERE/../app" && pwd)"

ABI="cp${PYTHON_VERSION//./}"

# Packages a Windows host needs that pip will NOT fetch for us.
#
# `--platform win_amd64` selects which wheel tags are acceptable. It does not
# change the environment markers pip evaluates -- those are still read from this
# machine. So a requirement written
#
#     colorama ; platform_system == "Windows"
#
# is judged against Linux, decided False, and silently omitted. The bundle then
# looks complete and fails on the BMS host with "No matching distribution found".
# Anything behind such a marker has to be named here explicitly.
WINDOWS_ONLY=(colorama)

# Windows-only by nature: the Excel automation bridge.
WINDOWS_EXTRAS=(pywin32)

# Optional locally, but bundled so the BMS host can read PDF text layers.
OPTIONAL=(pypdf)

echo "==> Building wheelhouse for Python ${PYTHON_VERSION}, win_amd64"
echo "    destination: ${DEST}"
mkdir -p "$DEST"

download() {
    python3 -m pip download --quiet --dest "$DEST" \
        --only-binary=:all: \
        --platform win_amd64 \
        --python-version "$PYTHON_VERSION" \
        --implementation cp \
        --abi "$ABI" \
        "$@"
}

# requirements-dev.txt includes requirements.txt, and adds what verify.ps1 needs
# to run the suite on the BMS machine.
download -r "$APP/requirements-dev.txt"
download "${OPTIONAL[@]}" "${WINDOWS_EXTRAS[@]}" "${WINDOWS_ONLY[@]}"

echo
echo "==> Checking the bundle is complete for a Windows target"
# Reads each wheel's own metadata. Installing from the bundle here with
# --no-index would NOT catch a missing Windows-only package: it skips the same
# requirement for the same wrong reason the download did, and passes.
python3 "$APP/tools/check_wheelhouse.py" "$DEST"

echo
echo "==> Compiled wheels (every one must be ${ABI} / win_amd64)"
find "$DEST" -name '*.whl' ! -name '*none-any.whl' -printf '    %f\n' | sort
if find "$DEST" -name '*.whl' ! -name '*none-any.whl' ! -name "*${ABI}*" | grep -q .; then
    echo "a compiled wheel is not built for ${ABI}" >&2
    exit 1
fi

echo
echo "$(find "$DEST" -name '*.whl' | wc -l) wheels, $(du -sh "$DEST" | cut -f1)"
