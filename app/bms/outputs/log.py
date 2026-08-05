"""The approved New Log Format -2026 output.

The workbook is the fixed BMS operational log template. It is populated from
`LogEntry` rows held in the database, so the log survives restarts and is
rebuilt from the record rather than from anything the browser holds.

Three rules, all confirmed by BMS and all enforced here:

* one row per member;
* the six post-submission columns stay genuinely blank until a user records the
  corresponding event -- never `N/A`, `Pending`, `-` or `0`;
* the formula and helper columns are never written, so `TAT`, `Current Date`,
  `Pending with Insurer since`, `Aged Pending` and the SUMMARY feeders keep
  working as the workbook author intended.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from ..models import Case, LogEntry, Member, TransactionType
from ..registry.generate import generate
from ..templates.specs import BMS_LOG

# Workflow status -> the log's controlled Status vocabulary, per BMS:
#   Pending to Insurer -- awaiting the insurer
#   Pending to Client  -- awaiting information from the client
#   Closed             -- the endorsement is complete
#   Booked             -- the invoice or accounting entry is accounted for
STATUS_BY_CASE_STATE = {
    "draft": "PENDING TO CLIENT",
    "processing": "PENDING TO CLIENT",
    "review": "PENDING TO CLIENT",
    "approved": "PENDING TO INSURER",
    "exported": "PENDING TO INSURER",
    "closed": "CLOSED",
}

ENTRY_TYPE_BY_TRANSACTION = {
    TransactionType.ADDITION.value: "ADDITION",
    TransactionType.DELETION.value: "DELETION",
}

LOG_DATE_FORMAT = "%d/%m/%Y"


def _as_log_date(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    try:
        return date.fromisoformat(iso_value).strftime(LOG_DATE_FORMAT)
    except ValueError:
        return iso_value


def build_entry(case: Case, member: Member, *, shared_by: str | None) -> LogEntry:
    """Create the log row for one member.

    Post-submission columns are deliberately not set. They are left NULL and are
    only filled in later, by an explicit user action, through `record_event`.
    """
    return LogEntry(
        case_id=case.id,
        member_id=member.id,
        shared_by=shared_by,
        client_name=case.client_name,
        sub_group=case.sub_group,
        insurer=case.insurer,
        policy_no=case.policy_no,
        beneficiary_name=member.full_name or None,
        relation=(member.relation or "").upper() or None,
        category=member.category,
        staff_id=member.staff_id,
        emirates_id=member.emirates_id,
        entry_type=ENTRY_TYPE_BY_TRANSACTION.get(member.transaction_type),
        effective_date=_as_log_date(member.effective_date),
        status=STATUS_BY_CASE_STATE.get(case.status, "PENDING TO CLIENT"),
        request_receive_date=_as_log_date(
            case.email_received_date.date().isoformat() if case.email_received_date else None
        ),
    )


# The only fields a later workflow action may fill in.
RECORDABLE_EVENTS = {
    "submitted_to_insurer": ("request_sent_date_to_insurer", "request_ref_no"),
    "card_received": ("card_receive_and_sent_date", "card_no"),
    "voucher_recorded": ("saiba_voucher_no",),
    "invoice_dated": ("bbm_invoice_date",),
}


class NotRecordable(Exception):
    pass


def record_event(entry: LogEntry, event: str, values: dict[str, str]) -> dict[str, tuple]:
    """Fill post-submission fields as the result of an explicit user action.

    Returns {field: (old, new)} so the caller can write the audit trail. Refuses
    placeholder values outright -- a blank cell is the correct state until the
    information genuinely exists.
    """
    allowed = RECORDABLE_EVENTS.get(event)
    if allowed is None:
        raise NotRecordable(f"{event!r} is not a recognised workflow event")

    placeholders = {"n/a", "na", "pending", "-", "0", "none", "nil", "tbc"}
    changes: dict[str, tuple] = {}
    for field_name, value in values.items():
        if field_name not in allowed:
            raise NotRecordable(
                f"{field_name!r} cannot be set by the {event!r} event"
            )
        if value is None or str(value).strip() == "":
            continue
        if str(value).strip().lower() in placeholders:
            raise NotRecordable(
                f"{field_name} must be left blank until the real value exists, "
                f"not set to {value!r}"
            )
        old = getattr(entry, field_name)
        setattr(entry, field_name, str(value).strip())
        changes[field_name] = (old, str(value).strip())
    return changes


@dataclass
class LogExport:
    path: Path
    row_count: int


def _row_for(entry: LogEntry, serial: int) -> dict:
    row = {
        "sr_no": serial,
        "shared_by": entry.shared_by,
        "client_name": entry.client_name,
        "sub_group": entry.sub_group,
        "insurer": entry.insurer,
        "policy_no": entry.policy_no,
        "beneficiary_name": entry.beneficiary_name,
        "relation": entry.relation,
        "category": entry.category,
        "staff_id": entry.staff_id,
        "emirates_id": entry.emirates_id,
        "entry_type": entry.entry_type,
        "effective_date": entry.effective_date,
        "status": entry.status,
        "request_receive_date": entry.request_receive_date,
        "remarks": entry.remarks,
        "remarks2": entry.remarks2,
        # Post-submission columns. Present only when a value genuinely exists;
        # a None here means the writer leaves an empty cell.
        "request_ref_no": entry.request_ref_no,
        "request_sent_date_to_insurer": entry.request_sent_date_to_insurer,
        "card_no": entry.card_no,
        "card_receive_and_sent_date": entry.card_receive_and_sent_date,
        "saiba_voucher_no": entry.saiba_voucher_no,
        "bbm_invoice_date": entry.bbm_invoice_date,
    }
    return {key: value for key, value in row.items() if value not in (None, "")}


def export(
    entries: Iterable[LogEntry],
    *,
    repo_root: str | Path,
    output: str | Path,
) -> LogExport:
    """Write a fresh copy of the approved log workbook for the given entries.

    The master ships carrying 1,101 example rows; `blank_first` strips them at
    registration so the produced log contains only BMS's own cases.
    """
    rows = [_row_for(entry, index) for index, entry in enumerate(entries, start=1)]
    generate(BMS_LOG, rows, repo_root=repo_root, output=output, blank_first=True)
    return LogExport(path=Path(output), row_count=len(rows))
