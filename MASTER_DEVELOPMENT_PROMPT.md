# MASTER DEVELOPMENT PROMPT
## BMS Masaood Medical Insurance Endorsement Processing Platform

## 1. Role and instruction

Act as a senior software architect, OCR engineer, Python developer, database designer, Excel automation specialist, information-security specialist, UX designer and quality-assurance lead.

Design and build a secure internal platform for BMS Masaood Insurance L.L.C. – O.P.C. to process corporate medical insurance endorsement requests.

Do not begin by creating a generic form, generic spreadsheet or visual mock-up. First inspect every supplied workbook, guideline and example, identify the actual operational rules and provide the discovery outputs listed in Section 29.

## 2. Required operating model

The production application must not connect to Outlook, Gmail or any mailbox. It must not monitor, read, retrieve or send emails.

For each request, a Medical Team user will:

1. Create a case.
2. Select the client, entity, insurer/TPA, policy and transaction type.
3. Enter the email subject and received date where required.
4. Copy and paste the relevant client email body into a Client Instructions field.
5. Separately upload supporting documents, images, Excel files or ZIP folders.
6. Select the applicable client master data and portal template.
7. Run local processing.
8. Review and resolve all flagged issues.
9. Approve the final member records.
10. Generate the exact portal workbook, supporting ZIP and BMS internal log output.

The supplied `.eml` files are development examples only. Use them to understand real request patterns and attachments. The production application does not need to import `.eml` files.

## 3. Business problem

The BMS Masaood Medical Team manages a large portfolio across multiple corporate clients, entities, policies, insurers, TPAs, categories, salary bands and subgroups.

Endorsement requests include:

- Employee additions.
- Spouse, child, parent and other dependant additions.
- Newborn additions.
- Employee and dependant deletions.
- Principal-and-family deletions.
- Category, plan and subgroup changes.
- Entity transfers.
- Marital-status and relationship changes.
- Name, DOB, nationality, passport, Emirates ID, UID, visa and Staff ID corrections.
- Other member-data modifications.

A request may contain one member, one family, several unrelated employees or mixed additions, deletions and changes.

Required information is distributed across the pasted client instruction, passports, Emirates IDs, visas, entry permits, birth certificates, insurance cards, medical declarations, COCs, photographs, ZIP folders, client Excel sheets, employee masters, insured-member masters and client-specific mapping files.

The current process requires manual document opening, extraction, member matching, principal matching, validation, portal-template completion and log maintenance. A batch may take two to three hours. The objective is to reduce normal processing to a few minutes plus controlled human review.

## 4. Project objective

Build a locally hosted or BMS-controlled platform that can:

- Accept pasted client instructions.
- Accept individual files and ZIP folders.
- Run local OCR and deterministic document analysis.
- Classify documents.
- Create one internal record per member.
- Match documents to the correct member.
- Link dependants to the correct principal.
- Apply client, entity, policy, category, salary and portal rules.
- Detect duplicate members and duplicate documents.
- Flag missing, conflicting, uncertain or invalid information.
- Prevent unresolved records from being exported.
- Populate the exact approved insurer/TPA template without structural changes.
- Generate supporting document packages where required.
- Maintain a persistent daily and historical BMS operational log.
- Retain unfinished and completed cases beyond 24 hours.
- Maintain a complete audit trail.

## 5. No external generative AI or token-based processing

Do not use any external generative AI or token-based service to process BMS documents or member data, including OpenAI, ChatGPT, Claude, Gemini, Azure OpenAI, AWS Bedrock or similar services.

Do not send member documents to external document-intelligence APIs unless BMS separately approves a specific service in writing.

Use local or BMS-controlled components such as:

- Tesseract OCR.
- PaddleOCR running locally.
- OCRmyPDF.
- OpenCV.
- PyMuPDF, pdfplumber or pypdf.
- Local passport MRZ parsing.
- Local barcode/QR parsing where relevant.
- Rule-based text parsing and regular expressions.
- Local database and file storage.
- Configurable deterministic validation and mapping rules.

The runtime application must not require internet access for document processing.

## 6. Accuracy requirement

Do not promise that OCR is inherently 100% accurate.

Implement the operational requirement as follows:

> No incorrect, incomplete, conflicting or unverified record may be released into a final portal file.

Every critical field must have a source, confidence/validation status and review state. The application must block export where a critical value is uncertain, conflicting, missing or invalid. The system must never invent or guess missing data.

