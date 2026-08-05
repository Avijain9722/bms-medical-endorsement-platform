"""Runtime configuration.

Everything that differs between a developer laptop, the BMS prototype host and a
future production server is read from the environment. Nothing here is a client
rule -- business configuration lives in the database so BMS can change it without
a code release.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Deployment settings.

    `database_url` accepts any SQLAlchemy URL. The schema is written to be
    PostgreSQL-ready -- explicit string lengths, timezone-aware timestamps, no
    SQLite-only types -- so moving from the prototype to PostgreSQL is a URL
    change plus a migration, not a rewrite.
    """

    database_url: str = os.environ.get("BMS_DATABASE_URL", "sqlite:///./bms_prototype.db")

    # Uploaded originals and generated outputs are written to disk, never held in
    # the browser. Cases survive refresh, logout, restart and end of day.
    data_root: Path = Path(os.environ.get("BMS_DATA_ROOT", REPO_ROOT / "var")).resolve()

    # Source workbooks. Read-only: the platform never writes here.
    template_root: Path = Path(os.environ.get("BMS_TEMPLATE_ROOT", REPO_ROOT)).resolve()

    max_upload_bytes: int = int(os.environ.get("BMS_MAX_UPLOAD_BYTES", 64 * 1024 * 1024))
    max_archive_members: int = int(os.environ.get("BMS_MAX_ARCHIVE_MEMBERS", 500))
    max_archive_bytes: int = int(os.environ.get("BMS_MAX_ARCHIVE_BYTES", 512 * 1024 * 1024))

    # Documents and generated files are purged this long after a case is closed.
    # The clock starts at closure, so an open case never loses its evidence.
    # Case records, member rows, log entries and the audit trail are kept.
    document_retention_hours: int = int(os.environ.get("BMS_DOCUMENT_RETENTION_HOURS", 36))
    purge_enabled: bool = _bool("BMS_PURGE_ENABLED", True)

    # OCR must run locally. No document may be sent to an external service.
    ocr_enabled: bool = _bool("BMS_OCR_ENABLED", True)
    tesseract_cmd: str = os.environ.get("BMS_TESSERACT_CMD", "tesseract")
    ocr_languages: str = os.environ.get("BMS_OCR_LANGUAGES", "eng+ara")

    @property
    def storage_root(self) -> Path:
        return self.data_root / "storage"

    @property
    def export_root(self) -> Path:
        return self.data_root / "exports"

    def ensure_directories(self) -> None:
        for path in (self.data_root, self.storage_root, self.export_root):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
