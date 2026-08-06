#!/usr/bin/env bash
# Copy Tesseract INTO the project, so OCR travels with the platform.
#
# A locked-down BMS host may not allow an installer to run, or may not have the
# packages available offline. This takes a Tesseract that is already installed
# (or unpacked) on a machine you do control and assembles a self-contained copy
# under vendor/tesseract/, which the platform then finds automatically.
#
#   ./deploy/bundle-tesseract.sh                 # from the system install
#   ./deploy/bundle-tesseract.sh /opt/tesseract  # from an unpacked build
#
# Copy the whole project folder to the BMS host afterwards. Nothing needs to be
# installed there and no administrator rights are required.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$(cd "$HERE/.." && pwd)/vendor/tesseract"
SOURCE="${1:-}"

if [ -n "$SOURCE" ]; then
    BINARY="$SOURCE/bin/tesseract"
    [ -x "$BINARY" ] || BINARY="$SOURCE/tesseract"
else
    BINARY="$(command -v tesseract || true)"
fi

if [ -z "$BINARY" ] || [ ! -x "$BINARY" ]; then
    echo "No tesseract found. Install it first, or pass the path to an unpacked build." >&2
    echo "  Debian/Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-ara" >&2
    exit 1
fi

echo "==> Source"
echo "    $BINARY"
"$BINARY" --version 2>&1 | head -1 | sed 's/^/    /'

mkdir -p "$VENDOR/tessdata"
cp "$BINARY" "$VENDOR/tesseract"
chmod +x "$VENDOR/tesseract"

echo "==> Shared libraries"
# A system tesseract links against libraries that will not be present on the
# target host, so they have to travel too. The wrapper below points the loader
# at them.
if command -v ldd >/dev/null; then
    mkdir -p "$VENDOR/lib"
    ldd "$BINARY" | awk '/=> \//{print $3}' | while read -r lib; do
        cp -n "$lib" "$VENDOR/lib/" 2>/dev/null || true
    done
    echo "    copied $(ls "$VENDOR/lib" | wc -l) libraries"

    mv "$VENDOR/tesseract" "$VENDOR/tesseract.bin"
    cat > "$VENDOR/tesseract" <<'WRAPPER'
#!/usr/bin/env bash
# Run the bundled tesseract against the bundled libraries.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$HERE/lib:${LD_LIBRARY_PATH:-}"
export TESSDATA_PREFIX="${TESSDATA_PREFIX:-$HERE/tessdata}"
exec "$HERE/tesseract.bin" "$@"
WRAPPER
    chmod +x "$VENDOR/tesseract"
fi

echo "==> Language data"
# Find the system tessdata and take the languages the platform asks for.
PREFIX="${TESSDATA_PREFIX:-}"
if [ -z "$PREFIX" ]; then
    for guess in /usr/share/tesseract-ocr/*/tessdata /usr/share/tessdata /usr/local/share/tessdata; do
        [ -d "$guess" ] && PREFIX="$guess" && break
    done
fi

if [ -z "$PREFIX" ] || [ ! -d "$PREFIX" ]; then
    echo "    WARNING: no tessdata found. Copy eng.traineddata (and ara.traineddata" >&2
    echo "    for Arabic) into $VENDOR/tessdata manually." >&2
else
    for lang in eng ara osd; do
        [ -f "$PREFIX/$lang.traineddata" ] && cp -n "$PREFIX/$lang.traineddata" "$VENDOR/tessdata/"
    done
    echo "    $(ls "$VENDOR/tessdata" | tr '\n' ' ')"
fi

echo
echo "==> Checking the bundled copy"
"$VENDOR/tesseract" --list-langs 2>&1 | sed 's/^/    /'

echo
echo "Bundled into vendor/tesseract/. The platform finds it automatically --"
echo "no BMS_TESSERACT_CMD needed. Confirm with:  curl -s localhost:8000/health"
