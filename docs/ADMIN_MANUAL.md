# Administrator manual

For whoever installs, configures and looks after the platform on the BMS host.
Day-to-day processing is covered in [USER_MANUAL.md](USER_MANUAL.md); install
mechanics in [PROTOTYPE_SETUP.md](PROTOTYPE_SETUP.md); backups in
[BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

---

## Contents

1. [First-time setup](#1-first-time-setup)
2. [How login works](#2-how-login-works)
3. [Accounts](#3-accounts)
4. [The client master](#4-the-client-master)
5. [Templates](#5-templates)
6. [Health checks and host capabilities](#6-health-checks)
7. [Retention and the purge](#7-retention)
8. [The audit trail](#8-the-audit-trail)
9. [Monthly routine](#9-monthly-routine)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. First-time setup

The full sequence is in [PROTOTYPE_SETUP.md](PROTOTYPE_SETUP.md); scripted
installers live in [`deploy/`](../deploy). The short version:

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m alembic upgrade head
.venv/bin/python -m uvicorn bms.web.app:app --host 127.0.0.1 --port 8000
```

On Windows the last line is `.venv\Scripts\python.exe -m uvicorn bms.web.app:app --host 127.0.0.1 --port 8000`.

`.venv/bin/python -m uvicorn` rather than a bare `uvicorn`: the explicit path
works whether or not the virtual environment is activated in this particular
terminal. A bare `uvicorn` in a fresh terminal fails with
`No module named uvicorn`, because it runs the system Python instead.


Before that first start, set at minimum:

```bash
export BMS_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
export BMS_DATABASE_URL="postgresql+psycopg://bms:...@localhost/bms"
export BMS_DATA_ROOT=/var/lib/bms-endorsements
```

**`BMS_SECRET_KEY` is the one that bites you if you skip it.** Without it the
application generates a new key at every start, which invalidates every signed
session cookie — everyone is logged out on every restart. Set it once, keep it
secret, keep it stable.

Store it outside the repository. On Linux use the systemd unit's
`EnvironmentFile=`; on Windows use the service account's environment. Never
commit it.

### The seed account

On the very first start, if there are no users at all, the platform creates one
account and prints its password to the console **once**:

```
[bms] created initial user 'bms' with password: 8Kd2wQ...
```

That password is never shown again and is not recoverable — only its hash is
stored. Capture it from the console, sign in, create the real accounts, and
change or disable `bms`. Set `BMS_SEED_PASSWORD` beforehand if you would rather
choose it yourself.

If the database already has users, no seed account is created. The seeding
cannot be used to get back in later; use the CLI (§3) for that.

---

## 2. How login works

Deliberately simple, because the platform is only reachable inside the BMS
network. There is no AD, no LDAP, no SSO and no MFA — BMS confirmed none is
wanted at this stage.

**What is stored.** Only a PBKDF2-HMAC-SHA256 hash, 240,000 iterations, with a
per-user random salt. Passwords themselves are never written anywhere — not to
the database, not to the logs, not to the audit trail. You cannot read a user's
password; you can only reset it.

**What happens at sign-in.** The submitted password is hashed with the stored
salt and compared in constant time. A wrong username and a wrong password fail
identically, so the form cannot be used to discover who has an account.

**The session.** On success the server sets one cookie holding the username and
an expiry, signed with `BMS_SECRET_KEY` using HMAC. The cookie is `HttpOnly` and
`SameSite=Lax`. It carries no privileges of its own — every request re-loads the
user from the database, so disabling an account takes effect on that user's very
next click rather than whenever their cookie happens to expire.

Editing the cookie invalidates the signature and drops you at the sign-in
screen. Sessions end on sign-out, at expiry, or when the secret key changes.

**Behind TLS.** Serve the application behind HTTPS in any real deployment and
set `BMS_COOKIE_SECURE=true`. The cookie is then never sent in the clear, and
HSTS is advertised. Over plain HTTP the cookie is visible to anyone on the
network path. Leave it `false` until TLS is actually in place — a secure cookie
over HTTP is never sent at all, which locks everyone out.

**Brute force.** Five failed sign-ins for one username, or from one address,
within fifteen minutes locks that username or address out for fifteen minutes.
The check runs before any password hashing, so a locked-out caller cannot keep
spending server CPU. An administrator can reset the password immediately rather
than waiting out the lockout.

**Identity is used, not just checked.** The signed-in user fills the log's
`SHARED BY` column and stamps every audit row. Shared accounts destroy both.
Give every processor their own.

---

## 3. Accounts

Two kinds:

| | Can do |
| --- | --- |
| **Processor** | Everything in the case journey: create, upload, process, review, approve, export, close; the log screen; the audit view |
| **Administrator** | All of that, plus the **Admin** screen: accounts, passwords, and the client master |

There is a single operational role for processing — no maker/checker split, and
no role that can override a critical error. That was BMS's decision and it is
enforced in the code, not by permissions: no override endpoint exists.

### From the web

**Admin** in the top navigation:

- **Add user** — username, display name, log associate name, administrator yes/no.
- **Reset password** — sets a new one immediately.
- **Disable** — leaves the account and its history intact but refuses sign-in.

Set the **log associate** to the name as it appears in the log's associate
vocabulary. That is what lands in `SHARED BY`. If it is blank, the log falls back
to the display name.

### From the command line

Use this for first-time setup, for scripting, and when every administrator is
locked out:

```bash
cd app
python3 -m bms.cli create-user --username jahnvi --display-name "Jahnvi" --admin
python3 -m bms.cli reset-password --username jahnvi
python3 -m bms.cli list-users
```

Passwords are prompted for without echoing. Non-interactively, one is generated
and printed. Minimum length is 10 characters when typed.

**Never delete an account.** Disable it. Deleting it would orphan the audit rows
and log entries that name it.

---

## 4. The client master

Everything about who the client is lives in the database, so BMS changes it
without a code release. Four levels:

```
Client                       ACME Group LLC            [code ACME]
 ├─ Sub-group                Abu Dhabi Operations       (emirate: Abu Dhabi)
 │                           Dubai Operations           (emirate: Dubai)
 ├─ Legal entity             ACME Trading LLC           (contract literal: ACME TRADING L.L.C.)
 └─ Policy                   insurer, network, policy no., category,
                             addition template, deletion template
```

**The emirate on the sub-group is not decoration.** It drives the deletion
effective date: Abu Dhabi deletions take the processing date, Dubai deletions
take cancellation + 30 days. A sub-group with no emirate cannot have that rule
applied. Accepted spellings are `Abu Dhabi`/`AUH`, `Dubai`/`DXB`, and
`Northern Emirates`/`NE`/`Sharjah`.

**The contract literal matters too.** Insurer workbooks have a dropdown of
contract names and the value must match one exactly. `contract_name` on the
legal entity is that exact literal, which is often not the entity's ordinary
name. Where they are the same, leave it blank.

### Entering it by hand

**Admin → Clients → Add client**, then open the client and add its sub-groups,
legal entities and policies. This is the right route for one client or a small
correction.

### Importing from Excel

**Admin → Clients → Import**, or:

```bash
python3 -m bms.cli import-clients master.xlsx --actor jahnvi
```

The first worksheet is read. Column headers are matched generously — the
import will not reject a file because someone wrote "Company Name" instead of
"Client":

| Field | Accepted headers |
| --- | --- |
| Client | `Client`, `Client Name`, `Company`, `Company Name`, `Group`, `Group Name` |
| Code | `Code`, `Client Code` |
| Sub-group | `Sub-Group`, `Sub Group`, `Subgroup`, `Sub-Group Name`, `Sub Group Name` |
| Legal entity | `Legal Entity`, `Entity`, `Entity Name`, `Legal Entity Name` |
| Contract literal | `Contract Name`, `Contract`, `Policy Name` |
| Insurer | `Insurer`, `Insurer/TPA`, `Insurer / TPA`, `TPA` |
| Network | `Network` |
| Policy no. | `Policy No`, `Policy No.`, `Policy Number`, `Policy` |
| Category | `Category`, `Cat`, `Plan/Category`, `Plan` |
| Emirate | `Emirate`, `Emirates`, `Region` |
| Addition template | `Addition Template`, `Addition Template Key`, `Addition` |
| Deletion template | `Deletion Template`, `Deletion Template Key`, `Deletion` |

One row may carry all four levels; repeated values are recognised rather than
duplicated. The import is **additive** — it creates what is missing and leaves
everything else alone. It never deletes.

Afterwards you get a count of what was created and a line for every row that was
skipped and why. Read the skipped list; a silently missing client surfaces later
as a case nobody can create.

```
read 240 row(s): 12 client(s), 31 sub-group(s), 44 legal entity(ies), 96 policy(ies) created
  skipped: row 118 has no client name
```

Verify with `python3 -m bms.cli list-clients`.

---

## 5. Templates

Nine registered workbooks, all read-only. The platform copies a master and
writes into the copy; it never writes to `BMS_TEMPLATE_ROOT`.

| Key | Workbook | Entry sheet | First data row |
| --- | --- | --- | --- |
| `nas.addition.aldar.v1` | NAS ALDAR Addition | `Sample Template` | 2 |
| `nas.addition.iffco.v1` | NAS IFFCO Addition | `Sample Template` | 2 |
| `nas.addition.hr.v1` | NAS HR Addition | `Sample Template` | 2 |
| `nas.deletion.v1` | NAS DeleteEmployees | `Sample Template` | 2 |
| `adnic.enrolment.v1` | ADNIC Member Enrollment | `Member Enrollment` | 2 |
| `adnic.termination.v1` | ADNIC Member Termination | `Member Termination` | 2 |
| `sukoon.addition.v1` | Sukoon Member addition | `Members` | 2 |
| `daman.addition.v1` | DAMAN Addition (`.xlsm`) | `Member Details` | 3 |
| `bms.log.2026` | New Log Format -2026 | `MAIN DATA` | 2 |

Two need Microsoft Excel to finish: `bms.log.2026` and `daman.addition.v1` both
carry live formulas. On a host without Excel the files are written correctly with
formulas intact but uncalculated, the export is recorded as
`recalculated: false`, and Excel calculates on first open.

**Daman is the one with real prerequisites.** It is a macro-enabled workbook
with a password-locked VBA project, SHA-512 sheet protection, 18 tables and a
Microsoft Purview label. The platform populates a byte-copy and re-asserts all of
that afterwards. It needs a Windows host with Excel — and BMS still owes itself
the written permission from Daman to populate their template programmatically
(recorded as outstanding in the discovery answers).

Adding a tenth template is three data edits and no processing code; the recipe
is in [PROTOTYPE_SETUP.md](PROTOTYPE_SETUP.md#adding-another-insurer-template).

### Verifying the masters

```bash
cd app && python3 tools/verify_sources.py
```

Checks every supplied file against `00_START_HERE/SHA256SUMS.txt`. Run it after
any change to `BMS_TEMPLATE_ROOT` and after restoring from backup. Expected:

```
Verified: 12 immutable verified, 15 advisory verified (of 27 manifest entries)
```

A mismatch on an immutable entry means a master workbook has been altered.
Restore it before processing anything — the structural fingerprints are taken
from these files.

---

## 6. Health checks

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "database": "postgresql",
  "ocr_engines": ["plain_text"],
  "virus_scanning": false, "engine": null,
  "excel_recalculation": false,
  "reason": "host is Linux, and Excel automation requires Windows",
  "templates_requiring_recalculation": ["bms.log.2026", "daman.addition.v1"]
}
```

The point of this endpoint is that a missing capability is **visible before
anyone depends on it**, rather than discovered at submission time.

| Missing | What happens | What you should do |
| --- | --- | --- |
| Tesseract | Scans are marked `ocr_unavailable` and raised for manual entry. Nothing is guessed | Install `tesseract-ocr` and `tesseract-ocr-ara`, plus `requirements-optional.txt` |
| ClamAV | Uploads are recorded `unavailable` — never `clean` — and raise a warning flag | Install ClamAV and keep its definitions current |
| Excel | The log and Daman are written with formulas intact but uncalculated | Move to a Windows host with Excel 365 if Daman is in scope |

Check `/health` after every install, upgrade and restore. It is the fastest way
to catch a host that has quietly lost a capability.

---

## 7. Retention

Documents and generated files are purged **36 hours after a case is closed**.
The clock starts at closure, so an open case keeps its evidence indefinitely.

**Kept forever regardless:** case records, member rows, log entries, the audit
trail.

| Variable | Default | Effect |
| --- | --- | --- |
| `BMS_DOCUMENT_RETENTION_HOURS` | `36` | Hours after closure |
| `BMS_PURGE_ENABLED` | `true` | `false` keeps documents indefinitely |
| `BMS_PURGE_INTERVAL_MINUTES` | `60` | How often the built-in sweep runs. `0` disables it |

The sweep runs inside the application. To drive it from your own scheduler
instead, set the interval to `0` and run:

```bash
cd app && python3 -m bms.cli purge
```

A sweep that fails is logged and the loop continues — one bad run does not stop
retention working tomorrow.

Downloading an already-purged export returns a clear "purged under retention"
message rather than a broken file.

---

## 8. The audit trail

**Audit** in the top navigation, filterable by case. Every row carries the
actor, the action, the entity, the field, the old value, the new value and the
timestamp.

What is recorded: sign-ins, case creation, uploads, processing runs, every
field correction, approvals, exports, downloads, closure, reopening, the
post-submission log events, account changes and client-master imports.

Two properties worth knowing:

- **It is append-only.** There is no edit or delete path in the application.
- **No-op edits are not recorded.** Saving a field unchanged writes nothing, so
  the trail shows real changes rather than every time someone pressed Save.

The trail is never purged.

---

## 9. Monthly routine

| When | Task |
| --- | --- |
| Monthly | Refresh the employee and insured-member masters — `import-clients` for client structure, and re-import the current member master |
| Monthly | `cd app && python3 tools/verify_sources.py` — confirm no master workbook has drifted |
| Monthly | Review `list-users`; disable anyone who has left |
| Monthly | Restore drill — see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) |
| Weekly | `curl /health` — confirm OCR, scanning and Excel are still as expected |
| Weekly | Check the disk under `BMS_DATA_ROOT` |
| After any upgrade | `python3 -m alembic upgrade head`, then `/health`, then the test suite |

The client master import is additive and safe to re-run. It will not delete a
client that has dropped out of the spreadsheet — remove those by hand, and only
once no open case refers to them.

---

## 10. Troubleshooting

**`ModuleNotFoundError: No module named uvicorn`** (or `alembic`, or `fastapi`).

You are running the system Python instead of the platform's own. The virtual
environment holds the dependencies, and a bare `uvicorn` only finds them if that
environment is activated in the terminal you are typing into — which it is not in
a freshly opened one.

Use the launcher, which never has this problem:

```bash
./deploy/start-linux.sh                                          # Linux
deploy\START-WINDOWS.bat                                        # Windows
```

Or address the environment's Python explicitly:

```bash
cd app && .venv/bin/python -m uvicorn bms.web.app:app --host 127.0.0.1 --port 8000
```

If that still fails, the environment really is incomplete — re-run
`deploy/install.sh` (or `install.ps1`). Deleting `app/.venv` first forces a
clean rebuild.

**Everyone is logged out after every restart.**
`BMS_SECRET_KEY` is not set, so a new signing key is generated at each start. Set
it permanently.

**A user cannot sign in and you are sure the password is right.**
Check `list-users` for `disabled`. Re-enable, or reset the password.

**Every administrator is locked out.**
Use the CLI on the host: `python3 -m bms.cli create-user --username rescue
--admin`. It needs filesystem and database access, not a session.

**An export is blocked and the user is stuck.**
That is the design. Open the member carrying the critical flag and correct the
data. There is no override and adding one would defeat the guarantee.

**An export failed with a structural drift error.**
The generated workbook did not match its master's fingerprint. Do not upload it.
Run `verify_sources.py` — the usual cause is that the master itself was modified.

**Scanned documents are all coming through as `ocr_unavailable`.**
Tesseract is not installed or `BMS_TESSERACT_CMD` points nowhere. Confirm with
`/health`; on Windows give the full path to `tesseract.exe`.

**The log workbook opens with empty `TAT` and `SUMMARY`.**
Expected on a host without Excel. The formulas are there and Excel calculates on
open. `/health` will say so with a reason.

**Uploads are rejected as too large.**
Raise `BMS_MAX_UPLOAD_BYTES` or `BMS_MAX_ARCHIVE_MEMBERS`. Both exist to stop one
upload exhausting the server; raise them deliberately, not reflexively.

**The database is out of step after an upgrade.**
`python3 -m alembic upgrade head`. The migrations run on SQLite and PostgreSQL
alike and are reversible.

---

## Still open with BMS

Two business rules could not be implemented because the rule itself has not been
supplied. Both are validated as far as the available information allows, and
neither is guessed at:

1. **The Emirates ID check-digit algorithm.** The platform validates the
   `784-YYYY-NNNNNNN-N` structure. It cannot confirm the final digit is
   arithmetically correct, so a structurally valid but mistyped number passes.
2. **Deletion-reason mapping.** There is no supplied mapping from a client's
   wording to the insurers' fixed reason lists. `Others` is used, with the
   client's wording in free text where the template allows it.

Supply either rule and it becomes a configuration change, not a rewrite.
