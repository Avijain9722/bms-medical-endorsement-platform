# Installing without internet access

The platform never needs the internet to **run** — no document is ever sent
anywhere. But installing it normally downloads a handful of Python libraries
from PyPI, which a locked-down BMS server will not allow.

The offline bundle removes that step. Every library is shipped inside the
package, and the installer is told to refuse network access entirely.

---

## Using it

Nothing special to do. If `deploy/wheelhouse/` is present, the installer finds
it and installs from it:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install.ps1     # Windows
```

```bash
./deploy/install.sh                                             # Linux
```

You will see:

```
==> Virtual environment
    offline bundle found -- installing without network access
```

If instead it says `no offline bundle -- downloading from PyPI`, the
`wheelhouse` folder is missing or was not extracted with the rest of the
package.

**The installer uses `--no-index`,** which makes pip refuse to contact PyPI even
if the machine does have a connection. An incomplete bundle therefore fails
loudly during installation rather than quietly reaching out from a host that is
not supposed to have internet access. That is deliberate: a silent download on
an air-gapped server is a compliance problem, not a convenience.

## What is in the bundle

| | |
| --- | --- |
| Libraries | Runtime dependencies, test tooling and optional extras |
| Wheels | Determined by the supported platform/version matrix when refreshed |
| Size | Determined by the wheel versions in the refreshed bundle |
| Operating systems | Windows (`win_amd64`) and Linux (`manylinux2014_x86_64`) |
| Python versions | 3.11, 3.12, 3.13 and 3.14 |

Most libraries are pure Python and work anywhere. Four contain compiled code and
need a build matched to the exact operating system and Python version —
`SQLAlchemy`, `pydantic-core`, `greenlet` and `MarkupSafe` — which is why the
bundle carries several copies of those and only those.

Also included:

- **pytest, HTTPX2, Ruff and openpyxl** — so you can run the current full suite on the
  BMS host itself, offline, rather than taking the delivered result on trust.
- **pypdf** — reads the text layer of PDFs directly, which is exact and always
  preferable to OCR.
- **pywin32** (Windows only) — lets Excel evaluate the BMS log and Daman
  formulas before hand-off.

## What is *not* in the bundle, and cannot be

Two things are operating-system packages, not Python ones, so they cannot ship
in a Python wheel bundle:

| | Get it from | Without it |
| --- | --- | --- |
| **Tesseract** (OCR) | UB Mannheim build on Windows; `apt-get install tesseract-ocr tesseract-ocr-ara` on Debian/Ubuntu | Scanned documents are flagged `ocr_unavailable` for manual typing. Nothing is guessed |
| **ClamAV** (virus scanning) | clamav.net, or `apt-get install clamav clamav-daemon` | Uploads are recorded as *unscanned* — never as clean — and raise a warning flag |

Both are optional. The platform works without either and says so at `/health`.

**Python itself is also not in the bundle.** Install Python 3.11 or newer from
python.org first; on Windows tick **"Add Python to PATH"** during setup.

## Verifying it worked

One command does both checks, installing the test tooling from the bundle
first (the runtime install deliberately does not carry pytest):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\verify.ps1     # Windows
```

```bash
./deploy/verify.sh                                             # Linux
```

And to see what the host can actually do:

```bash
curl -s http://127.0.0.1:8000/health
```

The test suite runs entirely offline. It is worth running once on the BMS host,
because it proves the installation is complete on that machine rather than on
the machine it was built on.

## Refreshing the bundle

Library versions move. To rebuild it on a machine that does have internet:

```bash
cd app
for PY in 311 312 313 314; do
  for PLAT in win_amd64 manylinux2014_x86_64; do
    python3 -m pip download -r requirements-dev.txt -d ../deploy/wheelhouse \
      --only-binary=:all: --platform "$PLAT" --python-version "$PY"
    python3 -m pip download pypdf -d ../deploy/wheelhouse \
      --only-binary=:all: --platform "$PLAT" --python-version "$PY"
  done
  python3 -m pip download pywin32 -d ../deploy/wheelhouse \
    --only-binary=:all: --platform win_amd64 --python-version "$PY"
done
```

Add a Python version to the `PY` list when BMS adopts one. Then test the result
the way it will actually be used — a clean virtual environment with the network
refused:

```bash
python3 -m venv /tmp/check
/tmp/check/bin/python -m pip install --no-index \
    --find-links deploy/wheelhouse -r app/requirements-dev.txt
```

If that succeeds, the bundle is complete.
