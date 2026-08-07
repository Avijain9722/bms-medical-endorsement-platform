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

# Start clean. The binary was overwritten on a re-run but the libraries and
# language files were not, so an upgraded tesseract ended up paired with the
# previous release's libtesseract -- an undefined-symbol crash on every call.
rm -rf "$VENDOR/lib" "$VENDOR/tesseract" "$VENDOR/tesseract.bin"
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
        cp -f "$lib" "$VENDOR/lib/" 2>/dev/null || true
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
        [ -f "$PREFIX/$lang.traineddata" ] && cp -f "$PREFIX/$lang.traineddata" "$VENDOR/tessdata/"
    done
    echo "    $(ls "$VENDOR/tessdata" | tr '\n' ' ')"
fi

# The program interpreter (ld-linux) is NOT bundled -- ldd prints it without a
# "=> " so it is not copied, and forcing a foreign one is not safe anyway. The
# target host therefore supplies ld.so while we supply libc, which only works if
# the target's glibc is the same or newer. Say so, loudly, because the build
# host always passes the check below regardless.
BUILD_GLIBC="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || echo '?')"
cat > "$VENDOR/BUILD-HOST.txt" <<EOF
Bundled on : $(uname -s) $(uname -m)
glibc      : $BUILD_GLIBC
tesseract  : $("$BINARY" --version 2>&1 | head -1)

This bundle carries libc and friends but NOT the program interpreter
(ld-linux-x86-64.so.2), which always comes from the host that runs it.
It therefore requires a target host with glibc $BUILD_GLIBC or NEWER.
On an older host every OCR call fails with an ld.so assertion or
"version \`GLIBC_x.yz' not found", and scans fall back to manual entry.

Check the target before copying:   ldd --version | head -1
If the target is older, rebuild this bundle on a host matching it.
EOF
echo "==> Portability"
echo "    built against glibc $BUILD_GLIBC -- target host needs $BUILD_GLIBC or newer"
echo "    (recorded in vendor/tesseract/BUILD-HOST.txt)"

echo
echo "==> Checking the bundled copy"
"$VENDOR/tesseract" --list-langs 2>&1 | sed 's/^/    /'

echo
echo "Bundled into vendor/tesseract/. The platform finds it automatically --"
echo "no BMS_TESSERACT_CMD needed. Confirm with:  curl -s localhost:8000/health"
