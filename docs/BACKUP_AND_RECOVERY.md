# Backup and recovery

BMS said backup is not mandatory at this stage, so nothing here runs
automatically. Everything is built and documented so it can be switched on
whenever BMS wants it.

Read this before you need it. The restore drill in §5 is the part people skip
and then regret.

---

## 1. What has to be backed up

Three things, and only three:

| # | What | Where | Lose it and… |
| --- | --- | --- | --- |
| 1 | **The database** | `BMS_DATABASE_URL` | Every case, member, log entry and audit row is gone. Unrecoverable |
| 2 | **The data root** | `BMS_DATA_ROOT` (default `<repo>/var`) | Uploaded documents and generated workbooks for open and recently-closed cases are gone |
| 3 | **The secret key** | `BMS_SECRET_KEY`, held outside the repo | Everyone is logged out once. Nothing else |

Two things that need **no** backup, because they are reproducible:

- **The source workbooks** under `02_PORTAL_TEMPLATES/` and
  `03_INTERNAL_LOG_TEMPLATE/` — immutable, verified against
  `00_START_HERE/SHA256SUMS.txt`, and restorable from source control.
- **The application code** — it is in git.

### How the two backed-up things relate

The database holds the *record*; the data root holds the *bytes*. A `case_file`
row points at a content-addressed file under `BMS_DATA_ROOT/storage`. Restore
the database without the data root and cases exist with their documents missing.
Restore the data root without the database and you have a pile of hash-named
files nothing refers to.

**Always back them up together, and always restore them together.** They do not
have to be captured in the same instant — the mismatch window is one backup
interval, and it only ever produces missing files, never wrong ones, because
storage is content-addressed and files are never overwritten in place.

---

## 2. Backing up

### PostgreSQL

```bash
pg_dump --format=custom --file=/backup/bms-$(date +%F).dump \
        "postgresql://bms:...@localhost/bms"
```

`--format=custom` is worth it: it compresses, and it lets you restore selected
tables if you ever need to.

### SQLite

Use SQLite's own backup command, not `cp`. Copying a live SQLite file gets you a
torn database.

```bash
sqlite3 /path/to/bms_prototype.db ".backup '/backup/bms-$(date +%F).db'"
```

### The data root

```bash
tar czf /backup/var-$(date +%F).tgz -C /var/lib bms-endorsements
```

Nothing under `BMS_DATA_ROOT/storage` is ever modified after it is written —
files are content-addressed and immutable until purge — so an incremental or
rsync-based backup works well and stays cheap.

### One script for both

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date +%F-%H%M)
DEST=/backup/bms

mkdir -p "$DEST"
pg_dump --format=custom --file="$DEST/db-$STAMP.dump" "$BMS_DATABASE_URL_LIBPQ"
tar czf "$DEST/data-$STAMP.tgz" -C "$(dirname "$BMS_DATA_ROOT")" "$(basename "$BMS_DATA_ROOT")"

sha256sum "$DEST/db-$STAMP.dump" "$DEST/data-$STAMP.tgz" > "$DEST/manifest-$STAMP.sha256"
find "$DEST" -name '*-*' -mtime +30 -delete
echo "backup complete: $STAMP"
```

Run it from cron or a scheduled task while the application is running — neither
step needs downtime. `BMS_DATABASE_URL_LIBPQ` is the same database in libpq
form (`postgresql://…`, without SQLAlchemy's `+psycopg`).

### Windows

```powershell
$stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" `
    --format=custom --file="D:\backup\bms\db-$stamp.dump" $env:BMS_DATABASE_URL_LIBPQ
Compress-Archive -Path $env:BMS_DATA_ROOT -DestinationPath "D:\backup\bms\data-$stamp.zip"
```

### Suggested schedule

| What | How often | Keep |
| --- | --- | --- |
| Database | Daily | 30 days, plus one monthly for a year |
| Data root | Daily | 30 days — retention purges most of it after 36 hours anyway |
| Off-host copy | Weekly | A backup on the same machine survives a bad deploy, not a dead disk |

Backups contain **member identity data** — names, Emirates ID numbers, passport
details, scanned documents. Treat the backup destination with exactly the access
control the live system gets, and encrypt anything that leaves the BMS network.

---

## 3. Restoring

Stop the application first. Restoring underneath a running server produces
inconsistent state.

```bash
sudo systemctl stop bms-endorsements
```

### Database

```bash
# PostgreSQL — into a clean database
dropdb bms && createdb bms
pg_restore --dbname="postgresql://bms:...@localhost/bms" /backup/bms-2026-08-05.dump

