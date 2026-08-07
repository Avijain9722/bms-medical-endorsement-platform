"""Append-only audit trail.

Every value the platform proposes, every correction a user makes, and every
export is recorded with the previous value, the new value, who did it and when.
Audit rows are written, never updated or deleted -- including by the retention
purge, which removes documents but keeps the record that they existed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent


def record(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    case_id: str | None = None,
    actor: str = "system",
    field_key: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    detail: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        actor=actor,
        field_key=field_key,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
        reason=reason,
        detail=detail,
    )
    session.add(event)
    return event


def record_field_change(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    case_id: str,
    field_key: str,
    old_value: Any,
    new_value: Any,
    actor: str,
    reason: str | None = None,
) -> AuditEvent | None:
    """Record a correction, skipping no-op edits so the trail stays readable."""
    if (old_value or "") == (new_value or ""):
        return None
    return record(
        session,
        action="field.update",
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        actor=actor,
        field_key=field_key,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
    )
