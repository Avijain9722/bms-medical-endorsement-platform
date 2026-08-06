# BMS Medical Endorsement Platform

Internal platform for BMS Masaood Insurance L.L.C. – O.P.C. that turns a pasted
client instruction plus a pile of supporting documents into a correctly filled
insurer portal workbook and the approved BMS operational log.

Runs entirely inside the BMS network. Nothing it processes leaves the host.

---

## Start here

| You are | Read |
| --- | --- |
| Processing endorsement requests | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| Installing or running the host | [docs/ADMIN_MANUAL.md](docs/ADMIN_MANUAL.md) and [docs/PROTOTYPE_SETUP.md](docs/PROTOTYPE_SETUP.md) |
| Maintaining the code | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Wondering what is actually proven | [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) |

Full index: [docs/README.md](docs/README.md).

## Run it

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m alembic upgrade head
uvicorn bms.web.app:app --host 127.0.0.1 --port 8000
```

The first start prints a seed account's password once. Capture it, sign in,
create real accounts. Set `BMS_SECRET_KEY` before that first start or everyone is
logged out at every restart.

Scripted installs for Linux and Windows are in [`deploy/`](deploy).

## What it will not do

By instruction and by design:

- No mailbox or Outlook integration — the email body is pasted by the user.
- No external AI or token-based document API — OCR runs locally.
- No change to the structure of any insurer, TPA or BMS log template — every
  generated workbook is re-fingerprinted against its master and blocked on any
  delta.
- No invented values — missing information raises a flag, never a plausible guess.
- No override of a critical error — the underlying data has to be corrected.
- Post-submission fields stay genuinely blank until staff perform the explicit
  workflow action.

## Repository layout

```
00_START_HERE/             the brief, file manifest and SHA256SUMS   (read-only)
01_BRAND_ASSETS/           logos and brand guidelines                (read-only)
02_PORTAL_TEMPLATES/       the eight insurer workbooks               (read-only)
03_INTERNAL_LOG_TEMPLATE/  New Log Format -2026                      (read-only)
04_DEVELOPMENT_EXAMPLES/   reference emails — never imported         (read-only)
05_TECHNICAL_REFERENCE/    NAS portal field guide                    (read-only)

app/                       the application
  bms/                     pipeline, ocr, matching, validation, outputs, ooxml, web
  tests/                   246 automated tests
  migrations/              Alembic
deploy/                    install scripts and service units
docs/                      the documentation set
  tools/                   verify_sources.py and reporting helpers
```

Everything numbered `00_`–`05_` is supplied material. The platform reads it and
never writes to it.

## Verify

```bash
cd app && python3 -m pytest tests -q     # 246 tests
cd app && python3 tools/verify_sources.py   # supplied workbooks unaltered
curl -s http://127.0.0.1:8000/health     # what this host can and cannot do
```
