# Architecture

The design record: what was built, how it fits together, and — more usefully —
why each significant decision went the way it did.

Written for whoever maintains this after the original build. If you are looking
for how to *use* the platform see [USER_MANUAL.md](USER_MANUAL.md); to run it,
[ADMIN_MANUAL.md](ADMIN_MANUAL.md).

---

## 1. The shape of it

```
   Browser (inside the BMS network only)
        │  server-rendered HTML, no SPA, no client-held state
        ▼
   FastAPI + Jinja2 ── HMAC-signed session cookie
        │
        ├── pipeline.py ......... the case journey, one function per step
        │      │
        │      ├── intake/ ...... paste parsing · ZIP expansion · virus scan
        │      ├── ocr/ ......... text extraction · classification · field rules
        │      ├── matching/ .... document→member grouping · principal linkage
        │      ├── validation/ .. business rules → severity-flagged exceptions
        │      └── outputs/ ..... bindings · value maps · workbook generation
        │                              │
        │                              ▼
        │                        registry/generate.py
        │                              │  copy master → write rows → re-fingerprint
        │                              ▼
        │                        ooxml/ ── package · sheet · fingerprint
        │
        ├── storage.py .......... content-addressed files under BMS_DATA_ROOT
        ├── audit.py ............ append-only trail
        └── models.py ........... SQLAlchemy 2.0, PostgreSQL-ready
```

About 8,200 lines of application code and 3,300 lines of tests, across 42
modules. Nothing in the runtime reaches the internet.

## 2. Constraints that shaped everything

These came from BMS and are not negotiable in the design:

| Constraint | Consequence |
| --- | --- |
| No mailbox integration | A pasted **BMS Comments** field is the only instruction input. No IMAP, no Graph, no `.eml` import |
| No external AI or token-based document API | OCR is local adapters over Tesseract. There is no HTTP client for document processing anywhere in the tree |
| Insurer and log templates are immutable | Generation is copy-then-surgically-write-then-verify. Nothing is ever built from scratch |
| Post-submission fields stay blank | Six log columns are nullable, null by default, and fill only through explicit workflow actions that refuse placeholders |
| No invented data | Missing information produces a flag, not a plausible value |
| No override of a critical error | There is no override endpoint. The guarantee is structural, not a permission |
| Reachable only inside the BMS network | Simple local auth. No AD, LDAP, SSO or MFA |

## 3. The decisions worth explaining

### Why the OOXML layer is hand-written

The obvious choice was openpyxl. It was tried and rejected.

Opening any of the supplied workbooks with openpyxl emits:

```
UserWarning: Data Validation extension is not supported and will be removed
```

All six of the BMS log's dropdowns are x14 extension validations. openpyxl
would have **silently dropped every one of them** on save. The same class of
loss applies to Daman's locked VBA project, its SHA-512 sheet protections, its
18 tables and its Purview label.

So `bms/ooxml/` treats a workbook as what it is: an ordered ZIP of XML parts.

- **`package.py`** — opens the archive, keeps the part order, and **refuses to
  add a part that was not in the master**. Anything the platform does not
  understand is copied through byte-for-byte.
- **`sheet.py`** — rewrites only `sheetData` and `sharedStrings.xml`. It
  re-registers the document's own namespace prefixes first, so ElementTree does
  not invent `ns0:` prefixes and rewrite every tag in the file.
- **`fingerprint.py`** — captures sheet order and visibility, header values,
  hidden column ranges, inline and x14 validations, defined names, tables,
  protection attributes, the VBA digest and the full part list.

Generation is always: copy the master, write only inside the approved rectangle,
re-fingerprint, diff against the registered baseline, **block on any delta**.
That last step is the whole point. A structurally damaged workbook is caught
before anyone tries to upload it, not at the portal.

**One thing is deliberately outside the fingerprint:** the number-format IDs on
the input rows. Blanking the log's example rows necessarily changes them, and
including them would have made every generation look like drift. Cell styles on
rows the platform creates are checked separately, against the prototypes
captured before blanking.

### Why templates are data, not code

A `TemplateSpec` says where the workbook is and where its data rectangle
starts. A `ValueMap` says how that insurer spells each controlled value. A
`TemplateBinding` says which member field fills which column — and which columns
are deliberately blank, with the reason recorded.

Adding a tenth insurer is three data edits. Intake, OCR, matching, validation,
review, logging and audit do not change, and the test suite picks the new entry
up automatically.

The `ValueMap` carries an `unsupported` dictionary as well as a mapping. ADNIC's
relation dropdown has no `Parent`; Daman's marital dropdown has no `Divorced`.
Rather than bending the value to the nearest option, the platform raises
`UnsupportedValue` and refuses. Approximating there would produce a workbook the
portal accepts and the insurer records wrongly, which is worse than a failure.

