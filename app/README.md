# BMS Medical Endorsement Platform — application

Locally hosted platform for processing corporate medical insurance endorsement
requests. See `../CLAUDE.md` and `../00_START_HERE/MASTER_DEVELOPMENT_PROMPT.md`
for the governing requirements.

The source workbooks in `02_PORTAL_TEMPLATES/` and `03_INTERNAL_LOG_TEMPLATE/`
are immutable inputs. Nothing in this application writes to them.

## What is built so far

**The template registry, fingerprinting and OOXML surgical writer** — the
riskiest part of the system, built and proven first.

**The operational platform** — a working internal web application: pasted client
instructions, bounded multi-file and nested-ZIP upload, local OCR, member
identification and document grouping, member-level review and approval, persistent
case storage, all eight registered insurer workflows, supporting-document packages,
the approved New Log Format -2026 output, client-master administration, retention,
migrations, Windows deployment and an append-only audit trail.
See [`docs/PROTOTYPE_SETUP.md`](../docs/PROTOTYPE_SETUP.md) to install and run it.

| Module | Responsibility |
| --- | --- |
| `bms/ooxml/package.py` | Reads a workbook as ordered ZIP parts and writes it back with part order, compression and ZIP metadata intact. Refuses to add or remove parts. |
| `bms/ooxml/fingerprint.py` | Structural fingerprint: sheet order and visibility, headers, hidden columns, every inline and x14 validation, defined names, tables, protection, VBA digest, part list. Plus a readable `diff`. |
| `bms/ooxml/sheet.py` | Cell-level writes into `sheetData` only. Preserves the document's own namespace prefixes so no other element is rewritten. |
| `bms/templates/specs.py` | The registered templates, with the column map, header row and first input row read out of each workbook. |
| `bms/registry/generate.py` | Generates from a fresh copy of the master, then re-fingerprints and blocks release on any structural delta. |
| `bms/models.py`, `bms/db.py`, `bms/storage.py` | PostgreSQL-ready schema, session handling, content-addressed file storage on disk. |
| `bms/ocr/` | Pluggable local text extraction, rule-based field reading (MRZ with check digits, Emirates ID, UID, dates) and content-driven document classification. |
| `bms/intake/` | Pasted-instruction parsing and safe archive expansion. |
| `bms/matching/grouping.py` | Member identification, document grouping and principal linkage, each with a score and a reason. |
| `bms/validation/rules.py` | Exception and confidence rules. Critical findings block export and cannot be overridden. |
| `bms/outputs/` | Registered NAS, ADNIC, Sukoon and Daman rows, supporting packages, the BMS log, recalculation and per-template value maps. |
| `bms/pipeline.py` | Case orchestration: intake, analysis, member building, review, export, retention purge. |
| `bms/web/` | The internal application. Server-rendered, no state in the browser. |

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
python3 -m pytest tests/ -q         # full suite: preservation + application
python3 -m tools.reports all        # readable evidence, not pass/fail
```

Both checks also run automatically on every push and pull request, and the
reports can be requested from the Actions tab. See
[`docs/CI_PIPELINE.md`](../docs/CI_PIPELINE.md) for what each one covers and how
to read a failure.

**324 tests.** 41 of them run the preservation suite against the real supplied
workbooks; the rest cover extraction, classification, instruction parsing,
archive safety, grouping, the validation rules, the end-to-end case pipeline and
the web tier.

The preservation suite proves:

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

The application tests additionally prove that uploads survive a new database
session, that identical files are deduplicated, that an unreadable document is
flagged rather than silently skipped, that a member with a critical flag cannot
be approved, that export is blocked while any critical flag is open, that
corrections are audited with their previous value, and that the retention purge
removes documents only after closure while keeping the record.

## Operational limits

- Formula recalculation is implemented, but can only be performed on a Windows
  host with Microsoft Excel installed; elsewhere the export records why it was
  not recalculated and Excel calculates it when opened.
- Structural fidelity is automated. Final acceptance by each insurer's live
  portal still requires a controlled upload test outside this repository.
- Client configuration is centralised. Employee and category values remain part
  of the reviewed case/member record rather than separate master-data screens.