A final record may be exported only after:

- Mandatory fields are complete.
- Critical document fields are supported by an identified source.
- Format and business validations pass.
- Principal linkage is confirmed for dependants.
- Exact portal values are confirmed.
- Conflicts and low-confidence fields are resolved.
- An authorised user approves the record.
- The generated workbook passes a structural integrity check.

## 7. Persistent case memory

Do not use temporary browser memory as the case store.

Use a persistent database and controlled file storage so that cases survive browser refresh, logout, workstation restart, application restart and end of day.

The platform must retain:

- New and partially processed cases.
- Open, pending, submitted, completed and closed cases.
- Original pasted instructions.
- Original uploaded files and ZIP structures.
- Extracted values and corrections.
- Generated outputs.
- Audit history.
- Daily logs.

Open cases must carry forward to future days. Retention must be configurable and must not default to automatic deletion after 24 hours.

## 8. Recommended architecture

Propose the final architecture after inspecting the files. A suitable target is:

- Internal browser-based frontend.
- Python backend using FastAPI or Django.
- PostgreSQL for multi-user production; SQLite only for an early standalone prototype.
- BMS-controlled local or private file storage.
- Background job processing for OCR and batch operations.
- Microsoft Excel automation on Windows where required for protected/macro-enabled workbooks.
- openpyxl only where inspection proves it will preserve the workbook safely.

The production deployment may be on a BMS server, BMS-controlled VM or approved private environment.

## 9. Case creation screen

Include:

- Client.
- Legal entity.
- Insurer/TPA.
- Policy number.
- Contract name.
- Transaction type.
- Email subject.
- Email received date and optional time.
- Optional sender name/email.
- Pasted client instructions.
- User notes.
- Supporting-document upload.
- ZIP upload.
- Client-information-sheet upload.
- Employee/insured-member master selection.
- Output-template selection or system recommendation.

## 10. Supported inputs

Support, subject to security controls:

- PDF, JPG, JPEG, PNG, TIFF and BMP.
- XLSX, XLSM and XLS.
- DOCX where required.
- ZIP and nested folders.
- Multiple ZIP files in one case.

Detect and flag unsupported, corrupted, empty or password-protected files.

## 11. Document classification

Classify uploaded files into types such as:

- Passport.
- Emirates ID.
- Visa/residence document.
- Entry permit or change-status document.
- Birth certificate.
- Medical insurance card.
- Certificate of Continuity.
- Medical declaration.
- Photograph.
- Cancellation document.
- Client information sheet.
- Employee or member master.
- Unknown document.

Unknown documents must be presented for manual classification.

## 12. OCR and pre-processing

Perform local pre-processing such as orientation detection, rotation, deskewing, denoising, contrast improvement, cropping, blank-page detection and image-quality checks.

Use embedded PDF text before OCR where available. Support English, Arabic where required, numbers, dates, UAE identity formats and passport MRZ data.

For every extracted field, retain:

- Value.
- Source file.
- Source page.
- Confidence score or validation result.
- Bounding area where technically feasible.

## 13. Normalised internal member record

Create one internal record per member. Do not write directly from raw OCR into a portal workbook.

The internal record should support:

### Case information
- BMS case number.
- Client, entity, insurer/TPA, policy, contract and transaction type.
- Email subject and received date.
- Pasted instruction reference.

### Identity
- Full, first, middle and last name.
- Arabic name fields where required.
- DOB, gender and nationality.
- Passport number and expiry.
- Emirates ID.
- UID/unified number.
- Visa file number, issuance emirate, expiry and type.
- Birth certificate number.

### Employment/client information
- Staff ID, SAP ID, temporary ID and employee ID as separate fields.
- Department, grade, category, salary band, subgroup and marital status.
- Effective date.

### Family information
- Relation.
- Principal name and identifiers.
- Principal card number.
- Family number.
- Linkage status.

### Portal-specific information
- Work/residence country, emirate and region.
- Email, mobile, member type and occupation.
- Regulator number, company phone and company email.
- Commission, COC status, PEC fields and attachment filenames.

### Processing information
- Extracted, existing, requested, proposed and approved values.
- Source and confidence/validation status.
- Reviewer and comments.

## 14. Source hierarchy

Make source priority configurable by client and policy. Use the following initial model:

