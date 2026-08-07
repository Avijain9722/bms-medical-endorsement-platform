# CI pipeline — what runs, when, and how to read it

This document describes every automated check in the repository, in plain terms.
It is written to be read by anyone on the BMS team, not only by developers.

---

## Why there is a pipeline at all

The platform's central promise is narrow and absolute:

> A generated insurer workbook differs from the template BMS was given **only** in
> the member rows we deliberately wrote. Nothing else moves.

That promise is easy to break by accident. A single careless save through an
ordinary spreadsheet library silently deletes the dropdown lists on the BMS log,
or strips the macro project out of the Daman workbook. Neither failure is visible
by looking at the file — you find out when the insurer's portal rejects the
upload.

The pipeline exists to catch that automatically, on every change, before anyone
uploads anything.

---

## The two workflows at a glance

| Workflow | File | When it runs | What you get |
| --- | --- | --- | --- |
| **Tests** | `.github/workflows/tests.yml` | Automatically on every push and pull request. Can also be started by hand. | Pass/fail. Blocks a broken change from being merged. |
| **Reports** | `.github/workflows/reports.yml` | **Only when you ask for it**, from the Actions tab. | Readable evidence: inventories, structural diffs, and sample workbooks you can open in Excel. |

Nothing in the Reports workflow runs on a push. It exists purely so you can
request evidence on demand.

---

## Workflow 1 — Tests (automatic)

Runs four independent gates. All must pass.

### Gate 1: Source integrity

**Question it answers:** *have the files BMS supplied been altered?*

The eight insurer workbooks, the BMS log template and the brand assets are
controlled inputs. Every structural check in the platform is measured against
them, so if one of them quietly changes, every stored baseline becomes
meaningless — and the platform would keep reporting "passed" while comparing
against the wrong thing.

This gate recomputes the SHA-256 of every supplied file and compares it with the
manifest BMS shipped in `00_START_HERE/SHA256SUMS.txt`.

| Folder | Severity | If a file changes |
| --- | --- | --- |
| `01_BRAND_ASSETS` | **Immutable** | Build **fails** |
| `02_PORTAL_TEMPLATES` | **Immutable** | Build **fails** |
| `03_INTERNAL_LOG_TEMPLATE` | **Immutable** | Build **fails** |
| `00_START_HERE` | Advisory | Reported, build passes |
| `04_DEVELOPMENT_EXAMPLES` | Advisory | Reported, build passes |
| `05_TECHNICAL_REFERENCE` | Advisory | Reported, build passes |

**When it fails, and it was intentional** — for example BMS issues a new NAS
template — the correct response is not to disable the check. It is:

1. Replace the workbook.
2. Update its line in `00_START_HERE/SHA256SUMS.txt`.
3. Re-run the Tests workflow so a new structural baseline is recorded.

**When it fails and nobody meant to change anything**, something has modified a
controlled file. Investigate before merging.

### Gate 2: Full application and template-preservation suite

**Question it answers:** *does populating a workbook change anything it shouldn't?*

The full suite runs on Linux with Python 3.11 and on Windows with Python 3.12 and
the deployment target, Python 3.14. Within it, 41 tests run against the real supplied
workbooks. For each of the nine registered
templates the suite populates a fresh copy with synthetic member rows, then
re-reads the result and compares its structure with the original.

Deprecation warnings are promoted to errors so a dependency or standard-library
change cannot quietly become a Python 3.14 runtime failure. The same matrix also
runs the repository's reliability-focused Ruff rules and compiles every
production module before the job can pass.

It checks, among other things:

- sheet order and which sheets are hidden;
- every column header, including the load-bearing trailing space in
  `"Effective Date "` and the double space in `"Waived PEC  Declaration"`;
- hidden columns;
- every dropdown, including the x14 extension validations that drive all six
  dropdowns on the BMS log;
- defined names, tables, sheet and workbook protection;
- that no OOXML part is added, removed or reordered;
- that the source workbooks themselves are never touched;
- that Daman's macro project, sensitivity label, form controls and all 18 tables
  are **byte-identical** after population;
- that Daman's data starts on row 3, below its instruction row and header row;
- that the log's 1,101 example rows are stripped and never reach output;
- that the log's six post-submission columns come out as **genuinely empty
  cells** — never `N/A`, `Pending`, `-` or `0`.

**When it fails**, the run log names the exact structural difference, for example:

```
structural drift in daman.addition.v1:
  - fingerprint.sheets[0].validations[3].sqref: 'M3:M65536' -> 'M3:M1048576'
```

That is a real defect in the change being made, not a flaky test. The generated
file is deleted rather than released.

### Gate 3: Windows install and smoke test

On Python 3.14, CI runs `deploy/install.ps1` and `deploy/verify.ps1`, starts the
real Uvicorn application, checks `/health` and `/login`, and verifies that browser
security headers are present. This catches Windows path, launcher and installer
failures that unit tests alone cannot expose.

### Gate 4: Offline bundle and disconnected Windows install

CI builds the Python 3.14 Windows wheelhouse on Linux, checks the wheel metadata
for Windows-only dependencies, and transfers it to a clean Windows runner. That
runner installs with `--no-index`, verifies the application, runs the suite and
starts the real service while pip is unable to contact PyPI. This proves the
package delivered to an offline BMS host is complete, rather than merely proving
that an online development installation works.

