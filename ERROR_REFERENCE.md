# Error reference

Every way the platform can refuse or fail, what it means, and who fixes it.

Written so that a message on screen can be looked up without reading the code.
Validation codes for individual members are in
[USER_MANUAL.md §5](USER_MANUAL.md#5-exception-codes); this covers everything
else.

---

## 1. The rule this all follows

The platform refuses rather than approximates. Every entry below stops something
happening, and none of them can be overridden — the underlying condition has to
be corrected. That is deliberate: a workbook that reaches an insurer with a
plausible-but-wrong value is worse than one that was never sent.

**Nothing on this page means a submission went out in a bad state.** All of them
happen before anything leaves the platform.

---

## 2. HTTP responses

| Code | When | Who fixes it |
| --- | --- | --- |
| **303** | Normal redirect after a successful action, and the redirect to `/login` when not signed in | Nobody — expected |
| **400** | The request was understood but cannot be acted on. See §3 | The person making the request |
| **403** | `Administrator access is required.` — a processor opened an admin screen | An administrator, if the person should have that access |
| **403** | *This form was not submitted from a current session* — the CSRF token was missing, stale or from another session. Almost always a page left open past a sign-out, or a resubmitted back-button form. Sign in again and retry. If it happens unprompted, tell an administrator | Sign in again |
| **404** | Case, member, file, export, log entry, user or client not found. Usually a stale link or an id typed by hand | Nobody — navigate again |
| **410** | `This export has been purged under retention.` The record survives; the file is gone. Re-export if the case is still open | Re-export, or accept it |
| **500** | Something unanticipated. See §5 | An administrator, from the server log |

## 3. 400 — rejected requests

| Message | Meaning |
| --- | --- |
| `A client is required.` | A case cannot exist without one |
| `An insurer is required.` | Same |
| `No file was uploaded.` | The upload form was submitted empty |
| `Nothing was entered for this event.` | A post-submission action was recorded with every field blank. Blank is the correct state until the value exists, so nothing is written |
| `'<name>' is not a recognised event` | Only the five defined workflow events can be recorded. A hand-edited form |
| `Choose a password of at least 10 characters.` | Minimum length for a typed password |
| `User '<name>' already exists.` | Usernames are unique. Reset the password instead |
| `Could not read the workbook: …` | A client-master import that is not a readable `.xlsx`, or has no recognisable header row |

Placeholder refusals also surface as 400. Recording `N/A`, `Pending`, `-`, `0`,
`None`, `Nil` or `TBC` into a post-submission column is rejected: those columns
must stay genuinely blank until the real value exists.

## 4. Export refusals

These four are the important ones. Each is shown on the case screen with an
explanation — **none of them reaches the browser as a bare 500**, which was the
behaviour before the branches below were added.

| Refusal | What it means | Who fixes it |
| --- | --- | --- |
| **`ExportBlocked`** | A member has an unresolved **critical** flag, or no member has been approved | The Medical Team, by correcting the member data. There is no override |
| **`StructuralDrift`** | The generated workbook no longer matches the master template — a sheet, header, hidden column, dropdown, named range, table or protection setting moved. **Not a data problem** | An administrator. Usually the master workbook itself was modified; run `verify_sources.py` |
| **`UnsupportedValue`** | The insurer's own dropdown cannot express a value on this case — ADNIC has no `Parent` relation, Daman has no `Divorced` marital status. The platform will not substitute a nearest match | BMS, by agreeing the correct value with the insurer |
| **`UnknownField`** | A template binding names a column that does not exist in that workbook. A configuration error, not a data one | An administrator or developer |

`StructuralDrift` is the one to take seriously. It means the safety check that
guarantees a generated file is structurally identical to the insurer's master has
failed. **Do not upload the file.**

## 5. 500 — unexpected errors

Anything not anticipated is caught, logged with a full traceback on the server,
and shown to the operator as:

> Something went wrong. The error has been recorded. Nothing was submitted to an
> insurer. Quote reference `a1b2c3d4e5f6` to your administrator.

The browser never receives a stack trace. That is a security decision as much as
a usability one — a traceback discloses file paths, library versions and
fragments of the query that caused it to whoever triggered it, and an error page
is reachable by anyone who can reach the login screen.

To investigate, search the server log for the reference:

```bash
journalctl -u bms-endorsements | grep a1b2c3d4e5f6      # systemd
findstr a1b2c3d4e5f6 C:\bms-endorsements\logs\service.log
```

## 6. Degradations — reported, never silent

These are not errors. They are capabilities the host lacks, and each one is
visible at `/health` rather than being discovered at submission time.

| Condition | Effect | Reported as |
| --- | --- | --- |
| No Tesseract | Scans are flagged for manual entry; nothing is guessed | `ocr_unavailable` on the document; `ocr_engines` at `/health` |
| No ClamAV | Uploads recorded as **unscanned**, never as clean | `not_virus_scanned` warning flag; `virus_scanning: false` |
| No Excel | Log and Daman formulas left uncalculated; Excel computes them on open | `recalculated: false` on the export, with a reason |
| No PDF reader | That document falls through to the next engine, or to manual entry | Absent from `ocr_engines` |

The distinction that matters: an absent virus scanner reports `unavailable`, and
**never** `clean`. Unscanned must never look like scanned.

## 7. Startup and installation

| Message | Cause |
| --- | --- |
| `ModuleNotFoundError: No module named uvicorn` (or `alembic`, `fastapi`) | The system Python is being run instead of the platform's. Use `deploy/start-linux.sh` / `START-WINDOWS.bat`, or `app/.venv/bin/python -m uvicorn …` |
| `Python 3.11 or newer is required` | The installer found an older interpreter |
| `no offline bundle -- downloading from PyPI` | `deploy/wheelhouse/` is missing. Fine with internet; fatal without |
| `The virtual environment is missing its dependencies.` | Installation did not complete. Re-run the installer; delete `app/.venv` first for a clean rebuild |
| Everyone signed out after every restart | `BMS_SECRET_KEY` is unset, so a new signing key is minted each start |

## 8. What is deliberately *not* an error

| Situation | Why it is correct |
| --- | --- |
| A blank post-submission column | It stays blank until someone performs the matching action. Blank is the answer, not a gap |
| `....` in Last Name | The recorded convention for a single-name member |
| `111111` in a mandatory newborn field | The agreed placeholder — but **never** in an Emirates ID, where it is a critical error |
| An empty `Departments` or `Grades` sheet | The supplied template ships empty. Preserved, not fixed |
| `#REF!` in ADNIC `WorkLocation` | A defect in the supplied master. Preserved, because the portal expects the file as supplied |
| A member flagged `no_supporting_documents` | Either the documents were not sent, or the member was read out of the email in error. Worth checking, not a fault |

## 9. Where each is enforced

| Layer | Refuses |
| --- | --- |
| `bms/validation/rules.py` | The 16 member validation codes |
| `bms/pipeline.py` | `ExportBlocked` — critical flags, nothing approved |
| `bms/registry/generate.py` | `StructuralDrift`, `UnknownField` |
| `bms/outputs/mapping.py` | `UnsupportedValue` |
| `bms/outputs/log.py` | `NotRecordable` — placeholders in post-submission columns |
| `bms/logbook.py` | `NotPermitted` — writing a column the operator does not own |
| `bms/web/app.py` | HTTP codes, and the catch-all that keeps tracebacks off the page |
