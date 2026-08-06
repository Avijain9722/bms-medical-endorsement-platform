"""Runtime configuration.

Everything that differs between a developer laptop, the BMS prototype host and a
future production server is read from the environment. Nothing here is a client
rule -- business configuration lives in the database so BMS can change it without
a code release.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Third-party binaries shipped inside the project rather than installed into the
# operating system. A locked-down BMS host may not permit an installer to run,
# so a copy that lives in the project folder and travels with it is often the
# only way to get OCR at all.
VENDOR_ROOT = Path(os.environ.get("BMS_VENDOR_ROOT", REPO_ROOT / "vendor")).resolve()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def bundled_tesseract() -> str | None:
    """Path to a Tesseract shipped inside the project, if one is present.

    Looked for at `vendor/tesseract/`. Returns None when nothing is bundled, so
    the caller falls back to whatever is on PATH.
    """
    for name in ("tesseract.exe", "tesseract"):
        candidate = VENDOR_ROOT / "tesseract" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _resolve_tesseract() -> str:
    """An explicit setting always wins, then a bundled copy, then PATH."""
    explicit = os.environ.get("BMS_TESSERACT_CMD")
    if explicit:
        return explicit
    return bundled_tesseract() or "tesseract"


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
    # How often the background purge runs. 0 disables the scheduler entirely, in
    # which case `python3 -m bms.cli purge` can be driven by an external timer.
    purge_interval_minutes: int = int(os.environ.get("BMS_PURGE_INTERVAL_MINUTES", 60))

    # Set true behind TLS so the session cookie is never sent in the clear. It
    # was hard-coded False, which meant enabling it required editing source on
    # the production host.
    cookie_secure: bool = _bool("BMS_COOKIE_SECURE", False)

    # OCR must run locally. No document may be sent to an external service.
    ocr_enabled: bool = _bool("BMS_OCR_ENABLED", True)
    # A copy under vendor/tesseract/ is picked up automatically; BMS_TESSERACT_CMD
    # overrides it. See `bundled_tesseract`.
    tesseract_cmd: str = field(default_factory=_resolve_tesseract)
    ocr_languages: str = os.environ.get("BMS_OCR_LANGUAGES", "eng+ara")

    @property
    def tessdata_dir(self) -> Path | None:
        """The language files that belong to `tesseract_cmd`, if they travel with it.

        A portable Tesseract keeps `tessdata/` beside the executable and cannot
        find it without TESSDATA_PREFIX -- the failure is an unhelpful "Error
        opening data file", so this is what makes a bundled copy actually work.
        A system install needs nothing: it knows its own prefix.
        """
        binary = Path(self.tesseract_cmd)
        if not binary.is_absolute():
            return None
        candidate = binary.resolve().parent / "tessdata"
        return candidate if candidate.is_dir() else None

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