# SQLite
cp /backup/bms-2026-08-05.db /path/to/bms_prototype.db
```

### Data root

```bash
rm -rf /var/lib/bms-endorsements
tar xzf /backup/var-2026-08-05.tgz -C /var/lib
chown -R bms:bms /var/lib/bms-endorsements
```

Ownership catches people out. If the service account cannot write to the data
root, uploads fail after the restore looked fine.

### Then, before letting anyone in

```bash
cd app
python3 -m alembic upgrade head          # in case the code is newer than the dump
python3 tools/verify_sources.py          # confirm the master workbooks are intact
sudo systemctl start bms-endorsements
curl -s http://127.0.0.1:8000/health
```

`alembic upgrade head` is safe to run against an already-current database — it
does nothing. Running it saves you from the case where you restored last month's
dump onto this month's code.

---

## 4. Recovery scenarios

### The application will not start after an upgrade

Nothing is lost; the data is fine.

```bash
git checkout <previous-tag>
cd app && python3 -m alembic downgrade -1     # only if the upgrade added a migration
sudo systemctl start bms-endorsements
```

Migrations are written to be reversible and that reversibility is tested.

### Someone deleted the data root

Cases, members, log entries and the audit trail are all in the database and
unaffected. Restore the data root from the most recent backup; documents for
cases closed more than the retention window ago were due for deletion anyway.

Cases whose documents cannot be restored still show their extracted values and
their audit history. Only the original files are gone.

### The database is corrupt

Restore the most recent dump. Work done since that dump is lost and has to be
reprocessed from the client's original email and attachments — which is why the
daily dump matters and the data root, on its own, is not a backup.

### Everyone is locked out

Not a restore situation.

```bash
cd app && python3 -m bms.cli create-user --username rescue --admin
```

The CLI needs filesystem and database access, not a session.

### The host is gone entirely

1. Build a new host — see [PROTOTYPE_SETUP.md](PROTOTYPE_SETUP.md) or
   [`deploy/install.sh`](../deploy/install.sh).
2. Clone the repository at the tag that was running.
3. Restore the environment file, **including `BMS_SECRET_KEY`**.
4. Restore the database and the data root.
5. `alembic upgrade head`, `verify_sources.py`, start, `/health`.

Expect an hour, most of it installing dependencies. Everything after step 2 is
copy-and-run.

### A master workbook has been altered

`verify_sources.py` reports a mismatch on an immutable entry.

**Stop processing.** The structural fingerprints are derived from these files,
so a modified master means generated workbooks are being checked against the
wrong baseline. Restore the file from source control, re-run the verifier, and
regenerate any export produced since the change.

---

## 5. The restore drill

A backup you have never restored is a hypothesis. Test it monthly, on a
throwaway host, using the real backup files.

```bash
# On a scratch host, from real backups
createdb bms_drill
pg_restore --dbname=postgresql://localhost/bms_drill /backup/db-latest.dump
mkdir -p /tmp/drill && tar xzf /backup/data-latest.tgz -C /tmp/drill

cd app
BMS_DATABASE_URL=postgresql+psycopg://localhost/bms_drill \
BMS_DATA_ROOT=/tmp/drill/bms-endorsements \
BMS_SECRET_KEY=drill-only \
  .venv/bin/python -m uvicorn bms.web.app:app --port 8099
```

Then confirm, by hand:

- [ ] `/health` returns `status: ok` and the expected database
- [ ] Sign in with a known account
- [ ] The case list shows the cases you expect, with the right counts
- [ ] Open a recently closed case — its members and their values are there
- [ ] Download an export whose case is inside the retention window — the file
      opens
- [ ] The log screen shows entries, and a date-range export produces a workbook
- [ ] The audit trail shows entries from before the backup was taken
- [ ] `cd app && python3 tools/verify_sources.py` passes

Then drop the drill database and delete `/tmp/drill`. **The drill copy holds real
member data** — do not leave it lying on a scratch host.

Record the date and the result. An untested backup that fails at 9am on a Monday
is worse than no backup, because you planned around it.

---

## 6. What survives what

| Event | Cases & members | Log entries | Audit trail | Documents | Exports |
| --- | --- | --- | --- | --- | --- |
| Sign-out / browser closed | ✓ | ✓ | ✓ | ✓ | ✓ |
| Application restart | ✓ | ✓ | ✓ | ✓ | ✓ |
| Host reboot | ✓ | ✓ | ✓ | ✓ | ✓ |
| Case closed, retention elapsed | ✓ | ✓ | ✓ | **purged** | **purged** |
| Data root lost, database intact | ✓ | ✓ | ✓ | lost | lost |
| Database lost, data root intact | lost | lost | lost | orphaned | orphaned |
| Both restored from backup | ✓ to the backup point | ✓ | ✓ | ✓ | ✓ |

The bottom two rows are the argument for backing up both together. The
database is the one you cannot reconstruct from anything else.
