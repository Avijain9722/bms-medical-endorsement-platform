# BMS Masaood Medical Endorsement Platform – Development Package

## Purpose

This package contains the master development prompt and the currently available source files for designing and building a locally hosted medical insurance endorsement-processing platform for BMS Masaood Insurance L.L.C. – O.P.C.

The production platform must not connect to a mailbox. A Medical Team user will manually paste the relevant client email body and separately upload the supporting documents or ZIP folders.

## Recommended working method

For development, use Claude Code from the extracted package folder where possible. See `CLAUDE_PROJECT_SETUP.md` and `CLAUDE_START_COMMAND.md`. For Claude.ai, create a dedicated Project and upload supported files individually rather than relying on the ZIP alone.

## Recommended upload order

1. Add `00_START_HERE/MASTER_DEVELOPMENT_PROMPT.md` to the project instructions or source context.
2. Upload all files in `01_BRAND_ASSETS`.
3. Upload all files in `02_PORTAL_TEMPLATES`.
4. Upload the file in `03_INTERNAL_LOG_TEMPLATE`.
5. Upload the files in `04_DEVELOPMENT_EXAMPLES` as development examples only.
6. Upload `05_TECHNICAL_REFERENCE/NAS_Portal_Field_Guide.md` as supporting technical reference.
7. Ask the developer to inspect every file and provide the discovery outputs required in the prompt before writing the full application.

## Important controls

- The Excel and XLSM files are fixed portal-import templates. Their structure must not be altered.
- The `.eml` files are included only so the developer can inspect realistic request patterns and embedded attachment structures. The production application is not required to import or read `.eml` files.
- All OCR and processing must run locally or within a BMS-controlled environment. No external generative AI or token-based API may process client documents.
- Final exported records must be blocked until all critical OCR uncertainty, missing data and conflicts are reviewed and resolved.
- Cases must persist beyond 24 hours and remain available until archived or deleted under an approved retention rule.

## Brand-guideline note

The uploaded filename refers to Brand Guidelines version 1.3, while the internal first-page title refers to September 2021 version 1.1. Treat the supplied document as the working reference for this development package, but confirm with BMS Marketing whether a later approved version exists before production release.

The supplied product-icon library is not intended for software user-interface elements. The platform should use a restrained, accessible UI icon system and should not copy or repurpose the marketing product icons as buttons or navigation icons.

## Files not yet supplied

See `MISSING_INPUTS_CHECKLIST.md`. These are not included in the ZIP because they were not provided in the current conversation.
