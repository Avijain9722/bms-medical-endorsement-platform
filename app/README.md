# BMS Medical Endorsement Platform — application

Locally hosted platform for processing corporate medical insurance endorsement
requests. See `/root/.claude/plans/` for the approved plan, and
`00_START_HERE/MASTER_DEVELOPMENT_PROMPT.md` for the governing requirements.

The source workbooks in `02_PORTAL_TEMPLATES/` and `03_INTERNAL_LOG_TEMPLATE/`
are immutable inputs. Nothing in this application writes to them.

## What is built so far

**Phase 2 — template registry, fingerprinting and the OOXML surgical writer.**
This is the riskiest part of the system, so it was built and proven first.

| Module | Responsibility |
| --- | --- |
| `bms/ooxml/package.py` | Reads a workbook as ordered ZIP parts and writes it back with part order, compression and ZIP metadata intact. Refuses to add or remove parts. |
| `bms/ooxml/fingerprint.py` | Structural fingerprint: sheet order and visibility, headers, hidden columns, every inline and x14 validation, defined names, tables, protection, VBA digest, part list. Plus a readable `diff`. |
| `bms/ooxml/sheet.py` | Cell-level writes into `sheetData` only. Preserves the document's own namespace prefixes so no other element is rewritten. |
| `bms/templates/specs.py` | The registered templates, with the column map, header row and first input row read out of each workbook. |
| `bms/registry/generate.py` | Generates from a fresh copy of the master, then re-fingerprints and blocks release on any structural delta. |

## Why not openpyxl

openpyxl cannot round-trip these files. Reading the BMS log with it emits:

```
UserWarning: Data Validation extension is not supported and will be removed
```

Every MAIN DATA dropdown is an x14 extension validation, so a save would silently
drop all six of them. The Daman workbook adds a password-locked VBA project,
SHA-512 sheet protection, 18 tables, four form controls and a Microsoft Purview
sensitivity label. The surgical writer touches `sheetData` and nothing else, so
all of it survives byte-for-byte — verified by test.

## Running the checks

```bash
cd app

python3 tools/verify_sources.py     # supplied files unchanged?
python3 -m pytest tests/ -q         # 41-test preservation suite
python3 -m tools.reports all        # readable evidence, not pass/fail
```

Both checks also run automatically on every push and pull request, and the
reports can be requested from the Actions tab. See
[`docs/CI_PIPELINE.md`](../docs/CI_PIPELINE.md) for what each one covers and how
to read a failure.

41 tests run against the real supplied workbooks. The suite proves:

- a generated workbook has **zero structural delta** from its master, for all nine
  registered templates;
- no OOXML part is added, removed or reordered;
- the source workbooks are never modified;
- Daman's `vbaProject.bin`, `docProps/custom.xml`, form controls, printer settings
  and all 18 table parts are byte-identical after population;
- Daman writes start at row 3, below its instruction row and header row;
- NAS headers survive intact, including the load-bearing trailing space in
  `"Effective Date "` and the double space in `"Waived PEC  Declaration"`;
- the log's 1,101 example rows are stripped at registration and none reach output;
- the log's six post-submission columns are **genuinely empty cells** — never
  `N/A`, `Pending`, `-` or `0`;
- no field can be mapped onto a log formula or helper column;
- an unrecognised field raises rather than being silently dropped.

## Conventions that matter

- **Never invent a value.** An unknown field raises `UnknownField`. A structural
  change raises `StructuralDrift` and the output file is deleted.
- **Template defects are preserved, not fixed.** The empty NAS Department/Grade
  lookups, the `SubNat_Other` range pointing at a blank cell, the Work Region
  validation that starts one row late, ADNIC's `#REF!` named range and Daman's
  pre-broken `INDIRECT(SUBSTITUTE(#REF!,...))` all stay exactly as supplied.
- **Style prototypes.** New cells inherit the template's own format for their
  column; cells that already exist keep the style the template gave them.

## Not yet built

Phases 1 and 3–7 of the approved plan: the FastAPI application and PostgreSQL
schema, case intake with the BMS Comments paste field, upload and ZIP extraction,
local OCR and document classification, member matching and principal linkage, the
review and exception workflow, supporting-document ZIPs, the 36-hour post-closure
purge, and the Windows Excel recalculation worker used for the Daman and log
hand-off (`ENGINE_RECALC`).
