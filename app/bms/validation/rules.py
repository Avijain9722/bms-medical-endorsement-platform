"""Validation and exception rules.

Two principles, both from the master prompt and confirmed by BMS:

* Nothing is invented. A missing value stays missing and is raised for a human.
* A CRITICAL flag blocks export and cannot be overridden. Correcting the
  underlying data is the only way forward.

Client-specific rules (required documents by insurer and transaction type,
category to salary band, and so on) belong in configuration, not here. This
module holds the rules BMS has confirmed apply across the board.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from ..models import DocumentType, Severity, TransactionType

# Confirmed by BMS: where a mandatory newborn value is not yet available, this
# placeholder is used -- except for Emirates ID, which must keep its real format.
NEWBORN_PLACEHOLDER = "111111"

# Confirmed by BMS: a member with no surname carries this in Last Name.
NO_SURNAME_MARKER = "...."

# Confirmed by BMS: Dubai deletions take effect 30 days after cancellation.
DUBAI_DELETION_OFFSET_DAYS = 30

EID_FORMAT_RE = re.compile(r"^784-\d{4}-\d{7}-\d$")

ADDITION_REQUIRED_FIELDS = (
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("date_of_birth", "Date of birth"),
    ("gender", "Gender"),
    ("nationality", "Nationality"),
    ("relation", "Relation"),
    ("category", "Category"),
    ("effective_date", "Effective date"),
)

DELETION_REQUIRED_FIELDS = (("effective_date", "Effective date"),)

# Below this a value is shown but must be confirmed before approval.
LOW_CONFIDENCE = 0.75


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    field_key: str | None = None

    @property
    def blocks_export(self) -> bool:
        return self.severity is Severity.CRITICAL


def resolve_effective_date(
    transaction_type: str,
    *,
    processing_date: date,
    emirate: str | None = None,
    cancellation_date: date | None = None,
) -> tuple[date, str]:
    """Determine the effective date, per the rules BMS confirmed.

    Additions use the current processing date. Deletions use the processing date
    for Abu Dhabi policies, and cancellation + 30 days for Dubai policies.
    """
    if transaction_type == TransactionType.DELETION.value:
        if (emirate or "").strip().lower() == "dubai":
            if cancellation_date is None:
                return processing_date, (
                    "Dubai deletion needs a cancellation date; "
                    "falling back to the processing date pending confirmation"
                )
            return (
                cancellation_date + timedelta(days=DUBAI_DELETION_OFFSET_DAYS),
                f"Dubai policy: cancellation date + {DUBAI_DELETION_OFFSET_DAYS} days",
            )
        return processing_date, "Abu Dhabi policy: current processing date"
    return processing_date, "Addition: current processing date"


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_member(
    member,
    *,
    processing_date: date | None = None,
    documents: list | None = None,
    is_newborn: bool = False,
) -> list[Finding]:
    """Validate one member record. Returns every finding, worst first."""
    processing_date = processing_date or date.today()
    documents = documents or []
    findings: list[Finding] = []
    is_addition = member.transaction_type == TransactionType.ADDITION.value

    required = ADDITION_REQUIRED_FIELDS if is_addition else DELETION_REQUIRED_FIELDS
    for field_key, label in required:
        value = getattr(member, field_key, None)
        if value in (None, ""):
            findings.append(
                Finding(
                    code="missing_mandatory_field",
                    severity=Severity.CRITICAL,
                    message=f"{label} is required and has not been provided.",
                    field_key=field_key,
                )
            )

    # A deletion needs something that identifies the member to the insurer.
    if not is_addition and not (member.member_card_no or member.emirates_id):
        findings.append(
            Finding(
                code="deletion_identifier_missing",
                severity=Severity.CRITICAL,
                message=(
                    "A deletion needs either the member card number or the Emirates ID; "
                    "neither is present."
                ),
                field_key="member_card_no",
            )
        )

    # Emirates ID must keep its real format. BMS was explicit that the newborn
    # placeholder must never be used here.
    if member.emirates_id:
        if member.emirates_id == NEWBORN_PLACEHOLDER:
            findings.append(
                Finding(
                    code="eid_placeholder_misuse",
                    severity=Severity.CRITICAL,
                    message=(
                        "The newborn placeholder must never be used for Emirates ID. "
                        "Enter the real Emirates ID or leave it blank."
                    ),
                    field_key="emirates_id",
                )
            )
        elif not EID_FORMAT_RE.match(member.emirates_id):
            findings.append(
                Finding(
                    code="eid_format_invalid",
                    severity=Severity.CRITICAL,
                    message=(
                        f"Emirates ID {member.emirates_id!r} is not in the 784-YYYY-NNNNNNN-C "
                        "format."
                    ),
                    field_key="emirates_id",
                )
            )
    elif is_addition and not is_newborn:
        findings.append(
            Finding(
                code="eid_missing",
                severity=Severity.REVIEW_REQUIRED,
                message="No Emirates ID was found. Confirm it or record why it is unavailable.",
                field_key="emirates_id",
            )
        )

    # Retroactive dates are not permitted as a normal processing option.
    effective = _parse_iso(member.effective_date)
    if effective and effective < processing_date:
        findings.append(
            Finding(
                code="retroactive_effective_date",
                severity=Severity.CRITICAL,
                message=(
                    f"Effective date {member.effective_date} is before the processing date "
                    f"{processing_date.isoformat()}. Retroactive dates are not permitted."
                ),
                field_key="effective_date",
            )
        )

    dob = _parse_iso(member.date_of_birth)
    if dob and dob > processing_date:
        findings.append(
            Finding(
                code="dob_in_future",
                severity=Severity.CRITICAL,
                message=f"Date of birth {member.date_of_birth} is in the future.",
                field_key="date_of_birth",
            )
        )

    expiry = _parse_iso(member.passport_expiry)
    if expiry and expiry < processing_date:
        findings.append(
            Finding(
                code="passport_expired",
                severity=Severity.REVIEW_REQUIRED,
                message=f"Passport expired on {member.passport_expiry}.",
                field_key="passport_expiry",
            )
        )

    # A dependant must resolve to a principal before it can be exported.
    if is_addition and (member.relation or "").lower() not in ("", "principal"):
        if not member.principal_member_id and not member.principal_card_no:
            findings.append(
                Finding(
                    code="principal_unresolved",
                    severity=Severity.CRITICAL,
                    message=(
                        f"{member.relation} has no confirmed principal. Link the principal or "
                        "supply the principal card number."
                    ),
                    field_key="principal_card_no",
                )
            )

    if member.last_name == NO_SURNAME_MARKER:
        findings.append(
            Finding(
                code="single_name_member",
                severity=Severity.WARNING,
                message=(
                    f"No surname supplied; '{NO_SURNAME_MARKER}' has been entered in Last Name "
                    "per the BMS rule."
                ),
                field_key="last_name",
            )
        )

    # Low-confidence readings must be confirmed, not silently trusted.
    provenance = member.provenance or {}
    for field_key, meta in provenance.items():
        if not isinstance(meta, dict):
            continue
        confidence = meta.get("confidence")
        if confidence is None or meta.get("reviewed"):
            continue
        if confidence < LOW_CONFIDENCE and getattr(member, field_key, None):
            findings.append(
                Finding(
                    code="low_confidence_value",
                    severity=Severity.REVIEW_REQUIRED,
                    message=(
                        f"{field_key.replace('_', ' ')} was read with {confidence:.0%} confidence "
                        f"from {meta.get('source_name', 'a document')}. Confirm it."
                    ),
                    field_key=field_key,
                )
            )

    if is_newborn and is_addition:
        if not member.birth_certificate_number:
            findings.append(
                Finding(
                    code="newborn_missing_birth_certificate",
                    severity=Severity.REVIEW_REQUIRED,
                    message="Newborn addition without a birth certificate number.",
                    field_key="birth_certificate_number",
                )
            )
        has_birth_certificate = any(
            getattr(document, "document_type", None) == DocumentType.BIRTH_CERTIFICATE.value
            for document in documents
        )
        if not has_birth_certificate:
            findings.append(
                Finding(
                    code="newborn_missing_document",
                    severity=Severity.REVIEW_REQUIRED,
                    message="No birth certificate document is attached to this newborn.",
                )
            )

    if is_addition and not documents:
        findings.append(
            Finding(
                code="no_supporting_documents",
                severity=Severity.REVIEW_REQUIRED,
                message="No supporting documents are attached to this member.",
            )
        )

    order = {
        Severity.CRITICAL: 0,
        Severity.REVIEW_REQUIRED: 1,
        Severity.WARNING: 2,
        Severity.PASSED: 3,
    }
    findings.sort(key=lambda finding: order[finding.severity])
    return findings


def check_duplicates(members: list) -> list[tuple[object, Finding]]:
    """Flag members that look like the same person twice in one case."""
    findings: list[tuple[object, Finding]] = []
    seen: dict[tuple[str, str], object] = {}
    for member in members:
        for key in ("passport_no", "emirates_id", "staff_id"):
            value = getattr(member, key, None)
            if not value:
                continue
            identity = (key, re.sub(r"[^A-Za-z0-9]", "", str(value)).upper())
            if identity in seen and seen[identity] is not member:
                findings.append(
                    (
                        member,
                        Finding(
                            code="duplicate_member",
                            severity=Severity.CRITICAL,
                            message=(
                                f"Another member in this case has the same {key.replace('_', ' ')} "
                                f"({value}). Resolve the duplicate before export."
                            ),
                            field_key=key,
                        ),
                    )
                )
            else:
                seen[identity] = member
    return findings
