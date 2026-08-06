"""The persistent BMS operational log.

Log rows live in the database and are maintained across days, so the workbook is
regenerated from the record rather than being the record. Two things this module
exists to make possible:

* **Later completion.** The six post-submission columns start blank and are
  filled only when a user records the corresponding event -- possibly days after
  the case was exported, possibly after it was closed. Every such update is
  audited with its previous value.
* **Date-range output.** BMS reports on the log by day, by client, by insurer and
  by status, so entries are queried and exported on demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from . import audit
from .config import Settings, settings
from .models import Case, CaseStatus, LogEntry
from .outputs import log as log_output
from .storage import ExportStorage

# The controlled Status vocabulary from the workbook's own validation list.
LOG_STATUSES = ("PENDING TO INSURER", "PENDING TO CLIENT", "BOOKED", "CLOSED")

# Fields a user may edit directly on a log row. Everything else is derived from
# the case and its members, and the calculated columns are never touched at all.
EDITABLE_FIELDS = ("status", "remarks", "remarks2")


@dataclass(frozen=True)
class LogFilter:
    date_from: date | None = None
    date_to: date | None = None
    client_name: str | None = None
    insurer: str | None = None
    status: str | None = None
    entry_type: str | None = None

    def describe(self) -> str:
        parts = []
        if self.date_from:
            parts.append(f"from {self.date_from.isoformat()}")
        if self.date_to:
            parts.append(f"to {self.date_to.isoformat()}")
        for label, value in (
            ("client", self.client_name),
            ("insurer", self.insurer),
            ("status", self.status),
            ("type", self.entry_type),
        ):
            if value:
                parts.append(f"{label} {value}")
        return ", ".join(parts) or "all entries"

    def slug(self) -> str:
        if self.date_from and self.date_to:
            return f"{self.date_from.isoformat()}_{self.date_to.isoformat()}"
        if self.date_from:
            return f"from-{self.date_from.isoformat()}"
        if self.date_to:
            return f"to-{self.date_to.isoformat()}"
        return "all"


def _apply(query: Select, criteria: LogFilter) -> Select:
    if criteria.client_name:
        query = query.where(LogEntry.client_name == criteria.client_name)
    if criteria.insurer:
        query = query.where(LogEntry.insurer == criteria.insurer)
    if criteria.status:
        query = query.where(LogEntry.status == criteria.status)
    if criteria.entry_type:
        query = query.where(LogEntry.entry_type == criteria.entry_type)
    if criteria.date_from:
        query = query.where(LogEntry.created_at >= datetime.combine(criteria.date_from, datetime.min.time()))
    if criteria.date_to:
        query = query.where(LogEntry.created_at <= datetime.combine(criteria.date_to, datetime.max.time()))
    return query


def query(
    session: Session,
    criteria: LogFilter | None = None,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[LogEntry]:
    statement = select(LogEntry).order_by(LogEntry.created_at)
    if criteria:
        statement = _apply(statement, criteria)
    if offset:
        statement = statement.offset(offset)
    if limit:
        statement = statement.limit(limit)
    return list(session.scalars(statement))


def count(session: Session, criteria: LogFilter | None = None) -> int:
    """How many rows match, without loading any of them.

    The screen needs the total to page through and to label the export, but
    reading every row to count them is what the paging is there to avoid.
    """
    statement = select(func.count()).select_from(LogEntry)
    if criteria:
        statement = _apply(statement, criteria)
    return int(session.scalar(statement) or 0)


def distinct_values(session: Session, column) -> list[str]:
    """Filter options, taken from what is actually in the log."""
    values = session.scalars(select(column).distinct().order_by(column)).all()
    return [value for value in values if value]


# --------------------------------------------------------------- workflow


class NotPermitted(Exception):
    pass


def apply_event(
    session: Session,
    entry: LogEntry,
    event: str,
    values: dict[str, str],
    *,
    actor: str,
    reason: str | None = None,
) -> dict[str, tuple]:
    """Record a post-submission event against a log row.

    Delegates the field rules to `outputs.log.record_event`, which refuses both
    fields outside the event and placeholder values -- a blank cell is the correct
    state until the information genuinely exists.
    """
    changes = log_output.record_event(entry, event, values)
    for field_name, (old, new) in changes.items():
        audit.record_field_change(
            session,
            entity_type="log_entry",
            entity_id=entry.id,
            case_id=entry.case_id,
            field_key=field_name,
            old_value=old,
            new_value=new,
            actor=actor,
            reason=reason or f"workflow event: {event}",
        )
    if changes:
        audit.record(
            session,
            action=f"log.{event}",
            entity_type="log_entry",
            entity_id=entry.id,
            case_id=entry.case_id,
            actor=actor,
            detail={"fields": sorted(changes)},
        )
    session.flush()
    return changes


def update_fields(
    session: Session,
    entry: LogEntry,
    values: dict[str, str],
    *,
    actor: str,
) -> dict[str, tuple]:
    """Edit the operator-owned fields: Status, REMARKS and Remarks2."""
    changes: dict[str, tuple] = {}
    for field_name, value in values.items():
        if field_name not in EDITABLE_FIELDS:
            raise NotPermitted(
                f"{field_name!r} is not editable on a log row; it is derived from the case "
                "or calculated by the workbook"
            )
        if field_name == "status" and value and value not in LOG_STATUSES:
            raise NotPermitted(
                f"{value!r} is not one of the log's controlled statuses: {', '.join(LOG_STATUSES)}"
            )
        old = getattr(entry, field_name)
        new = (value or "").strip() or None
        if (old or "") == (new or ""):
            continue
        setattr(entry, field_name, new)
        changes[field_name] = (old, new)
        audit.record_field_change(
            session,
            entity_type="log_entry",
            entity_id=entry.id,
            case_id=entry.case_id,
            field_key=field_name,
            old_value=old,
            new_value=new,
            actor=actor,
        )
    session.flush()
    return changes


def reopen_case(session: Session, case: Case, *, actor: str, reason: str | None = None) -> None:
    """Reopen a closed case so later information can still be recorded.

    Clearing `documents_purged_at` is deliberate: if the retention purge already
    removed the documents they do not come back, and the field records that it
    happened. Reopening only restores the ability to edit.
    """
    previous = case.status
    case.status = CaseStatus.REVIEW.value
    case.closed_at = None
    audit.record(
        session,
        action="case.reopen",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        old_value=previous,
        new_value=case.status,
        reason=reason,
    )
    session.flush()


# ----------------------------------------------------------------- export


@dataclass
class LogDownload:
    filename: str
    relative_path: str
    sha256: str
    row_count: int


def export_range(
    session: Session,
    criteria: LogFilter,
    *,
    actor: str,
    config: Settings | None = None,
) -> LogDownload:
    """Generate a fresh copy of the approved log workbook for a filter."""
    config = config or settings
    config.ensure_directories()

    entries = query(session, criteria)
    work_dir = config.data_root / "tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"New-Log-Format-2026_{criteria.slug()}_{stamp}.xlsx"
    temporary = work_dir / filename
    try:
        result = log_output.export(entries, repo_root=config.template_root, output=temporary)
        generated = Path(temporary).read_bytes()
    finally:
        # The filename carries a timestamp, so without this every log export a
        # user ever ran left its own scratch copy in var/tmp forever. The purge
        # does not clean that directory, and this is the most-used export there
        # is.
        temporary.unlink(missing_ok=True)

    storage = ExportStorage(config)
    relative, digest = storage.write("_log", filename, generated)

    audit.record(
        session,
        action="log.export",
        entity_type="log",
        actor=actor,
        detail={
            "filter": criteria.describe(),
            "rows": result.row_count,
            "sha256": digest,
        },
    )
    return LogDownload(
        filename=filename,
        relative_path=relative,
        sha256=digest,
        row_count=result.row_count,
    )