- Name, DOB, gender, nationality, passport number and passport expiry: passport primary.
- Emirates ID: Emirates ID copy primary.
- UID, visa file number, visa emirate and visa expiry: visa/entry permit primary.
- Staff ID, category, marital status and relation: pasted client instruction or client sheet, subject to client rules.
- Principal card number: existing member master/card/client instruction.
- Contract and policy: client configuration.
- Salary band: approved client/category mapping.
- Effective date: configured client-specific precedence.

Where sources conflict, display all relevant values and require resolution. Do not silently select one.

## 15. Pasted-instruction analysis

Use local deterministic parsing to identify, where stated:

- Staff/SAP/temporary/employee IDs.
- Member and principal names.
- Policy, contract and card numbers.
- Transaction type.
- Category, marital status, relation and subgroup.
- Effective, joining, entry and deletion dates.
- Deletion reason.
- Member count and special instructions.

The user must be able to correct all parsed values.

## 16. Member and document matching

Do not rely only on filenames or names.

Match using combinations of:

- Staff, SAP, temporary and employee IDs.
- Full name and DOB.
- Passport number.
- Emirates ID.
- UID and visa file number.
- Insurance/principal card number.
- ZIP folder and filename.
- Pasted instruction.
- Existing client/member master.
- Family linkage.

Show a document bundle per member. Allow users to reassign, split or remove documents before approval.

## 17. Principal and dependant matching

Use a controlled hierarchy such as:

1. Principal card number.
2. Principal Staff ID.
3. Employee/SAP ID.
4. Emirates ID.
5. Passport number.
6. Existing insured-member master.
7. Family number.
8. Manual approval.

Block export where no principal is found, several candidates exist, the principal belongs to another entity/policy, the principal is inactive or scheduled for deletion, or category rules conflict.

## 18. Transaction processing

Support at minimum:

- Principal employee addition.
- Spouse, child, parent and other dependant addition.
- Newborn addition with approved missing-document exceptions.
- Employee deletion.
- Dependant deletion.
- Principal-and-family deletion with family-impact review.
- Category/plan change.
- Subgroup/entity transfer.
- Member-data modification.
- Mixed batches containing different transaction types.

For changes, show existing, requested and approved values. Preserve unchanged data from the approved master.

## 19. Effective-date rules

Make date precedence configurable. Potential sources include explicit requested effective date, joining date, entry date, visa/change-status date, newborn DOB where permitted and email received date as a fallback.

Flag retroactive dates, future dates, dates outside the policy period and dates inconsistent with client/insurer rules.

## 20. Client and policy configuration

Do not hardcode client rules in source code.

Maintain configurable records for:

- Clients and legal entities.
- Policies, contracts, insurers and TPAs.
- Categories, salary bands and category mappings.
- Subgroups, departments, grades and occupations.
- Default locations/contact details.
- Required documents by transaction.
- Effective-date and newborn rules.
- Emirates ID placeholder rules.
- Portal terminology mappings.
- Attachment and ZIP naming conventions.

## 21. Immutable portal templates

Treat every supplied Excel/XLSM workbook as a fixed portal-import template.

Preserve exactly:

- File type.
- Worksheet names/order and hidden status.
- Column headers/order and hidden columns.
- Row structure.
- Formulas, tables and named ranges.
- Data validations and dropdown lists.
- Macros and workbook/worksheet protection.
- Formatting and number/date formats.
- Instructions and helper sheets.

Do not rename, add, delete or reorder columns or sheets. Do not rebuild the workbook. Populate only approved input cells and exact dropdown values.

## 22. Template Registry and structural fingerprint

Register each template by:

Client + Entity + Insurer/TPA + Policy + Contract + Transaction Type + Version

Store a read-only master and metadata including sheet structure, approved input area, mandatory fields, dropdowns, named ranges, formulas, macro/protection status and a structural fingerprint.

Generate every output from a fresh copy. Compare the result to the registered fingerprint. Block release if an unauthorised structural change is detected.

Use Microsoft Excel automation where necessary to preserve complex protected or macro-enabled workbooks.

## 23. Supporting-document ZIP

Where required, generate a package containing only the relevant files. Preserve originals separately for audit. Detect duplicates by hash, apply approved names, ensure Excel attachment filenames match exactly, and generate a manifest linking member, Staff ID, document type, original filename, output filename and portal row.

## 24. Review and exception workflow

