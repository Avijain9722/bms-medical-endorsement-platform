# Recommended Claude Setup

## Preferred method for application development: Claude Code

1. Extract this package into a dedicated local folder.
2. Open a terminal in the extracted root folder.
3. Start Claude Code in planning mode:

```bash
claude --permission-mode plan
```

4. Paste the command in `CLAUDE_START_COMMAND.md`.
5. Require Claude to complete discovery and ask all open questions before writing or modifying application code.
6. After approving the discovery plan, instruct Claude to create the application in a new `app/` directory and never alter the source templates in this package.

Claude Code is more suitable than uploading one ZIP into a web chat because it can inspect the full directory tree, preserve binary source files, create code and tests, and work iteratively in the same project folder.

## Alternative method: Claude.ai Project

1. Create a dedicated Claude Project.
2. Place the permanent instructions from `MASTER_DEVELOPMENT_PROMPT.md` into Project Instructions.
3. Upload the source files individually to Project Knowledge, using the directory order in `README.md`.
4. Start a new chat in that Project and paste `CLAUDE_START_COMMAND.md`.

Do not rely on the ZIP alone in Claude.ai. Keep filenames descriptive and refer to each template by its exact filename.

## Confidentiality control

The package contains realistic client examples and may contain personal or sensitive information. Use only a BMS-approved Claude account and environment. If approval for external AI processing has not been confirmed, replace the examples with anonymised or synthetic copies before upload. The production application itself must not use external AI or token-based document processing.
