# Initial Command for Claude

Read `00_START_HERE/MASTER_DEVELOPMENT_PROMPT.md`, `00_START_HERE/README.md`, `00_START_HERE/ATTACHMENT_MANIFEST.md`, `00_START_HERE/MISSING_INPUTS_CHECKLIST.md`, and every supplied workbook and reference file in this package.

Treat `03_INTERNAL_LOG_TEMPLATE/New Log Format -2026.xlsx` as the latest and only approved BMS operational log template. Treat every insurer/TPA workbook as an immutable portal-import source template. Do not edit any source workbook or development example.

The production workflow must not connect to or read a mailbox. A user will manually paste the client email body, enter the received date and upload the supporting documents or ZIP folders separately. The production application must use local OCR and deterministic rules, with no external generative-AI or token-based document-processing API.

Begin in discovery and planning mode only. Do not start coding yet. Produce:

1. A complete file inventory and confirmation that every file was opened or structurally inspected.
2. A workbook-by-workbook technical inventory covering sheets, hidden sheets/columns, headers, formulas, tables, macros, named ranges, validations, protections, first permitted input row and structural-preservation risks.
3. A field-source and validation matrix for each transaction type.
4. A comparison of client-specific and insurer/TPA-specific templates.
5. The proposed local OCR and document-classification architecture.
6. The member-matching, duplicate-detection and principal/dependant-linkage design.
7. The application architecture, database schema, security model and deployment options.
8. The exact handling of the BMS log, including fields populated automatically, fields populated only after an explicit workflow action, fields left blank for manual entry and formula/helper fields that must not be overwritten.
9. A phased implementation and test plan.
10. A consolidated numbered list of every unresolved business, security, deployment and data-retention question requiring BMS confirmation.

Clearly separate verified findings from assumptions. Do not infer missing business rules. Wait for my approval and answers before implementing the application.