---

## Workflow 2 — Reports (run on request)

Go to **Actions → Reports → Run workflow**, choose what you want, and press the
green button.

> **If "Reports" is not in the Actions list yet:** GitHub only offers a
> manually-triggered workflow once its file exists on the default branch. Until
> the pull request that introduces `reports.yml` is merged into `main`, the entry
> will not appear and the API returns `404`. This is a GitHub rule, not a problem
> with the workflow. In the meantime, run the same reports locally — see
> [Running everything locally](#running-everything-locally). The **Tests**
> workflow is unaffected and runs on branches straight away, because `push` and
> `pull_request` events do not have this restriction.

### Options

| Input | Meaning | Default |
| --- | --- | --- |
| **report** | Which report to produce: `all`, `inventory`, `preservation`, `blank-fields` or `sources`. | `all` |
| **rows** | How many synthetic member rows to write per template in the preservation report. | `3` |
| **attach_samples** | Attach the generated workbooks so you can open them in Excel. | on |

### What each report tells you

**`inventory`** — what the registry believes each workbook looks like, read fresh
from the file at report time. One summary table plus a detail block per template:
entry sheet, sheets in order with hidden ones marked, hidden columns, validation
count, defined names, tables, protection, part count and a fingerprint digest.

Use it to answer "what is actually in this template?" without opening Excel, and
to compare two variants — it is how the difference between the ALDAR, IFFCO and
NASHR NAS files was established.

**`preservation`** — populates every template from its master and reports the
structural delta. A passing row means zero differences beyond the cells written.
Includes a dedicated Daman section comparing 25 critical parts byte-for-byte.

**`blank-fields`** — writes one log row and then checks, cell by cell, that the
six post-submission columns are empty:

| Cell | Column |
| --- | --- |
| `G2` | Request Ref.No. |
| `Q2` | Request sent date to Insurer |
| `R2` | CARD # |
| `T2` | Card receive and sent date to Insured |
| `V2` | Saiba Voucher No. |
| `W2` | BBM Invoice date |

This is the check that guards the rule that these fields stay blank until staff
record the corresponding event.

**`sources`** — the same SHA-256 verification the Tests workflow runs, presented
as a readable table.

### Where the output appears

- **In the run summary page** — the report renders as formatted text. No
  downloading required.
- **As artifacts** — `report-<name>` contains the Markdown file;
  `sample-workbooks` contains the generated `.xlsx` and `.xlsm` files, kept for
  7 days.

The sample workbooks contain synthetic values only (`first_name-1`, and so on).
No client or member data is ever produced by this workflow.

---

## Running everything locally

You do not need GitHub to run any of this.

```bash
cd app

# Gate 1 — source integrity
python3 tools/verify_sources.py

# Gate 2 — the preservation suite (needs pytest)
python3 -m pip install pytest
python3 -m pytest tests -q

# Reports
python3 -m tools.reports inventory
python3 -m tools.reports preservation --rows 5 --samples ./samples
python3 -m tools.reports blank-fields
python3 -m tools.reports all --out report.md
```

Exit code `0` means passed, `1` means failed, so these can be wired into any
other job runner if BMS later moves off GitHub Actions.

---

## What was added, file by file

| File | Purpose |
| --- | --- |
| `.github/workflows/tests.yml` | The automatic gate: source integrity, the cross-platform full suite, and a Python 3.14 Windows install/smoke test. Also startable by hand. |
| `.github/workflows/reports.yml` | The on-demand reports. Manual trigger only, with a choice of report and a row count. |
| `app/tools/verify_sources.py` | Recomputes SHA-256 for every supplied file and checks it against the manifest. Immutable folders fail the build; docs and examples are advisory. |
| `app/tools/reports.py` | Produces the inventory, preservation and blank-field reports as Markdown. |

No application behaviour changed. These additions only observe and report on
what was already there.

---

## Reading a failure — quick guide

| Symptom | What it means | What to do |
| --- | --- | --- |
| `immutable source file changed` | A supplied workbook or brand asset was modified. | If deliberate, update `SHA256SUMS.txt` and re-run. If not, revert it. |
| `structural drift in <template>` | Populating the workbook altered something structural. | Read the named delta — it points at the exact sheet, validation or part. |
| `style at <cell>: new cell expected prototype N` | A written cell did not inherit the template's format for its column. | Usually a missing style prototype; the column's format would be wrong in Excel. |
| `no column for field '<name>'` | Code tried to write a field the template has no column for. | Add it to `app/bms/templates/specs.py` deliberately — never guess a destination. |
| `column X is a formula or helper cell` | Something tried to overwrite a calculated column on the BMS log. | The value must come from the workbook's own formula, not from us. |
| `must be genuinely blank until recorded` | A placeholder was supplied for a post-submission field. | Leave it unset. It is filled only when a user records the event. |

---

## Limits worth knowing

- The pipeline runs on Linux and Windows. The hosted Windows worker does not
  include Microsoft Excel, so the COM recalculation path is exercised through
  its deterministic availability/fallback tests. An end-to-end recalculation
  still needs a BMS Windows host with Excel installed.
- The preservation suite proves *structural* fidelity. It does not prove an
  insurer's portal will accept the file — only a real upload does that.
- Reports use synthetic data. They demonstrate mechanics, not real case output.
