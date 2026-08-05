# User manual — Medical Team

For the people who process endorsement requests. It assumes no technical
knowledge. Everything here is done in a browser, inside the BMS network.

If you are setting the platform up or administering it, read
[ADMIN_MANUAL.md](ADMIN_MANUAL.md) instead.

---

## Contents

1. [What the platform does — and what it deliberately does not](#1-what-the-platform-does)
2. [Signing in](#2-signing-in)
3. [The case journey, step by step](#3-the-case-journey)
4. [Reading the review screen](#4-reading-the-review-screen)
5. [Exception codes and what to do about each](#5-exception-codes)
6. [The log screen](#6-the-log-screen)
7. [Recording what happens after submission](#7-after-submission)
8. [Frequently asked questions](#8-frequently-asked-questions)

---

## 1. What the platform does

You paste the client's email body, upload their documents, and the platform
reads the documents locally, works out who each member is, groups their
documents together, applies the client's rules, and shows you everything it
proposes — with a confidence figure and the source document for every value.
You correct what is wrong, approve, and it fills the insurer's portal workbook
and the BMS log.

**What it will never do**, by design and by instruction:

| It does not | Because |
| --- | --- |
| Read your mailbox or connect to Outlook | You paste the email body yourself |
| Send any document to an external AI or cloud service | All reading happens on the BMS host |
| Change the shape of an insurer workbook | The generated file is checked against the master and blocked if anything moved |
| Fill in a value it has not been given | A blank cell is the correct answer until someone knows the real one |
| Let you override a critical error | The underlying data has to be corrected instead |

That last one is worth repeating. There is no override button, no supervisor
password and no "export anyway" path. If the platform is blocking you, the fix
is always in the member's data.

---

## 2. Signing in

Go to the address your administrator gave you — something like
`http://bms-endorsements:8000`. You will see the sign-in screen.

- Your username and password come from your administrator.
- You stay signed in until you sign out or the server restarts.
- Signing out is in the top right of every screen.

**Your sign-in matters beyond access.** The log's `SHARED BY` column is filled
from whoever is signed in when the case is exported. Do not process a case
under a colleague's account — the log will credit the wrong person and the
audit trail will too.

If you are locked out, your administrator can reset your password. Nobody,
including the administrator, can read your existing one.

---

## 3. The case journey

Seven steps. A case survives everything in between — closing the browser,
signing out, going home, a server restart. Nothing is held in the browser.

### Step 1 — Create the case

**Cases → New case.**

| Field | What to put in it |
| --- | --- |
| Client | Pick from the list. If the client is missing, ask your administrator to add it |
| Sub-group | The client's sub-group, where they have more than one |
| Legal entity | The entity the members belong to |
| Policy | The policy the endorsement is against |
| Insurer | Who the request goes to |
| Transaction type | Addition or Deletion |
| **BMS Comments** | **Paste the client's email body here, exactly as received** |

Paste the email body unedited. Do not tidy it, do not summarise it, do not
translate it. The platform reads names, staff IDs, card numbers, categories,
relations and dates out of it, and your original paste is kept unchanged as the
record of what the client actually asked for. Anything you want to add goes in
the notes, not in place of the paste.

One case per client email. If the email asks for three additions and one
deletion, that is still one case — the transaction type is recorded per member.

The case gets a reference automatically. Write it down; it is how you find the
case again.

### Step 2 — Upload the documents

**Upload files** on the case screen. You can:

- Select many files at once.
- Upload ZIPs. They are opened for you, including ZIPs inside ZIPs.
- Upload the same file twice without harm — it is stored once and reported as a
  duplicate.

Each file is virus-scanned before anything else touches it. If the scanner
finds something, the file is rejected outright and never reaches storage. If no
scanner is installed on the host, uploads are recorded as **unscanned** and
raise a warning — they are never quietly recorded as clean.

Very large files and enormous archives are refused rather than allowed to
exhaust the server. Your administrator sets the limits.

### Step 3 — Run processing

Press **Run processing**. The platform then:

1. Reads text from each document — directly if the file has text in it, by
   local OCR if it is a scan.
2. Works out what each document is: passport, Emirates ID, visa, entry permit,
   birth certificate, card, photo.
3. Pulls out identity fields, with a confidence figure and the page they came
   from.
4. Decides which documents belong to which person, and links dependants to
   their principal.
5. Applies the client's rules and raises exceptions.

**If the host has no OCR engine installed**, scanned documents are marked
`ocr_unavailable` and raised for you to type in by hand. They are never guessed
at. Ask your administrator to check `/health` if you see this and did not expect
it.

### Step 4 — Review each member

This is the step that matters, and section 4 covers it in detail.

### Step 5 — Approve

Approve each member once you are satisfied. A member with an open **critical**
issue cannot be approved.

### Step 6 — Export

Choose the template and press **Export**. You get two files:

- **The insurer's portal workbook**, filled in and structurally identical to the
  master. Every generated workbook is re-checked against its master before you
  are allowed to download it. If a single sheet, header, dropdown, named range,
  table or protection setting has moved, the export is blocked and reported — it
  is never handed to you to upload and find out at the portal.
- **The BMS log rows**, one row per member.

You can also build a **supporting-document ZIP**, which contains each member's
documents under approved names plus a `manifest.csv` tying every file back to
its member and its row in the portal workbook. That is what you attach to the
insurer email.

Export is refused while any critical issue is open, on any member, by any path.

### Step 7 — Close

Close the case when the endorsement is done. Closing starts the retention clock:
uploaded documents and generated files are deleted a set number of hours later
(36 by default). **The case record, the member rows, the log entries and the
audit trail are kept indefinitely** — only the documents go.

An open case never loses its evidence, however long it stays open. If you need
the documents back after closure, they are gone; re-request them from the
client.

---

## 4. Reading the review screen

Each member has a screen showing every proposed value with three things
attached:

- **The value** the platform proposes.
- **Where it came from** — which document, which page.
- **How confident it is** — a figure from 0 to 1.

A high confidence is not a guarantee. It means the text was clean and the field
parsed cleanly; it does not mean the document was the right one. Read the source.

### Correcting a value

Type over it and save. Three things then happen:

1. The value is marked **confirmed** — a human has looked at it.
2. The audit trail records the old value, the new value, you, and the time.
3. The member is re-validated, so exceptions clear or appear immediately.

Saving a value unchanged does nothing and writes no audit entry. Only real
changes are recorded.

### The confidence flags

| What you see | Meaning |
| --- | --- |
| A low-confidence marker | The reading was poor. Check the document and correct or confirm |
| `ocr_unavailable` | No OCR engine on the host. Type it in |
| A reassign control on a document | The platform put this document with the wrong person. Move it |

### Reassigning documents

If a passport has been attached to the wrong member, move it. Grouping is
deterministic — it scores identifiers, names and the archive folder a document
came from — but a client who zips a family together under one folder can mislead
it. Reassigning re-validates both members.

---

## 5. Exception codes

Four severities:

| Severity | Meaning | Blocks export? |
| --- | --- | --- |
| **Critical** | The data is wrong or missing and must be fixed | **Yes, always. No override** |
| **Review required** | Someone must look at this and decide | No, but do not ignore it |
| **Warning** | Worth knowing, normal in some cases | No |
| **Passed** | Checked and fine | No |

### Every code the platform can raise

| Code | Severity | What it means | What to do |
| --- | --- | --- | --- |
| `missing_mandatory_field` | Critical | A field the insurer requires is empty | Fill it from the documents, or get it from the client |
| `deletion_identifier_missing` | Critical | A deletion has nothing to identify the member by | Add the card number, staff ID or Emirates ID |
| `eid_placeholder_misuse` | Critical | `111111` has been used in an Emirates ID | Emirates ID never takes the newborn placeholder. Get the real number or leave the member out |
| `eid_format_invalid` | Critical | The Emirates ID is not in `784-YYYY-NNNNNNN-N` form | Re-read the card; correct the digits |
| `eid_missing` | Review required | No Emirates ID found | Check whether the member has one yet. Newborns often do not |
| `retroactive_effective_date` | Critical | The effective date is in the past | Retroactive dates are not permitted. The effective date is the processing date |
| `dob_in_future` | Critical | Date of birth is after today | Almost always a misread date. Check the document |
| `passport_expired` | Review required | The passport has expired | Ask the client for a current one, or confirm the insurer will accept it |
| `principal_unresolved` | Critical | A dependant has no confirmed principal | Confirm which principal they belong to |
| `single_name_member` | Warning | The member has one name only | `....` goes in the Last Name field. This is correct, not an error |
| `low_confidence_value` | Review required | A value was read poorly | Check it against the document and correct or confirm |
| `newborn_missing_birth_certificate` | Review required | A newborn without a birth certificate | Request it |
| `newborn_missing_document` | Review required | A newborn without supporting documents | Request them |
| `no_supporting_documents` | Review required | A member with no documents at all | Either the documents were not sent, or the member was read out of the email in error |
| `duplicate_member` | Critical | Two members in this case look like the same person | Merge them, or correct whichever is wrong |
| `not_virus_scanned` | Warning | No virus scanner on the host | Tell your administrator. The file is usable |

### The two placeholder rules

- **`111111`** goes in a mandatory field a newborn genuinely cannot have yet —
  but **never in an Emirates ID**. The platform raises a critical error if it
  finds it there.
- **`....`** goes in Last Name for a member with only one name. The available
  name goes in the correct field.

Nothing else is ever a placeholder. Never type `N/A`, `Pending`, `-`, `0`,
`None`, `Nil` or `TBC` into a field — the platform refuses several of these
outright, and a blank cell is the right answer where the information does not
exist.

### Effective dates

| Situation | Effective date |
| --- | --- |
| Any addition | Today's processing date |
| Deletion, Abu Dhabi policy | Today's processing date |
| Deletion, Dubai policy | Cancellation date **+ 30 days** |

The platform applies these itself. If a client asks for a date in the past, that
is a critical error, not a setting to change.

### Deleting a principal

Their dependants are included automatically where applicable. You do not list
them separately, and you do not need to raise a second request.

---

## 6. The log screen

**Log** in the top navigation. Every member the platform has processed has one
row.

### Filtering

Filter by date range, client, insurer, status or entry type. Filtering only
changes what you are looking at — it never alters a single entry.

### Statuses

| Status | Meaning |
| --- | --- |
| `PENDING TO CLIENT` | Waiting on information from the client |
| `PENDING TO INSURER` | Sent, waiting on the insurer |
| `CLOSED` | The endorsement is complete |
| `BOOKED` | The invoice or accounting entry is accounted for |

Status follows the case automatically as it moves through draft, review,
approval, export and closure. You can also set it directly on the log screen
when the real position differs.

### Remarks

`Remarks` and `Remarks 2` are yours — missing documents, what you are waiting
for, anything a colleague picking the case up would want to know. There is no
required format and they may stay blank.

### Exporting the log

Set your filters, press **Export**. You get a copy of the approved
`New Log Format -2026` workbook containing exactly the rows you filtered to.

The workbook's own formulas — `TAT`, `Current Date`, `Pending with Insurer
since`, `Aged Pending` and the whole `SUMMARY` sheet — are left alone. The
platform never writes into them. On a Windows host with Excel they are
calculated before you get the file; otherwise Excel calculates them the moment
you open it.

---

## 7. After submission

Six columns stay genuinely blank until someone does something real:

| Column | Filled when |
| --- | --- |
| `Request Ref.No.` | You record the submission |
| `Request sent date to Insurer` | You record the submission |
| `CARD #` | You record card receipt |
| `Card receive and sent date to Insured` | You record card receipt |
| `Saiba Voucher No.` | You record the voucher |
| `BBM Invoice date` | You record the invoice date |

These are not filled by export, by approval or by closing a case. They are
filled by you, on the log screen, using the matching action — **Record
submission**, **Record card received**, **Record voucher**, **Record invoice
date**.

The platform refuses to put a placeholder in any of them. If you try to record
`Pending` or `N/A` as a reference number it will tell you to leave it blank
instead. Every one of these edits is audited.

---

## 8. Frequently asked questions

**The platform says my export is blocked. Who can override it?**
Nobody. There is no override, deliberately. Open the member with the critical
flag and correct the data.

**Can I change the effective date to what the client asked for?**
Not to a past date. Retroactive dates are not a permitted processing option.
Raise it with the client.

**The client's deletion reason does not match any option.**
Use `Others`, and put the client's actual wording in the free-text field where
the template has one.

**A document is in Arabic.**
If Arabic OCR is installed on the host it will be read. Check with your
administrator, and type the values in if not.

**I closed a case by mistake.**
Reopen it from the case screen. The documents are still there as long as the
retention window has not elapsed.

**I need last month's documents back.**
They are gone if the case was closed more than the retention window ago. The
case, members, log rows and audit trail are all still there — only the documents
are purged.

**Can I work on two cases at once?**
Yes. Nothing is held in the browser, so open as many tabs as you like.

**Something looks wrong with a generated workbook.**
Do not upload it. Tell your administrator and quote the case reference — the
platform records the structural check result for every export.
