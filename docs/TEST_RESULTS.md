# Test results

The recorded run of the automated suite for the delivered build.

```
246 tests · 0 failures · 0 errors · 0 skipped · 152.72s
```

Reproduce it with:

```bash
cd app
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

---

## 1. The run

| | |
| --- | --- |
| Date | 2026-08-05 17:02 UTC |
| Commit | `b590ae1` on `claude/bms-phase1-prototype` |
| Python | 3.11.15 |
| Platform | Linux x86-64, glibc 2.39 |
| Database | SQLite (per-test temporary files) |
| Key packages | FastAPI 0.141.1 · Starlette 1.4.0 · SQLAlchemy 2.0.51 · Alembic 1.19.0 · Pydantic 2.13.4 · Jinja2 3.1.6 · pytest 9.1.1 |
| Tesseract | **not installed** — deliberate, see §4 |
| ClamAV | **not installed** — deliberate, see §4 |
| Microsoft Excel | **not available** — Linux host, see §4 |
| Result | **246 passed, 0 failed, 0 errored, 0 skipped** |

Zero skips is the number to look at. Nothing was quietly stepped over because
the host lacked something; where a capability is missing, the test asserts the
*degradation* instead of being skipped.

## 2. By area

| Tests | Time | Module | What it covers |
| ---: | ---: | --- | --- |
| 46 | 1.7s | `test_all_templates.py` | Every registered insurer template generated end to end. Each binding's columns must exist in the real workbook, each literal must be one that template's own dropdowns accept, and the output must still match its fingerprint |
| 41 | 32.8s | `test_template_preservation.py` | The central promise: a generated portal file differs from its master only in the member rows deliberately written |
| 28 | 91.5s | `test_logbook_and_package.py` | The operational log workflow — statuses, date-range export, post-submission events — and supporting-document ZIP packaging with its manifest |
| 27 | 11.8s | `test_pipeline.py` | End-to-end case processing against a real database and the real workbooks |
| 27 | <0.1s | `test_matching_and_rules.py` | Member grouping, principal/dependant linkage and every validation rule |
| 21 | <0.1s | `test_ocr.py` | Deterministic field extraction and document classification |
| 17 | 5.0s | `test_master_and_admin.py` | Client master, administration and the login mechanism |
| 15 | 6.1s | `test_infrastructure.py` | Virus scanning, Excel recalculation, the purge scheduler and Alembic migrations |
| 14 | <0.1s | `test_intake.py` | Pasted-instruction parsing and archive expansion |
| 10 | 3.0s | `test_web.py` | The web tier through the real ASGI app — routing, templates, session cookie |
| **246** | **152.7s** | | |

`test_logbook_and_package.py` and `test_template_preservation.py` account for
four-fifths of the runtime because they open, populate and re-fingerprint real
workbooks rather than fixtures.

## 3. What the suite actually proves

Each of these maps to a commitment made to BMS.

### Templates are not altered

- Every one of the nine registered workbooks is generated from its master and
  re-fingerprinted: sheet order and visibility, header values, hidden column
  ranges, inline **and** x14 data validations, defined names, table definitions,
  protection attributes, the VBA digest and the full OOXML part list. **Any
  delta fails the test**, which is the same check that blocks a live export.
- Bindings cannot name a column that does not exist in the real workbook.
- A value a binding writes must be a literal that template's own dropdown
  accepts. Where an insurer's dropdown genuinely cannot express a value — ADNIC
  has no `Parent` relation, Daman has no `Divorced` marital status — the platform
  refuses rather than approximating, and that refusal is tested.
- The Daman `.xlsm` copy keeps its password-locked VBA project, both SHA-512
  sheet protections, all 18 tables and its document labels.
- The log's formula columns — `TAT`, `Current Date`, `Pending with Insurer
  since`, `Aged Pending`, the `AC–AP` feeder block and all of `SUMMARY` — still
  contain formulas after generation, and there is no code path that can write
  into them.

### Blank means blank

The six post-submission columns — `Request Ref.No.`, `Request sent date to
Insurer`, `CARD #`, `Card receive and sent date to Insured`, `Saiba Voucher No.`
and `BBM Invoice date` — are asserted to be **genuinely empty cells**, not empty
strings, on a freshly exported log. They fill only through the matching explicit
workflow action, and an attempt to record `N/A`, `Pending`, `-`, `0`, `None`,
`Nil` or `TBC` into any of them is refused.