Provide one review row per member showing status, identifiers, transaction type, relation/principal, category, effective date, document status, confidence, missing fields, conflicts, template validation and approval.

On member selection, show source document/page, extracted/existing/requested/approved values and reviewer notes.

Use statuses such as Passed, Warning, Review Required and Critical Error. Critical errors must block export.

## 25. Required exception handling

Include controls for:

- Missing Emirates ID and permitted placeholder use.
- Newborn without passport, visa, UID or Emirates ID.
- Single-name, long and compound names.
- Cross-document name/DOB/passport/nationality conflicts.
- Invalid identification formats.
- Missing UID/visa number.
- Expired passport/visa.
- Blurred, cropped, rotated, glared or unreadable files.
- Password-protected/corrupted files.
- Duplicate filenames and duplicate content.
- Multi-member PDFs and several documents in one image.
- Missing or ambiguous principal.
- Principal recently added without card.
- Principal scheduled for deletion.
- Existing-member and same-day duplicate requests.
- Addition and deletion for the same member.
- Category/salary/effective-date conflicts.
- Unsupported portal values.
- Missing mandatory documents.
- Unapproved/outdated template versions.
- Any structural workbook change.

## 26. Persistent BMS operational log

Use the supplied current workbook `New Log Format -2026.xlsx` as the fixed BMS operational log template. Preserve its worksheets, columns, formulas, tables, validations, helper columns, summary logic, formatting and workbook structure. Do not rename, add, delete or reorder its fields.

Continuously maintain case records in the application database throughout the day and beyond. Do not recreate the log from temporary browser memory. Generate a fresh downloadable copy of the approved log workbook for the selected date range or filter.

### 26.1 Initial log-field population

Populate only information that is available and verified at the relevant workflow stage:

- `SR NO.`: generate sequentially in the exported log.
- `SHARED BY`: populate according to the approved BMS definition and user mapping.
- `Client Name`, `Sub-Group`, `Insurer`, `Policy No.`: populate from the selected client/policy configuration.
- `Beneficiary name`: populate from the verified passport name or existing member master, according to the transaction type.
- `Relation`, `Category`, `Staff ID`, `Emirates ID no.`, `Entry Type`, `Effective Date`: populate only from verified case data and approved mappings.
- `Request Receive date`: populate from the email-received date manually entered by the user.
- `Status`: populate from the approved application workflow-to-log status mapping.
- `REMARKS` and `Remarks2`: populate only if an approved BMS rule defines their use; otherwise leave blank for staff entry.

### 26.2 Fields that must remain blank until the relevant later event

Do not fabricate, estimate, insert placeholders or automatically use `N/A`, `Pending`, zero or a hyphen for post-submission information. Leave the following cells genuinely blank until the information exists or the user performs the corresponding explicit action:

- `Request Ref.No.`: insurer/TPA portal or submission reference; staff enters it after receipt, unless a future approved integration provides it.
- `Request sent date to Insurer`: populate only when an authorised user explicitly records or confirms submission.
- `CARD #`: staff enters the card/member number after it is issued or received.
- `Card receive and sent date to Insured`: populate only after staff records the applicable completion event.
- `Saiba Voucher No.`: leave blank until manually entered or supplied through a future approved Saiba integration.
- `BBM Invoice date`: leave blank until manually entered by the responsible team.
- Any later-stage insurer, TPA, finance or internal reference not available when the case is created.

Users must be able to reopen a case and update these fields later. Record every manual update in the audit trail with previous value, new value, user and timestamp.

### 26.3 Calculated and helper fields

Preserve existing workbook formulas for `TAT`, `Current Date`, `Pending with Insurer since`, `Aged Pending` and all helper/summary columns unless BMS expressly approves a revised formula. Do not write hardcoded values over formula cells.

Where application-side calculations are also maintained, ensure they do not replace or conflict with the approved workbook formulas. Flag any formula behaviour that produces misleading ageing for blank submission dates or closed cases and request BMS approval before modifying it.

### 26.4 Log outputs

Provide downloadable daily and date-range outputs for all cases, open cases, closed cases, submitted cases, exceptions, pending information, client/insurer activity and turnaround time. Each member must occupy a separate operational row unless BMS approves another rule. Preserve shared case-level information across all rows belonging to the same batch.

## 27. Audit, security and access

Record original inputs, OCR output, source/page, confidence, corrections, users, timestamps, reasons, template/version, generated files, submission data and final status.

