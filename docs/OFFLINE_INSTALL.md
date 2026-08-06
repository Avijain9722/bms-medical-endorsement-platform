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
| Libraries | 20 runtime, plus test tooling and optional extras |
| Wheels | 33 |
| Size | ~16 MB |
| Operating system | Windows (`win_amd64`) |
| Python version | 3.14 |

Most libraries are pure Python and work anywhere. Five contain compiled code and
need a build matched to the exact operating system and Python version —
`SQLAlchemy`, `pydantic-core`, `greenlet`, `MarkupSafe` and `pywin32` — which is
why the bundle is specific to one Python version rather than universal. Pinning
to a single version is what keeps it to 16 MB instead of 55.

Also included:

- **pytest, httpx and openpyxl** — so you can run the full 300-test suite on the
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

**Python itself is also not in the bundle.** Install **Python 3.14** from
python.org first; on Windows tick **"Add Python to PATH"** during setup. It must
be 3.14: the compiled wheels are built for it, and `install.ps1` checks and says
so plainly rather than letting pip fail with a confusing message.

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
./deploy/build-wheelhouse.sh 3.14 deploy/wheelhouse
```

One script rather than a recipe here, because a recipe and what was actually run
drifted apart once and cost a failed installation on the BMS host. CI runs this
exact script, so what ships is what was tested. Pass a different version as the
first argument when BMS adopts one.

### `--platform` does not make pip think it is on Windows

This cost a failed installation on the BMS host, so it is worth stating plainly.

`--platform win_amd64` chooses which wheel **tags** are acceptable. It does not
change the environment markers pip evaluates. A requirement written

```
colorama ; platform_system == "Windows"
```

is therefore tested against the *building* machine — Linux — decided to be
False, and silently left out. The bundle looks complete and installs cleanly
here, then fails on the BMS host with:

```
ERROR: Could not find a version that satisfies the requirement colorama;
platform_system == "Windows" (from click)
```

Anything behind such a marker has to be named explicitly, as `colorama` is
above.

### Verify it the way that actually catches this

The obvious check — installing from the wheelhouse with `--no-index` on the
build machine — **does not work**. It skips the Windows-gated requirement for
exactly the same wrong reason the download did, and passes.

Use the checker instead. It reads each wheel's own metadata and reports every
requirement a Windows host would activate that is not in the folder. No network,
no Windows needed:

```bash
cd app
python3 tools/check_wheelhouse.py ../deploy/wheelhouse
```

It exits non-zero when something is missing, so it can gate a release. A local
`--no-index` install is still worth running afterwards — it catches a different
class of problem, a wheel whose tags do not match the target — but on its own it
is not evidence the bundle is complete.

The only complete proof is installing the bundle on Windows. The
`offline-install` CI job does exactly that on every change: `offline-bundle`
cross-builds the wheelhouse on Linux — the situation that hid the missing
package — uploads it, and `offline-install` installs it on a Windows runner with
`--no-index`, then runs the suite and starts the platform. Had it existed, the
bundle that failed at BMS would have failed here first.