### Why OCR is adapters with an honest floor

`TextExtractor` implementations are tried in order: plain text file → embedded
PDF text → Tesseract OCR. If none can read a document, the result is
`ocr_unavailable` and a review flag — never a guess.

This matters more than accuracy. A platform that guesses badly at an Emirates ID
is worse than one that says it cannot read the card, because the second gets
corrected and the first gets submitted.

MRZ parsing implements the ICAO 9303 TD3 check digits (weights 7, 3, 1) and uses
them to *drive* confidence rather than to reject — a failed check digit lowers
confidence and raises a flag, because a genuine passport with a smudged scan is
common and should reach a human, not the bin.

Extraction is separated from reading throughout: the rules operate on text, so
they are tested by handing them text. That is also why the suite runs without
Tesseract installed.

### Why grouping is scored, not rule-chained

Documents are assigned to members by score: strong identifiers (Emirates ID,
passport number, card number) 6.0; a shared staff ID 4.0 — deliberately below
strong, because a principal and a dependant legitimately share one; two matching
name tokens 3.0; the archive folder a document arrived in 3.0; weak signals 2.0.
The assignment threshold is 3.0.

The tuning is load-bearing and was corrected twice during the build. Two
matching name tokens must bind; one must not. A staff ID must contribute
meaningfully without alone overriding an identifier conflict.

Scored assignment beats a rule chain here because the inputs are messy — clients
zip families under one folder, name files inconsistently, and send a dependant's
passport with the principal's staff ID written on it. Every assignment is
visible and reassignable in the review screen.

### Why nothing is held in the browser

BMS was explicit: no reliance on browser cache, no session-only state, write to
drive. So the application is server-rendered with no client framework, and every
step writes to the database and the filesystem as it happens.

A case survives a refresh, a sign-out, going home, and a server restart. This
also makes the audit trail complete by construction — there is no intermediate
state that exists only in a tab.

### Why storage is content-addressed

Files are stored under their SHA-256. Uploading the same document twice stores
one copy and reports a duplicate; nothing is ever overwritten in place. That
makes backups incremental-friendly and makes a partially-restored data root
produce *missing* files rather than *wrong* ones.

ZIP expansion guards against both zip-slip (a path escaping the extraction root)
and zip-bombs (member count and total expanded size), with configurable limits.

### Why the audit trail skips no-op edits

Saving a field unchanged writes nothing. A trail that records every button press
is a trail nobody reads. `audit.record_field_change` compares first.

### Why two Excel engines

Seven workbooks are written entirely through the OOXML layer. Two —
`bms.log.2026` and `daman.addition.v1` — carry live formulas that Excel must
evaluate. On Windows with Excel present, `recalc.py` drives it through COM; on
any other host the file is written correctly with formulas intact, the export
records `recalculated: false` with a reason, and Excel calculates on first open.

The degradation is *reported*, not silent. `/health` names the missing
capability and why.

## 4. Data model

Core tables, all SQLAlchemy 2.0 `Mapped`/`mapped_column`:

| Table | Holds |
| --- | --- |
| `user` | Local accounts, PBKDF2 hashes, `is_admin`, `active`, `log_associate` |
| `client` / `sub_group` / `legal_entity` / `client_policy` | The client master. `sub_group.emirate` drives deletion dates; `legal_entity.contract_name` holds the exact dropdown literal |
| `case` | One per client email: client, sub-group, entity, policy, insurer, transaction type, status, owner, `bms_comments` (the immutable original paste), `closed_at` |
| `case_file` | Upload, SHA-256, media type, size, scan verdict and detail, ZIP parent, classification, status |
| `extracted_field` | Field key, raw and normalised value, confidence, source document |
| `member` | One per human, with `provenance` JSON recording where each value came from |
| `review_flag` | Code, severity, message, field, member. Critical blocks export |
| `export` | Generated file, kind, filename, `recalculated`, `recalc_detail` |
| `log_entry` | One row per member mirroring the 27 MAIN DATA columns. Six post-submission columns nullable and null |
| `audit_event` | Actor, action, entity, field, old, new, timestamp. Append-only |

The schema is PostgreSQL-ready on SQLite: explicit string lengths, timezone-aware
timestamps, dialect-neutral JSON, string UUID keys. Alembic runs on both, with
`render_as_batch` so SQLite's inability to alter columns in place is handled
automatically.

## 4a. Runtime footprint

What the BMS host actually needs, verified by auditing every import in the
runtime tree rather than by reading the requirements file:

| Required | Why |
| --- | --- |
| **Python 3.11+** | The runtime. Everything else installs from the offline bundle |
| **uvicorn** | The only network listener. Bound to `127.0.0.1` |
| **FastAPI** | Imported by exactly one file — the web app |
| **SQLAlchemy** | Imported by eight — the data layer |

