# Phase 1 prototype — setup and operation

How to install, run and operate the endorsement platform prototype. Written for
whoever sets it up on the BMS host, not only for developers.

---

## What this phase delivers

A working internal web application that takes a pasted client instruction and a
pile of documents, and produces a NAS portal workbook and the approved BMS log.

- Browser-based, server-rendered, inside the BMS network.
- Python backend, PostgreSQL-ready schema, SQLite for the prototype.
- Local OCR only. No document leaves the host.
- Case data written to disk and the database as it happens — nothing depends on
  the browser holding state.
- One NAS addition workflow and one NAS deletion workflow, end to end.
- Member-level review and correction, with confidence and exception flags.
- Append-only audit trail.

---

## Requirements

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11 or newer | |
| Database | SQLite (bundled) or PostgreSQL 14+ | SQLite is fine for a single-user prototype |
| Tesseract | 5.x | Optional. Without it, scans must be typed in by hand |
| Microsoft Excel | 365 | Only needed later, for the Daman and log recalculation step |

## Install

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional local OCR:

```bash
# Debian/Ubuntu
sudo apt-get install tesseract-ocr tesseract-ocr-ara
pip install -r requirements-optional.txt
```

Nothing here contacts an external service at runtime.

## Run

```bash
cd app
uvicorn bms.web.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. On first start the application creates a user
`bms` and prints its password once:

```
[bms] created initial user 'bms' with password: 8Kd2wQ...
```

Set `BMS_SEED_PASSWORD` to choose it yourself. Change it before real use.

Check what is actually available on the host:

```bash
curl -s http://127.0.0.1:8000/health
{"status":"ok","database":"sqlite","ocr_engines":["plain_text"],...}
```

`ocr_engines` lists the readers that loaded. If Tesseract is missing it will not
appear, and scanned documents will be marked `ocr_unavailable` and raised for
manual entry rather than being guessed at.

## Configuration

All settings are environment variables. None of them are business rules —
client rules live in the database.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BMS_DATABASE_URL` | `sqlite:///./bms_prototype.db` | Any SQLAlchemy URL. For PostgreSQL: `postgresql+psycopg://user:pass@host/bms` |
| `BMS_DATA_ROOT` | `<repo>/var` | Where uploads and generated files are written |
| `BMS_TEMPLATE_ROOT` | repository root | Where the source workbooks live. Read-only |
| `BMS_SECRET_KEY` | generated per start | Session signing key. **Set this**, or sessions end at every restart |
| `BMS_SEED_PASSWORD` | generated | Initial account password |
| `BMS_DOCUMENT_RETENTION_HOURS` | `36` | Hours after **case closure** before documents are purged |
| `BMS_PURGE_ENABLED` | `true` | Set `false` to keep documents indefinitely |
| `BMS_OCR_ENABLED` | `true` | Set `false` to force manual entry |
| `BMS_TESSERACT_CMD` | `tesseract` | Full path on Windows |
| `BMS_OCR_LANGUAGES` | `eng+ara` | Passed to Tesseract |
| `BMS_MAX_UPLOAD_BYTES` | 64 MB | Per uploaded file |
| `BMS_MAX_ARCHIVE_MEMBERS` | 500 | Per ZIP, guards against archive bombs |

Serve behind TLS in any real deployment and set `secure=True` on the session
cookie in `bms/web/app.py`.

## Moving to PostgreSQL

```bash
export BMS_DATABASE_URL="postgresql+psycopg://bms:password@localhost/bms"
pip install "psycopg[binary]"
```

The schema is written to be portable — explicit string lengths, timezone-aware
timestamps, dialect-neutral JSON, string UUID keys. The prototype creates tables
directly; production should adopt Alembic so schema changes are reviewable.

---

## Using it

1. **New case.** Client, insurer, transaction type. Paste the client email body
   into **BMS Comments**. The platform never reads a mailbox.
2. **Upload documents.** Individual files or ZIPs; nested ZIPs are expanded,
   identical files are stored once and reported as duplicates.
3. **Run local processing.** Text is extracted, documents are classified,
   members are identified and their documents grouped.
4. **Review each member.** Every proposed value shows its confidence and the
   document it came from. Corrections are audited and mark the value confirmed.
5. **Approve.** Refused while a critical issue is open.
6. **Export.** Produces the NAS workbook and the BMS log. The generated workbook
   is re-fingerprinted against its master and blocked on any structural change.
7. **Close.** Starts the document retention clock. Records and the audit trail
   are kept regardless.

## Retention

Purging is not automatic in the prototype — run it from a scheduled task:

```bash
cd app && python3 -c "
from bms.db import init_engine, session_scope
from bms.pipeline import purge_closed_cases
init_engine()
with session_scope() as s:
    print(purge_closed_cases(s))"
```

It removes uploaded documents and generated files for cases closed more than
`BMS_DOCUMENT_RETENTION_HOURS` ago. The clock starts at **closure**, so an open
case never loses its evidence. Case records, member rows, log entries and the
audit trail are always kept.

## Backup

Two things to copy: the database, and `BMS_DATA_ROOT`.

```bash
sqlite3 bms_prototype.db ".backup 'backup.db'"     # or pg_dump
tar czf var-backup.tgz var/
```

## Tests

```bash
cd app
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

The suite runs **without** Tesseract on purpose: one of the things it proves is
that the pipeline degrades honestly when no OCR engine is present.

---

## Adding another insurer template

Designed so this does not touch processing code:

1. Add a `TemplateSpec` in `app/bms/templates/specs.py` — source path, entry
   sheet, header row, first data row, column map.
2. Add a `ValueMap` in `app/bms/outputs/mapping.py` for that insurer's literals
   (`Male` vs `MALE` vs `M`) and its date format.
3. Add a row builder alongside `_addition_row` in `app/bms/outputs/nas.py`, or a
   sibling module for a differently-shaped template.
4. Run the preservation suite. It picks up new specs automatically and will fail
   if the generated file differs structurally from its master.

Nothing in intake, OCR, matching, validation, review or audit changes.
