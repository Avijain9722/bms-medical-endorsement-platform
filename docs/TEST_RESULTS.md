# Test results

The recorded run of the automated suite for the delivered build.

```
297 tests · 0 failures · 0 errors · 0 skipped · 187.0s
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
| Date | 2026-08-06 12:20 UTC |
| Commit | `c13b35f` on `claude/bms-install-version-check` |
| Python | 3.11.15 |
| Platform | Linux x86-64, glibc 2.39 |
| Database | SQLite (per-test temporary files) |
| Key packages | FastAPI 0.141.1 · Starlette 1.4.0 · SQLAlchemy 2.0.51 · Alembic 1.19.0 · Pydantic 2.13.4 · Jinja2 3.1.6 · pytest 9.1.1 |
| Tesseract | 5.3.4, bundled at `vendor/tesseract/` — see §4 |
| ClamAV | **not installed** — deliberate, see §4 |
| Microsoft Excel | **not available** — Linux host, see §4 |
| Result | **297 passed, 0 failed, 0 errored, 0 skipped** |

Zero skips is the number to look at. Nothing was quietly stepped over because
the host lacked something; where a capability is missing, the test asserts the
*degradation* instead of being skipped.

### OCR was exercised for the first time

Earlier runs had no OCR engine on the host, so only the *absence* path was
covered. This run bundled Tesseract 5.3.4 into `vendor/tesseract/` and confirmed:

- the platform locates a bundled copy with no configuration at all;
- it sets `TESSDATA_PREFIX` so the bundled language data is found;
- a generated test image was read through `TextPipeline` and returned text with
  `source: tesseract`;
- `eng`, `ara` and `osd` language data all load.

Six tests were added to cover the resolution order, the language-file handling
and the absolute-path detection. Later work on paging, page weight and the
export-refusal branches took it to 267, and the stabilisation pass to 297.

### What the stabilisation pass added

Eleven tests, each confirmed to fail against the code before it:

- sign-out submitted with only the fields the rendered page actually contains,
  which is what the previous CSRF test could not see;
- every POST form checked for a token *inside its own tags*, not merely
  somewhere in the same file;
- an error message that quotes user input reflected as escaped text, not markup;
- the administrator client-master import bounded like every other upload;
- the content-security policy refusing inline script, and no template carrying
  any;
- the stylesheet and script served as cacheable files that answer 304;
- a case reference not reissued after a case is deleted, and a collision retried
  rather than raised;
- both export paths leaving no scratch workbook behind;
- a refused export leaving no half-written record;
- the refusal screen and the case screen agreeing on export order;
- reprocessing issuing a query count that does not grow with the batch.

### Also run on the deployment target

The Windows package pins Python 3.14, so the suite is run there too rather than
only on the development host's 3.11. Locally, on a 3.14 interpreter with
`DeprecationWarning` promoted to an error:

```
297 passed
```

CI runs the same suite on `windows-latest` under both 3.12 and 3.14, and
separately runs `install.ps1`, `verify.ps1` and the live platform on 3.14 —
because a green suite says nothing about whether the installer works. Every
commit on the branch runs all five jobs.

The local run and the CI run agreeing is what rules out anything specific to the
development container.

## 2. By area

| Tests | Time | Module | What it covers |
| ---: | ---: | --- | --- |
| 48 | 58.9s | `test_web.py` | The web tier through the real ASGI app — routing, templates, session cookie, paging, page weight, the CSRF guard, the content-security policy and every export-refusal branch |
| 46 | 1.6s | `test_all_templates.py` | Every registered insurer template generated end to end. Each binding's columns must exist in the real workbook, each literal must be one that template's own dropdowns accept, and the output must still match its fingerprint |
| 41 | 28.7s | `test_template_preservation.py` | The central promise: a generated portal file differs from its master only in the member rows deliberately written |
| 30 | 10.4s | `test_pipeline.py` | End-to-end case processing against a real database and the real workbooks, including reference allocation and the query cost of reprocessing |
| 29 | <0.1s | `test_ocr.py` | Deterministic field extraction, document classification, and locating a Tesseract bundled inside the project |
| 28 | 78.7s | `test_logbook_and_package.py` | The operational log workflow — statuses, date-range export, post-submission events — and supporting-document ZIP packaging with its manifest |
| 27 | <0.1s | `test_matching_and_rules.py` | Member grouping, principal/dependant linkage and every validation rule |
| 17 | 5.7s | `test_infrastructure.py` | Virus scanning, Excel recalculation, the purge scheduler and Alembic migrations |
| 17 | 3.0s | `test_master_and_admin.py` | Client master, administration and the login mechanism |
| 14 | <0.1s | `test_intake.py` | Pasted-instruction parsing and archive expansion |
| **297** | **187.0s** | | |

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
| **OCR accuracy on real scans** | No real client documents to test against — only synthetic data was used | Tesseract 5.3.4 is now present and was exercised end to end: a generated test image was read through the full pipeline. What remains unproven is *accuracy against genuine scans*, not whether OCR runs. The pipeline also still degrades honestly when no engine is installed — scans are marked `ocr_unavailable` and raised for manual entry, never guessed at |
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