Optional, each degrading visibly rather than failing:

| Optional | Without it |
| --- | --- |
| **Tesseract** | Scans flagged for manual entry. Nothing guessed |
| **ClamAV** | Uploads recorded `unavailable`, never `clean` |
| **Excel + pywin32** | Log and Daman formulas left for Excel to compute on open |
| **pypdf** | PDFs fall through to the next reader |

Deliberately absent, and confirmed absent:

- **No database server.** SQLite ships inside Python. PostgreSQL is supported,
  not required.
- **No Celery, Redis or message broker.** The retention purge is an asyncio task
  in-process; there is no queue to run.
- **No Node, npm or build step.** Templates are server-rendered Jinja2.
- **No HTTP client anywhere in the runtime tree** — no `requests`, `httpx`,
  `urllib` or `aiohttp` import outside the tests. The platform cannot call out
  even by accident.
- **No external asset.** No CDN, no web font, no analytics, no remote image.
  Every byte the browser loads comes from the host. Checked, not assumed: a
  single inline `<script>` on one screen, and nothing else.

That last point is what makes the offline claim real. An internal application
that pulls a font or a script from a CDN leaks the fact of its use to a third
party and breaks entirely on an air-gapped host.

## 4b. Keeping the browser light

Server-rendered HTML with no client framework is only lightweight if the pages
stay bounded. Two things made them grow without limit, both measured rather than
guessed:

**The operational log** rendered every matching row, and each row carried five
workflow forms. At 1,000 entries that was **3.1 MB of HTML and 57,081 DOM
elements** — browsers begin to struggle past roughly 10,000. Now every list is
paged at 50 rows and the editor lives on its own screen: **32 KB and 1,035
elements, identical at 10,000 entries.**

**The new-case form** embedded the entire client master as JSON so its dropdowns
could cascade. At 500 companies that was **707 KB on every visit**, nearly all of
it for companies the operator was not going to choose. Now the page carries only
company names and fetches one company's sub-groups, entities and policies on
selection — **1.3 KB, constant.**

**The stylesheet** was inlined into every page — 3.3 KB of identical bytes on
all eleven screens, re-sent on every navigation. It is now a file the browser
fetches once and revalidates with a 304. Measured across four representative
pages: **23,957 bytes of HTML before, 9,238 after**, plus 5,564 bytes of assets
fetched once.

All three are held by tests that assert the page stays bounded as rows are
added, so the next feature cannot quietly reintroduce the problem.

## 4c. Where the time goes

Measured with 8,000 cases and 40,000 members, files, flags and log rows — not
reasoned about.

**Indexes.** Twenty-four foreign keys carried four indexes between them, while
`case_id` alone is a query predicate in twenty places. Every case screen
therefore full-scanned five tables, at a cost that grew with the whole database
rather than with the case being opened. All foreign keys, the log's filter
columns and the `created_at` ordering columns are now indexed.

| Screen | Before | After |
| --- | ---: | ---: |
| One case | 22.4 ms *(at 2,000 cases)* | **7.9 ms** *(at 8,000)* |
| Operational log | 38.6 ms | **13.4 ms** |
| Case list | 14.1 ms | **6.7 ms** |

The absolute numbers matter less than the shape: opening a case was linear in
the size of the database and is now effectively flat.

**The log's filter dropdowns** ran three `SELECT DISTINCT` over the whole table
plus a `COUNT`, so four full scans per page load of a table that only ever
grows. Indexed, each is an index scan.

**Uploads** were read whole into memory and size-checked afterwards, so the
limit could not prevent what it existed to prevent — a body of any size was
fully resident before anything rejected it. Reading now stops at the first chunk
that crosses the limit, bounding peak memory by the limit itself. The
administrator client-master import was doing the same thing and is now bounded
the same way.

**Reprocessing a case** loaded its documents four times: identity building and
member building each selected the files and then their extracted fields once per
file, and the member loop flushed once per member to obtain an id it could have
generated itself. Everything is now loaded in two statements and flushed once.

| Documents in the batch | Before | After |
| ---: | --- | --- |
| 20 | 67 queries, 29.8 ms | **9 queries, 18.3 ms** |
| 60 | 147 queries, 54.1 ms | **9 queries, 26.7 ms** |
| 120 | 267 queries, 95.3 ms | **9 queries, 37.6 ms** |

The count is flat: it no longer depends on how many documents a client attached
or how many members they resolve to. A test holds that property.

That consolidation reads the fields by joining through the case rather than by
an id list, which needs `extracted_fields.file_id` indexed — the one foreign key
the indexing pass above missed, and the busiest, since that table grows with
every document ever uploaded and is never purged. On 24,000 rows: full scan
5.63 ms, index search **0.140 ms**.

**The retention purge** asked, once per file, whether any other case still
referenced that blob. It now asks once per case.