### Critical errors cannot be overridden

A member with an unresolved critical flag cannot be exported by any route,
including a direct POST to the export endpoint. There is no override parameter
and no override endpoint — the test asserts the block, and the absence is
structural.

### The business rules behave

- Effective date is the processing date; a retroactive date is a critical error.
- Abu Dhabi deletions take the processing date; Dubai deletions take
  cancellation + 30 days.
- Deleting a principal pulls in their dependants without listing them.
- `111111` fills a newborn's unavailable mandatory field but is a **critical
  error** in an Emirates ID.
- A single-name member gets `....` in Last Name.
- Duplicate members within a case are detected and block export.

### Nothing is held in the browser

A case created, left mid-processing and read back through a fresh database
session retains its files, members and state. The retention purge fires only
after closure and only past the configured window, and it never removes the case
record, member rows, log entries or audit trail.

### The audit trail is honest

Every field correction records the old value, the new value, the actor and the
timestamp. A save that changes nothing writes no audit row — tested explicitly,
so the trail shows real changes rather than every button press.

### Filtering the log does not write to it

Added after a UI check ambiguously appeared to change an entry while filtering.
It was not a defect — browsers scope form submissions — but it was an untested
path. There are now two tests: one that the status editor saves through the real
UI, and one that filtering by status leaves every entry byte-identical.

### Migrations match the models

`alembic upgrade head` on an empty database produces exactly the tables the
model metadata describes, so a fresh deployment and a migrated one are the same
schema. `downgrade base` removes them all again.

## 4. What this run did not exercise

Stated plainly, because a green suite is not the same as a proven deployment.

| Not exercised | Why | What is proven instead |
| --- | --- | --- |
| **OCR accuracy on real scans** | No Tesseract on this host, and no real client documents to test against — only synthetic data was used | That the pipeline degrades honestly: scans are marked `ocr_unavailable` and raised for manual entry, never guessed at. The extraction rules themselves are tested against supplied text, including MRZ parsing with correct ICAO 9303 check digits |
| **Excel recalculation** | Requires a Windows host with Excel; this run was on Linux | That the absence is detected and reported with a reason, that exports record `recalculated: false`, and that the files are written with formulas intact so Excel calculates on open |
| **Virus scanning against a real signature** | No ClamAV on this host | That an infected verdict blocks the upload and never reaches storage (tested with an injected verdict), and that an absent scanner reports `unavailable` — never `clean` — and raises a warning flag |
| **A live insurer portal upload** | Requires the insurer portals and real credentials | That the generated workbook is structurally identical to the master the portal expects. Manual acceptance against the live portals remains a BMS step |
| **PostgreSQL** | The run used SQLite | The schema is written portable — explicit string lengths, timezone-aware timestamps, dialect-neutral JSON, string UUID keys — and the same migrations run on both. Worth re-running the suite against PostgreSQL on the production host |
| **The Emirates ID check digit** | BMS has not supplied the algorithm | Structural validation of `784-YYYY-NNNNNNN-N`. A structurally valid but mistyped number will pass |
| **Deletion-reason mapping** | BMS has not supplied a mapping | `Others` plus the client's wording in free text where the template allows |

The first three are **unverified, not unbuilt** — the code paths exist and their
failure modes are tested. Re-run the suite on the production Windows host with
Tesseract, ClamAV and Excel present to close them out.

## 5. Continuous integration

Both workflows are described in [CI_PIPELINE.md](CI_PIPELINE.md).

- **`tests.yml`** — the full suite on every push and pull request.
- **`reports.yml`** — on demand, via `workflow_dispatch`: template inventory,
  fingerprint baselines, coverage and the source verification report.

`workflow_dispatch` only appears in the GitHub UI once the workflow file exists
on the default branch. Until this branch merges, triggering it returns 404 — that
is GitHub's behaviour, not a broken workflow.

## 6. Verifying the source workbooks

Separate from the test suite, and worth running after any change to the template
root or any restore:

```bash
cd app && python3 tools/verify_sources.py
```

```
Verified: 12 immutable verified, 15 advisory verified (of 27 manifest entries)
```

A mismatch on an immutable entry means a supplied master has been altered.
Restore it before processing anything — the structural fingerprints are taken
from these files.
