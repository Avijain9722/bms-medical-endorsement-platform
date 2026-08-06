# Documentation index

| Document | For | Read it when |
| --- | --- | --- |
| [USER_MANUAL.md](USER_MANUAL.md) | The Medical Team | You process endorsement requests |
| [ADMIN_MANUAL.md](ADMIN_MANUAL.md) | Whoever runs the host | Accounts, the client master, health checks, monthly routine |
| [PROTOTYPE_SETUP.md](PROTOTYPE_SETUP.md) | Installers and developers | Installing, configuring, adding a template |
| [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) | Whoever runs the host | Before you need it, not after |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Maintainers | You are changing the code and want to know why it is shaped this way |
| [ERROR_REFERENCE.md](ERROR_REFERENCE.md) | Everyone | Something on screen needs looking up |
| [TEST_RESULTS.md](TEST_RESULTS.md) | Everyone | You want to know what is proven and what is not |
| [CI_PIPELINE.md](CI_PIPELINE.md) | Maintainers | Changing the workflows, or reading a failed run |
| [OFFLINE_INSTALL.md](OFFLINE_INSTALL.md) | Installers | The BMS server has no internet access |
| [BUNDLING_TESSERACT.md](BUNDLING_TESSERACT.md) | Installers | You cannot install OCR into the operating system |
| [`../deploy/`](../deploy) | Installers | Scripted install for Linux and Windows |

## The short version

Paste the client's email body, upload their documents, and the platform reads
them locally, identifies each member, groups their documents, applies the
client's rules, and shows every proposed value with its confidence and its
source. You correct what is wrong, approve, and it fills the insurer's portal
workbook and the BMS log.

It does not read mailboxes, does not send anything to an external service, does
not change the shape of an insurer workbook, does not invent a value it has not
been given, and does not let anyone override a critical error.

## Current state

- Nine registered templates: NAS addition (ALDAR, IFFCO, HR), NAS deletion,
  ADNIC enrolment and termination, Sukoon addition, Daman addition, and the
  approved `New Log Format -2026`.
- **300 automated tests, 0 failures, 0 skipped** — see
  [TEST_RESULTS.md](TEST_RESULTS.md) for what that does and does not cover.
- Two business rules still open with BMS: the Emirates ID check-digit algorithm,
  and the mapping from a client's deletion wording to the insurers' fixed lists.