## 5. Security posture

| | |
| --- | --- |
| Network | Reachable only inside the BMS environment. No public exposure |
| Authentication | Local. PBKDF2-HMAC-SHA256, 240,000 rounds, per-user salt, constant-time comparison. Identical failure for wrong user and wrong password |
| Sessions | HMAC-signed cookie, `HttpOnly`, `SameSite=Lax`. Carries no privileges — the user is re-loaded per request, so disabling an account takes effect immediately |
| Uploads | Virus-scanned before extraction. Infected files never reach storage. An absent scanner reports `unavailable`, never `clean` |
| Archives | Zip-slip and zip-bomb guarded, with configurable member and size limits |
| CSRF | Every state-changing request carries a token derived from the session — `HMAC(secret, session-cookie)`. No second cookie and no server-side store: it is stable per session, unique per session, unguessable without the signing key, and worthless once the session ends. Enforced as a global dependency, so a route added later is protected by default. Only `/login` is exempt, because no session exists yet to derive one from |
| Output escaping | Templates are escaped by Jinja. The one response built by hand — the HTTP error page — escapes its detail explicitly, because several details quote the request back (a username, a workflow event name, a filename) |
| Content-Security-Policy | `script-src 'self'` with **no** `unsafe-inline`, so markup that reaches a page cannot execute; the platform's only script is served from `/static`. `style-src` keeps `unsafe-inline` for layout style attributes, which cannot run script. Held by a test that also refuses any inline `<script>` or `on*` handler in a template |
| Authorisation | One operational role. Administrators additionally manage accounts and the client master. No role can override a critical error |
| Retention | Documents purged 36 hours after closure; records and audit trail kept indefinitely |
| Audit | Append-only, no edit or delete path, never purged |
| Secrets | `BMS_SECRET_KEY` from the environment, never committed |

Serve behind TLS and set `secure=True` on the session cookie for any real
deployment.

## 6. Where the defects live — on purpose

The supplied templates contain genuine defects. **They are preserved, not
fixed**, because the insurer portals expect the files exactly as supplied:

- NAS `Departments` and `Grades` sheets are empty; the platform leaves them empty.
- NAS `SubNat_Other` points at a blank cell.
- NAS Work Region validation starts at row 3 while data starts at row 2.
- NAS Visa Type has no dropdown at all.
- NAS maps `Lymphatic system conditions` to `PREG131`.
- ADNIC `WorkLocation` resolves to `#REF!`.
- Daman ships a pre-broken `INDIRECT(SUBSTITUTE(#REF!,…))` validation and a
  pre-seeded `AK3 = "1-4000"`.

Fixing any of these would be a structural change, which is precisely what the
fingerprint blocks. If BMS wants them corrected, that is a conversation with the
insurers followed by a new registered master — not a code change.

## 7. What discovery changed

Findings from reading the supplied material that altered the brief:

- **The log shipped with 1,101 real member rows** — names and Emirates IDs. BMS
  confirmed these are examples only. They are stripped at registration and never
  imported, so no real member data from the supplied workbook exists in the
  database or on disk.
- **The `.eml` files were not anonymised.** They are reference only, never
  imported or processed.
- **The log carries 10 external links to personal paths.** Ignored entirely.
- **The Masaood logo colours sit outside the brand palette.** The UI follows the
  brand guideline document, not the logo.
- **Daman is the hardest template by a distance** — locked VBA, SHA-512
  protection, 18 tables, a Purview label — and needs both a Windows host with
  Excel and Daman's written permission, which BMS does not yet hold.

## 8. Known limits

| Limit | Status |
| --- | --- |
| Emirates ID check digit | Structure validated only. BMS has not supplied the algorithm |
| Deletion-reason mapping | `Others` plus free text. BMS has not supplied a mapping |
| OCR accuracy on real scans | Unverified — no Tesseract in the build environment and no real documents. The degradation path is tested |
| Excel COM recalculation | Unverified — no Windows host. The absence path is tested |
| PostgreSQL | Schema is portable and migrations run on both; the recorded suite ran on SQLite |
| Live portal upload | Structural equivalence is proven; acceptance against the live portals is a BMS step |

The first two are blocked on business rules, not engineering. Supply either and
it becomes configuration.

## 9. Reading the code

Suggested order, if you are new to it:

1. **`bms/models.py`** — the vocabulary. Everything else refers to these.
2. **`bms/pipeline.py`** — the case journey, one function per step. This is the spine.
3. **`bms/templates/specs.py`** and **`bms/outputs/bindings.py`** — how a template is described.
4. **`bms/registry/generate.py`** — copy, write, re-fingerprint, block on drift.
5. **`bms/ooxml/`** — the mechanics, once you want to know how the writing works.
6. **`app/tests/test_template_preservation.py`** — the tests that state the central promise most directly.