Use role-based access for Processor, Reviewer, Administrator and Management/Read-only users.

Include authentication, session control, controlled storage, encryption in transit, audit logging, download controls, virus scanning, backup/recovery and configurable retention. No personal or medical data may be sent to an unapproved external service.

## 28. BMS branding and UI requirements

Use the supplied BMS brand guidelines and logo files as the visual source of truth, subject to BMS Marketing confirmation before production release.

Apply the following controls:

- Use the positive two-colour logo on white or light-grey backgrounds where possible.
- Use the negative white/orange logo on dark backgrounds.
- Do not alter, redraw, recolour, distort, crop or separate the orange square from the logo.
- Maintain adequate clear space and legibility.
- Use Oswald for major headings and Montserrat for body/interface text where locally available and properly licensed/bundled. Use a safe local fallback where unavailable; do not make runtime calls to external font services.
- Use left-aligned typography and do not justify text.
- Use sentence or title case; reserve all caps for limited emphasis.
- Use a clean square-grid principle inspired by the brand layout system, without creating an overly decorative interface.
- Prioritise accessibility, readability, contrast, responsive layouts and efficient data-entry workflows.
- Do not use the supplied marketing product icons as software UI controls. Use a consistent accessible UI icon set instead.
- Do not apply BMS branding inside insurer/TPA portal workbooks unless the original template already contains it.
- Do not approximate brand colours from screenshots; obtain exact values from the supplied guideline/source assets.

Create a restrained professional interface suitable for high-volume operational work, not a marketing website. Branding must not reduce usability or data density.

## 29. Required first response before full development

Before building the full platform, provide:

1. Confirmation of the requirement and production workflow.
2. List of every supplied file inspected.
3. Workbook-by-workbook structural inventory.
4. Comparison of template differences.
5. Identification of hidden sheets, formulas, validations, macros, tables, named ranges and protection.
6. Identification of ambiguous or missing business rules.
7. Proposed architecture and deployment model.
8. Proposed database schema.
9. Proposed OCR/document-classification approach.
10. Proposed member/principal matching logic.
11. Proposed template-preservation and fingerprint method.
12. Proposed case workflow and UI wireframes.
13. Proposed branding implementation, including logo variant and typography use.
14. Security, retention and audit design.
15. Phased implementation plan.
16. Risks, controls and prototype acceptance tests.

## 30. Development phases

### Phase 1 – Discovery
Inspect every supplied file and produce the required inventory and business-rule gap list.

### Phase 2 – Prototype
Support pasted instructions, uploads/ZIP extraction, local OCR, member grouping, review, one addition template, one deletion template and the BMS log.

### Phase 3 – Template expansion
Add all supplied NAS, ADNIC, Daman and Sukoon workflows, followed by additional NextCare, MedNet and insurer templates.

### Phase 4 – Production controls
Add multi-user access, roles, audit, template versioning, security, backups, dashboards and reports.

### Phase 5 – Testing and deployment
Perform OCR accuracy, exception, batch, workbook-preservation, security and user-acceptance testing; then deploy and document.

## 31. Acceptance criteria

The completed platform must demonstrate that it can:

- Create a case without mailbox integration.
- Accept pasted instructions and manually uploaded files.
- Run OCR locally.
- Read and validate passport, Emirates ID and visa information.
- Identify several members and group documents accurately.
- Process additions, dependants, newborns, deletions, changes and mixed batches.
- Link dependants to confirmed principals.
- Detect duplicates, conflicts and missing information.
- Apply client mappings and effective-date rules.
- Select and populate the exact approved template.
- Preserve validations, formulas, macros, hidden content and workbook structure.
- Block unresolved records.
- Generate portal-ready workbooks and supporting ZIPs.
- Update and download the persistent BMS log.
- Retain cases beyond 24 hours.
- Maintain a complete audit trail.
- Operate without external generative AI or token usage.
- Apply the supplied BMS visual identity without compromising usability.

## 32. Required deliverables

Provide the complete architecture, database schema, UI wireframes, OCR/classification and matching design, validation engine, Template Registry, Excel population/fingerprint engine, supporting-ZIP module, daily-log module, user/access controls, audit module, source code, installation/configuration guides, test cases/results, user/admin manuals, backup/recovery guide and deployment package.

The design must be modular so a new client, policy, mapping or portal template can be added without rebuilding the entire application.
