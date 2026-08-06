# BMS Medical Endorsement Platform — working notes

Read this before changing anything. Several rules below look like ordinary
engineering preferences and are not: they are the reasons the platform is
correct, and each one was arrived at by finding out the hard way.

---

## What this is

An internal platform for BMS Masaood Insurance L.L.C. – O.P.C. A member of the
Medical Team pastes a client's email body, uploads their documents, and the
platform reads them locally, identifies each member, groups their documents,
applies the client's rules, and produces the insurer's portal workbook and the
approved BMS operational log — with member-level review in between.

Runs inside the BMS network. Nothing it processes leaves the host.

## The constraints — these are not negotiable

BMS stated each of these. Breaking one is a correctness or compliance failure,
not a style disagreement.

| Rule | What it means in code |
| --- | --- |
| **No mailbox integration** | The pasted `bms_comments` field is the only instruction input. No IMAP, no Graph, no `.eml` import — ever |
| **No external AI or document API** | OCR is local Tesseract. There is no HTTP client anywhere in `app/bms/` and there must not be one |
| **Templates are immutable** | Copy the master, write only inside the approved rectangle, re-fingerprint, block on any delta |
| **No invented values** | Missing information raises a flag. Never substitute a nearest match, a default or a placeholder |
| **No override of a critical error** | There is no override endpoint and there must not be one. The data gets corrected instead |
| **Post-submission columns stay blank** | Six log columns are nullable and null until staff perform the matching explicit action |
| **Synthetic or anonymised test data only** | Never commit real member data. The supplied log's 1,101 example rows are stripped at registration and never imported |

## Do not replace the OOXML layer with a spreadsheet library

This is the one most likely to be "helpfully" undone.

`app/bms/ooxml/` is hand-written against the raw ZIP and XML **on purpose**.
openpyxl was tried first and rejected: opening any supplied workbook emits

```
UserWarning: Data Validation extension is not supported and will be removed
```

**All six of the BMS log's dropdowns are x14 extension validations.** openpyxl
silently drops every one of them on save. The same class of loss applies to
Daman's password-locked VBA project, its two SHA-512 sheet protections, its 18
tables and its Purview label.

openpyxl *is* used in the tests, to verify generated files independently. That
is the only acceptable use of it in this repository.

## Preserve the defects in the supplied templates

The masters contain genuine mistakes. They are preserved deliberately, because
the insurer portals expect the files exactly as supplied:

- NAS `Departments` and `Grades` sheets are empty
- NAS `SubNat_Other` points at a blank cell
- NAS Work Region validation starts at row 3 while data starts at row 2
- NAS Visa Type has no dropdown
- NAS maps `Lymphatic system conditions` to `PREG131`
- ADNIC `WorkLocation` resolves to `#REF!`
- Daman ships a pre-broken `INDIRECT(SUBSTITUTE(#REF!,…))` and a pre-seeded `AK3`

Fixing any of these is a structural change, which is exactly what the
fingerprint blocks. If BMS wants them corrected, that is a conversation with the
insurers and a newly registered master — not a code change.

## Layout

```
app/bms/
  ooxml/       raw OOXML: package, sheet writer, structural fingerprint
  registry/    generate → re-fingerprint → block on drift
  templates/   TemplateSpec per workbook (where, which sheet, which rows)
  outputs/     ValueMap (how an insurer spells a value) + TemplateBinding
  intake/      pasted instructions, archives, virus scanning
  ocr/         local text extraction, classification, field rules
  matching/    document→member grouping, principal linkage
  validation/  the business rules, as severity-flagged exceptions
  web/         FastAPI + Jinja2, server-rendered, no client framework
app/tests/     the suite — run it before claiming anything works
docs/          eleven manuals; docs/README.md indexes them
deploy/        installers, launchers, verify scripts
00_ .. 05_     supplied material. READ-ONLY. Never write here
```

## Adding an insurer template

Three data edits, no processing code:

1. `templates/specs.py` — a `TemplateSpec`
2. `outputs/mapping.py` — a `ValueMap`, including values the insurer's dropdown
   **cannot** express, which are refused rather than approximated
3. `outputs/bindings.py` — a `TemplateBinding`, plus deliberate blanks and why

The suite picks new entries up automatically.

## Conventions

- **Comments explain why, never what.** The code says what it does.
- **Refuse rather than approximate.** ADNIC has no `Parent` relation and Daman
  no `Divorced` marital status; both raise `UnsupportedValue`.
- **Degrade visibly.** No Tesseract, ClamAV or Excel is fine — but `/health`
  must say so, and an absent virus scanner reports `unavailable`, never `clean`.
- **Every list screen is paged.** The log grows forever; an unpaged screen once
  reached 3.1 MB and 57,081 DOM elements.
- **Index new foreign keys.** Twenty-four of them once carried four indexes
  between them, and every case screen full-scanned five tables.

## Before you say it works

```bash
cd app
python -m pytest tests -q          # the whole suite, currently 300
python tools/verify_sources.py     # supplied workbooks unaltered
```

Both must pass. And note what the suite does **not** prove — every test imports
the application directly, so nothing exercises the commands the manuals print.
Two real defects reached users through that gap: a documented `uvicorn` command
that failed in a fresh terminal, and a shipped default administrator password.
**Run the thing the way a user runs it.**

## Still open with BMS

Do not invent answers to these:

1. **The Emirates ID check-digit algorithm.** The format `784-YYYY-NNNNNNN-N`
   is validated; the final digit cannot be confirmed arithmetically.
2. **Deletion-reason mapping** from a client's wording to each insurer's fixed
   list. `Others` plus free text until BMS supplies one.
3. **Daman's written permission** to populate their template programmatically —
   recorded as not currently held.

## Known limits

- The **Windows path** is now covered by CI (`windows-install` in
  `.github/workflows/tests.yml`). Before that job existed it was entirely
  unverified — keep it green.
- **OCR accuracy** on real scans is unproven; only that OCR runs.
- **Login throttling is in-process memory**: correct for one instance, but it
  will not coordinate across several.
