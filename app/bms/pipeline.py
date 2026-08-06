"""Case processing service.

The orchestration layer between the web tier and the domain modules. Everything
it does is persisted immediately -- there is no in-memory case state and nothing
depends on the browser holding anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import audit
from .config import Settings, settings
from .intake import archives, instructions, scanning
from .matching.grouping import DocumentIdentity, MemberGroup, archive_root, group_documents, link_principals
from .models import (
    Case,
    CaseFile,
    CaseStatus,
    DocumentType,
    Export,
    ExtractedField,
    FileStatus,
    LogEntry,
    Member,
    ReviewFlag,
    Severity,
    TransactionType,
    new_id,
)
from .ocr.classify import classify
from .ocr.fields import extract_fields
from .ocr.text import TextPipeline
from .outputs import log as log_output
from .outputs import nas as nas_output
from .outputs import package as package_output
from .outputs import recalc as recalc_output
from .storage import ExportStorage, Storage
from .templates.specs import SPECS_BY_KEY
from .validation.rules import (
    NO_SURNAME_MARKER,
    check_duplicates,
    check_member,
    resolve_effective_date,
)

# Fields the pipeline may propose onto a member from document evidence.
MEMBER_FIELD_KEYS = (
    "first_name",
    "middle_name",
    "last_name",
    "date_of_birth",
    "gender",
    "nationality",
    "passport_no",
    "passport_expiry",
    "emirates_id",
    "unified_no",
    "visa_file_number",
    "birth_certificate_number",
    "staff_id",
    "member_card_no",
)


@dataclass
class UploadOutcome:
    stored: list[CaseFile]
    duplicates: list[str]
    skipped: list[tuple[str, str]]
    password_protected: bool
    # Rejected by the virus scanner. These are never written to storage.
    infected: list[tuple[str, str]] = dataclass_field(default_factory=list)


# How many times a colliding reference is re-derived before giving up. A
# collision needs two cases created in the same instant, so one retry is
# realistically enough; three costs nothing and covers a burst.
REFERENCE_ATTEMPTS = 3


def next_reference(session: Session) -> str:
    """Sequential, human-quotable case reference.

    Derived from the highest reference already issued this year, not from how
    many rows exist. Counting them repeats a reference as soon as any case is
    deleted, and `Case.reference` is unique -- so the next case created after a
    deletion failed on the constraint and the operator got a 500.

    Zero padding to five digits makes the lexical maximum the numeric one.
    """
    year = date.today().year
    prefix = f"BMS-{year}-"
    highest = session.scalar(
        select(func.max(Case.reference)).where(Case.reference.like(f"{prefix}%"))
    )
    previous = 0
    if highest:
        suffix = highest.removeprefix(prefix)
        if suffix.isdigit():
            previous = int(suffix)
    return f"{prefix}{previous + 1:05d}"


def create_case(
    session: Session,
    *,
    client_name: str,
    insurer: str,
    transaction_type: str,
    actor: str,
    bms_comments: str = "",
    **fields,
) -> Case:
    # Two operators creating a case in the same instant read the same highest
    # reference and one of them loses on the unique constraint. Retrying re-reads
    # it inside a savepoint, so the loser gets the next number instead of a 500.
    for attempt in range(REFERENCE_ATTEMPTS):
        case = Case(
            reference=next_reference(session),
            client_name=client_name,
            insurer=insurer,
            transaction_type=transaction_type,
            bms_comments=bms_comments,
            **fields,
        )
        try:
            with session.begin_nested():
                session.add(case)
                session.flush()
            break
        except IntegrityError:
            if attempt == REFERENCE_ATTEMPTS - 1:
                raise

    audit.record(
        session,
        action="case.create",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={"reference": case.reference, "transaction_type": transaction_type},
    )
    return case


def record_oversize_upload(
    session: Session,
    case: Case,
    filename: str,
    *,
    actor: str,
) -> UploadOutcome:
    """Refuse a file that was abandoned mid-read for exceeding the size limit.

    The web tier stops reading as soon as a body crosses the limit, so the bytes
    never exist here to hand to `add_upload`. The refusal is recorded the same
    way either route reaches it, so the operator sees one message.
    """
    audit.record(
        session,
        action="file.rejected",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={"filename": filename, "reason": "exceeds the maximum upload size"},
    )
    return UploadOutcome(
        stored=[],
        duplicates=[],
        skipped=[(filename, "exceeds the maximum upload size")],
        password_protected=False,
        infected=[],
    )


def add_upload(
    session: Session,
    case: Case,
    filename: str,
    data: bytes,
    *,
    actor: str,
    config: Settings | None = None,
) -> UploadOutcome:
    """Store an upload, expanding archives into their members."""
    config = config or settings
    config.ensure_directories()
    storage = Storage(config)

    outcome = UploadOutcome(
        stored=[], duplicates=[], skipped=[], password_protected=False, infected=[]
    )

    if len(data) > config.max_upload_bytes:
        outcome.skipped.append((filename, "exceeds the maximum upload size"))
        return outcome

    if archives.is_archive(filename):
        extraction = archives.extract(data, prefix=filename, config=config)
        outcome.password_protected = extraction.password_protected
        outcome.skipped.extend(extraction.skipped)
        payloads = [(m.name, m.archive_path, m.data) for m in extraction.members]
        if extraction.truncated:
            outcome.skipped.append((filename, "archive truncated at the configured limit"))
    else:
        payloads = [(filename, None, data)]

    existing = {
        row.sha256
        for row in session.scalars(select(CaseFile).where(CaseFile.case_id == case.id))
    }

    for name, archive_path, payload in payloads:
        # Scan before storing: an infected payload never reaches the filesystem.
        verdict = scanning.scan_bytes(payload, filename=name)
        if verdict.blocks_upload:
            outcome.infected.append((archive_path or name, verdict.detail))
            continue

        digest, _ = storage.put_bytes(payload)
        if digest in existing:
            outcome.duplicates.append(archive_path or name)
            continue
        existing.add(digest)

        record = CaseFile(
            case_id=case.id,
            original_name=name,
            archive_path=archive_path,
            sha256=digest,
            byte_size=len(payload),
            media_type=_guess_media_type(name),
            status=FileStatus.STORED.value,
            scan_verdict=verdict.verdict,
            scan_detail=verdict.detail[:2000] if verdict.detail else None,
        )
        session.add(record)
        outcome.stored.append(record)

    session.flush()
    audit.record(
        session,
        action="case.upload",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={
            "filename": filename,
            "stored": len(outcome.stored),
            "duplicates": len(outcome.duplicates),
            "skipped": len(outcome.skipped),
            "infected": len(outcome.infected),
            "scanner": scanning.describe_host()["engine"],
        },
    )
    return outcome


def _guess_media_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".zip": "application/zip",
        ".txt": "text/plain",
        ".csv": "text/csv",
    }.get(suffix, "application/octet-stream")


def analyse_files(
    session: Session,
    case: Case,
    *,
    actor: str,
    config: Settings | None = None,
    pipeline: TextPipeline | None = None,
) -> list[CaseFile]:
    """Extract text, classify and read fields for every unprocessed file."""
    config = config or settings
    storage = Storage(config)
    pipeline = pipeline or TextPipeline()

    files = list(
        session.scalars(
            select(CaseFile).where(
                CaseFile.case_id == case.id,
                CaseFile.status == FileStatus.STORED.value,
            )
        )
    )

    for record in files:
        data = storage.get_bytes(record.sha256)
        result = pipeline.extract(record.original_name, data)

        if result is None:
            # No engine could read it. Say so plainly rather than pretending the
            # document was empty.
            record.status = FileStatus.OCR_UNAVAILABLE.value
            record.notes = (
                "No local text or OCR engine could read this file. "
                f"Available engines: {', '.join(pipeline.available_engines()) or 'none'}."
            )
        else:
            record.status = FileStatus.EXTRACTED.value
            record.extracted_text = result.text[:200_000]
            record.text_source = result.source

            for extraction in extract_fields(result.text):
                session.add(
                    ExtractedField(
                        file_id=record.id,
                        field_key=extraction.field_key,
                        raw_value=extraction.raw_value,
                        normalised_value=extraction.normalised_value,
                        # A reading is only as good as the text it came from.
                        confidence=round(extraction.confidence * result.confidence, 3),
                        method=extraction.method,
                    )
                )

        classification = classify(record.original_name, record.extracted_text)
        if not record.manually_classified:
            record.document_type = classification.document_type.value
            record.classification_confidence = classification.confidence

    session.flush()
    audit.record(
        session,
        action="case.analyse",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={"files": len(files), "engines": pipeline.available_engines()},
    )
    return files


def _document_evidence(
    session: Session, case: Case
) -> tuple[dict[str, CaseFile], dict[str, list[ExtractedField]]]:
    """Every file on the case and the fields read out of it, in two queries.

    This used to be four times as many round trips as there are documents: the
    files were selected twice and the fields once per file, twice over, because
    identity building and member building each did their own loading. A 120
    document batch -- one large client email -- issued 267 queries to assemble
    what two statements return.

    The fields are fetched by joining through the case rather than by an IN list
    of file ids, so a batch larger than the driver's parameter limit still works.
    """
    files = {
        record.id: record
        for record in session.scalars(select(CaseFile).where(CaseFile.case_id == case.id))
    }
    fields: dict[str, list[ExtractedField]] = {file_id: [] for file_id in files}
    for field in session.scalars(
        select(ExtractedField)
        .join(CaseFile, ExtractedField.file_id == CaseFile.id)
        .where(CaseFile.case_id == case.id)
    ):
        fields.setdefault(field.file_id, []).append(field)
    return files, fields


def _identities(
    file_by_id: dict[str, CaseFile], field_by_file: dict[str, list[ExtractedField]]
) -> list[DocumentIdentity]:
    identities = []
    for record in file_by_id.values():
        fields = field_by_file.get(record.id, [])
        identifiers = {
            f.field_key: f.normalised_value
            for f in fields
            if f.normalised_value
        }
        names = {
            value
            for key, value in identifiers.items()
            if key in ("first_name", "last_name") and value
        }
        identities.append(
            DocumentIdentity(
                file_id=record.id,
                filename=record.original_name,
                archive_root=archive_root(record.archive_path),
                identifiers=identifiers,
                names=names,
                confidence=max((f.confidence for f in fields), default=0.0),
            )
        )
    return identities


def build_members(
    session: Session,
    case: Case,
    *,
    actor: str,
    processing_date: date | None = None,
) -> list[Member]:
    """Identify members, attach their documents, and raise review flags.

    Re-runnable: members created by a previous run are cleared first so a user
    can upload more documents and reprocess without duplicates. Manual
    corrections are lost by design -- the UI warns before reprocessing.
    """
    processing_date = processing_date or date.today()

    for existing in session.scalars(select(Member).where(Member.case_id == case.id)):
        session.delete(existing)
    for flag in session.scalars(select(ReviewFlag).where(ReviewFlag.case_id == case.id)):
        session.delete(flag)
    session.flush()

    parsed = instructions.parse(case.bms_comments or "")

    groups: list[MemberGroup] = []
    for index, hint in enumerate(parsed.members):
        groups.append(
            MemberGroup(
                key=f"hint:{index}",
                staff_id=hint.staff_id,
                display_name=hint.name,
                relation=hint.relation,
                marital_status=hint.marital_status,
                category=hint.category or parsed.category,
                principal_staff_id=hint.principal_staff_id,
                member_card_no=hint.member_card_no,
                identifiers={k: v for k, v in {"staff_id": hint.staff_id}.items() if v},
                names={hint.name} if hint.name else set(),
            )
        )

    file_by_id, field_by_file = _document_evidence(session, case)
    groups, unassigned = group_documents(groups, _identities(file_by_id, field_by_file))

    effective_date, effective_reason = resolve_effective_date(
        case.transaction_type,
        processing_date=processing_date,
        emirate=_case_emirate(case, session),
    )

    members: list[Member] = []
    for index, group in enumerate(groups):
        member = Member(
            # Assigned here rather than by the flush, so a member's documents can
            # be pointed at it without a round trip per member. This loop used to
            # flush inside itself for exactly that id.
            id=new_id(),
            case_id=case.id,
            row_index=index,
            transaction_type=case.transaction_type,
            relation=group.relation or ("Principal" if case.transaction_type == TransactionType.ADDITION.value else None),
            marital_status=group.marital_status,
            category=group.category or case.category,
            contract_name=case.contract_name,
            staff_id=group.staff_id,
            member_card_no=group.member_card_no,
            effective_date=effective_date.isoformat(),
            provenance={},
        )

        if group.display_name:
            parts = group.display_name.split()
            member.first_name = parts[0]
            if len(parts) > 2:
                member.middle_name = " ".join(parts[1:-1])
            if len(parts) > 1:
                member.last_name = parts[-1]

        # Take the best-supported value for each field across the member's docs.
        best: dict[str, tuple[float, str, CaseFile]] = {}
        for file_id in group.file_ids:
            record = file_by_id.get(file_id)
            if record is None:
                continue
            record.member_id = None  # cleared, then re-pointed below
            for field in field_by_file.get(file_id, []):
                if field.field_key not in MEMBER_FIELD_KEYS or not field.normalised_value:
                    continue
                current = best.get(field.field_key)
                if current is None or field.confidence > current[0]:
                    best[field.field_key] = (field.confidence, field.normalised_value, record)

        provenance = {}
        for field_key, (confidence, value, record) in best.items():
            # Never overwrite something the instruction stated explicitly.
            if getattr(member, field_key, None):
                continue
            setattr(member, field_key, value)
            provenance[field_key] = {
                "confidence": confidence,
                "source": record.id,
                "source_name": record.original_name,
                "reviewed": False,
            }
        member.provenance = provenance

        if member.first_name and not member.last_name:
            member.last_name = NO_SURNAME_MARKER

        session.add(member)

        for file_id in group.file_ids:
            record = file_by_id.get(file_id)
            if record is not None:
                record.member_id = member.id

        members.append(member)

    # One flush for the whole batch rather than one per member.
    session.flush()

    _link_principals(session, groups, members)
    _raise_flags(
        session,
        case,
        members,
        parsed=parsed,
        unassigned=unassigned,
        file_by_id=file_by_id,
        processing_date=processing_date,
        effective_reason=effective_reason,
    )

    case.status = CaseStatus.REVIEW.value
    session.flush()

    audit.record(
        session,
        action="case.build_members",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={
            "members": len(members),
            "unassigned_documents": len(unassigned),
            "effective_date": effective_date.isoformat(),
            "effective_date_reason": effective_reason,
        },
    )
    return members


def _case_emirate(case: Case, session: Session | None = None) -> str | None:
    """The emirate governing the deletion effective-date rule.

    Registered configuration first: the sub-group or policy in the client master
    records it explicitly. Only when neither is set does this fall back to
    reading DXB/AUH out of free text, which is a guess and is reported as such.
    """
    if session is not None:
        from .models import ClientPolicy, SubGroup

        if case.sub_group_id:
            sub_group = session.get(SubGroup, case.sub_group_id)
            if sub_group and sub_group.emirate:
                return sub_group.emirate
        if case.client_policy_id:
            policy = session.get(ClientPolicy, case.client_policy_id)
            if policy and policy.emirate:
                return policy.emirate

    haystack = " ".join(
        part for part in (case.policy_no, case.contract_name, case.sub_group, case.category) if part
    ).lower()
    if "dxb" in haystack or "dubai" in haystack:
        return "Dubai"
    if "auh" in haystack or "abu dhabi" in haystack:
        return "Abu Dhabi"
    return None


def _link_principals(session: Session, groups: list[MemberGroup], members: list[Member]) -> None:
    by_key = {group.key: member for group, member in zip(groups, members)}
    for group, principal_group, reason in link_principals(groups):
        member = by_key.get(group.key)
        if member is None:
            continue
        if principal_group is not None:
            principal = by_key.get(principal_group.key)
            if principal is not None:
                member.principal_member_id = principal.id
                member.principal_card_no = member.principal_card_no or principal.member_card_no
                (member.provenance or {})["principal_member_id"] = {
                    "confidence": 0.9,
                    "source_name": "instruction",
                    "reviewed": False,
                    "reason": reason,
                }
    session.flush()


def _raise_flags(
    session: Session,
    case: Case,
    members: list[Member],
    *,
    parsed,
    unassigned,
    file_by_id,
    processing_date: date,
    effective_reason: str,
) -> None:
    def add(code: str, severity: Severity, message: str, member: Member | None = None, field_key=None):
        session.add(
            ReviewFlag(
                case_id=case.id,
                member_id=member.id if member else None,
                code=code,
                severity=severity.value,
                message=message,
                field_key=field_key,
            )
        )

    for note in parsed.notes:
        add("instruction_parse", Severity.REVIEW_REQUIRED, note)

    if parsed.transaction_type and parsed.transaction_type != case.transaction_type:
        add(
            "transaction_type_conflict",
            Severity.REVIEW_REQUIRED,
            f"The pasted instruction reads as a {parsed.transaction_type} but the case is "
            f"set to {case.transaction_type}. Confirm which is correct.",
        )

    for identity in unassigned:
        add(
            "document_unassigned",
            Severity.REVIEW_REQUIRED,
            f"{identity.filename} could not be matched to a member. Assign it manually.",
        )

    for record in file_by_id.values():
        if record.scan_verdict in ("unavailable", "error"):
            add(
                "not_virus_scanned",
                Severity.WARNING,
                f"{record.original_name} was not virus scanned ({record.scan_verdict}). "
                "Install a local scanner, or confirm the file's provenance before opening it.",
            )
        if record.status == FileStatus.OCR_UNAVAILABLE.value:
            add(
                "ocr_unavailable",
                Severity.REVIEW_REQUIRED,
                f"{record.original_name} could not be read locally; enter its values by hand.",
            )
        if record.document_type == DocumentType.UNKNOWN.value:
            add(
                "document_unclassified",
                Severity.REVIEW_REQUIRED,
                f"{record.original_name} could not be classified. Set its document type.",
            )

    for member in members:
        documents = [record for record in file_by_id.values() if record.member_id == member.id]
        for finding in check_member(
            member,
            processing_date=processing_date,
            documents=documents,
            is_newborn=parsed.is_newborn,
        ):
            add(finding.code, finding.severity, finding.message, member, finding.field_key)

    for member, finding in check_duplicates(members):
        add(finding.code, finding.severity, finding.message, member, finding.field_key)


# ----------------------------------------------------------------- review ops


def update_member_field(
    session: Session,
    member: Member,
    field_key: str,
    value: str | None,
    *,
    actor: str,
    reason: str | None = None,
) -> None:
    """Apply a reviewer correction, with an audit entry and provenance update."""
    if not hasattr(member, field_key):
        raise AttributeError(f"members have no field {field_key!r}")
    old = getattr(member, field_key)
    cleaned = (value or "").strip() or None
    setattr(member, field_key, cleaned)

    provenance = dict(member.provenance or {})
    entry = dict(provenance.get(field_key) or {})
    entry.update({"confidence": 1.0, "reviewed": True, "source_name": f"{actor} (manual)"})
    provenance[field_key] = entry
    member.provenance = provenance

    audit.record_field_change(
        session,
        entity_type="member",
        entity_id=member.id,
        case_id=member.case_id,
        field_key=field_key,
        old_value=old,
        new_value=cleaned,
        actor=actor,
        reason=reason,
    )


def revalidate_member(session: Session, case: Case, member: Member, *, processing_date=None) -> None:
    """Re-run validation for one member after corrections."""
    for flag in session.scalars(
        select(ReviewFlag).where(ReviewFlag.member_id == member.id)
    ):
        session.delete(flag)
    session.flush()

    documents = list(session.scalars(select(CaseFile).where(CaseFile.member_id == member.id)))
    is_newborn = instructions.parse(case.bms_comments or "").is_newborn
    for finding in check_member(
        member,
        processing_date=processing_date or date.today(),
        documents=documents,
        is_newborn=is_newborn,
    ):
        session.add(
            ReviewFlag(
                case_id=case.id,
                member_id=member.id,
                code=finding.code,
                severity=finding.severity.value,
                message=finding.message,
                field_key=finding.field_key,
            )
        )
    session.flush()


def blocking_flags(session: Session, case: Case) -> list[ReviewFlag]:
    """Open critical flags. While any exist, nothing may be exported."""
    return list(
        session.scalars(
            select(ReviewFlag).where(
                ReviewFlag.case_id == case.id,
                ReviewFlag.severity == Severity.CRITICAL.value,
                ReviewFlag.resolved.is_(False),
            )
        )
    )


def approve_member(session: Session, case: Case, member: Member, *, actor: str) -> None:
    """Approve one member. Refuses while that member has a critical flag."""
    critical = [
        flag
        for flag in session.scalars(
            select(ReviewFlag).where(
                ReviewFlag.member_id == member.id,
                ReviewFlag.severity == Severity.CRITICAL.value,
                ReviewFlag.resolved.is_(False),
            )
        )
    ]
    if critical:
        raise PermissionError(
            f"{member.full_name or 'this member'} has {len(critical)} unresolved critical "
            "issue(s). Correct the underlying data first -- critical errors cannot be overridden."
        )
    member.approved = True
    member.approved_at = datetime.now(timezone.utc)
    audit.record(
        session,
        action="member.approve",
        entity_type="member",
        entity_id=member.id,
        case_id=case.id,
        actor=actor,
    )


class ExportBlocked(Exception):
    pass


def export_case(
    session: Session,
    case: Case,
    *,
    template_key: str,
    actor: str,
    config: Settings | None = None,
) -> tuple[Export, Export]:
    """Generate the portal workbook and the BMS log for a case."""
    config = config or settings
    config.ensure_directories()

    blocking = blocking_flags(session, case)
    if blocking:
        raise ExportBlocked(
            f"{len(blocking)} unresolved critical issue(s) block this export: "
            + "; ".join(flag.message for flag in blocking[:3])
        )

    members = [
        member
        for member in session.scalars(
            select(Member).where(Member.case_id == case.id).order_by(Member.row_index)
        )
    ]
    approved = [member for member in members if member.approved]
    if not approved:
        raise ExportBlocked("No members have been approved for export.")

    export_storage = ExportStorage(config)
    work_dir = config.data_root / "tmp"
    work_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(config)

    # --- supporting documents ------------------------------------------------
    # Built first: the packager decides the filenames, and the workbook's
    # attachment columns must carry exactly those names for the insurer to match
    # each document to its member.
    documents_by_member = {
        member.id: list(
            session.scalars(select(CaseFile).where(CaseFile.member_id == member.id))
        )
        for member in approved
    }
    package = package_output.build(
        approved,
        documents_by_member,
        read_bytes=storage.get_bytes,
        case_reference=case.reference,
        first_data_row=SPECS_BY_KEY[template_key].first_data_row,
    )
    attachments = {
        member.id: {
            "photo": package.photo_names.get(member.id),
            "declaration": package.declaration_names.get(member.id),
        }
        for member in approved
    }

    # --- portal workbook -----------------------------------------------------
    suffix = ".xlsx"
    portal_path = work_dir / f"{case.reference}-portal{suffix}"
    try:
        built = nas_output.generate_workbook(
            template_key,
            approved,
            repo_root=config.template_root,
            output=portal_path,
            attachments=attachments,
        )
        portal_bytes = portal_path.read_bytes()
    finally:
        # The workbook is only staged here on the way to export storage, which
        # holds the copy that matters. Left behind, every case ever exported kept
        # a scratch copy in var/tmp for the life of the installation -- and the
        # retention purge does not look in that directory.
        portal_path.unlink(missing_ok=True)
    portal_name = f"{case.reference}-{template_key}{suffix}"
    relative, digest = export_storage.write(case.reference, portal_name, portal_bytes)
    portal_recalc = recalc_output.recalculate(
        export_storage.absolute(relative), template_key=template_key
    )
    portal_export = Export(
        case_id=case.id,
        kind="portal",
        template_key=template_key,
        filename=portal_name,
        relative_path=relative,
        sha256=digest,
        fingerprint_ok=True,
        row_count=len(built.rows),
        recalculated=portal_recalc.performed,
        recalc_detail=portal_recalc.detail,
    )
    session.add(portal_export)

    # --- supporting-document ZIP --------------------------------------------
    package_export = None
    if package.file_count:
        package_name = f"{case.reference}-supporting-documents.zip"
        package_relative, package_digest = export_storage.write(
            case.reference, package_name, package.data
        )
        package_export = Export(
            case_id=case.id,
            kind="zip",
            template_key=None,
            filename=package_name,
            relative_path=package_relative,
            sha256=package_digest,
            fingerprint_ok=True,
            row_count=package.file_count,
        )
        session.add(package_export)

    # --- BMS log -------------------------------------------------------------
    shared_by = _shared_by(session, case)
    entries = list(session.scalars(select(LogEntry).where(LogEntry.case_id == case.id)))
    if not entries:
        for member in approved:
            entry = log_output.build_entry(case, member, shared_by=shared_by)
            session.add(entry)
            entries.append(entry)
        session.flush()

    log_path = work_dir / f"{case.reference}-log.xlsx"
    try:
        log_result = log_output.export(entries, repo_root=config.template_root, output=log_path)
        log_bytes = log_path.read_bytes()
    finally:
        log_path.unlink(missing_ok=True)
    log_name = f"{case.reference}-New-Log-Format-2026.xlsx"
    log_relative, log_digest = export_storage.write(case.reference, log_name, log_bytes)
    log_recalc = recalc_output.recalculate(
        export_storage.absolute(log_relative), template_key="bms.log.2026"
    )
    log_export = Export(
        case_id=case.id,
        kind="log",
        template_key="bms.log.2026",
        filename=log_name,
        relative_path=log_relative,
        sha256=log_digest,
        fingerprint_ok=True,
        row_count=log_result.row_count,
        recalculated=log_recalc.performed,
        recalc_detail=log_recalc.detail,
    )
    session.add(log_export)

    case.status = CaseStatus.EXPORTED.value
    session.flush()

    audit.record(
        session,
        action="case.export",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
        detail={
            "template_key": template_key,
            "portal_rows": len(built.rows),
            "log_rows": log_result.row_count,
            "portal_sha256": digest,
            "log_sha256": log_digest,
            "package_files": package.file_count,
            "package_skipped": len(package.skipped),
        },
    )
    return portal_export, log_export


def _shared_by(session: Session, case: Case) -> str | None:
    from .models import User

    if not case.owner_id:
        return None
    user = session.get(User, case.owner_id)
    return user.log_associate or user.display_name if user else None


def close_case(session: Session, case: Case, *, actor: str) -> None:
    case.status = CaseStatus.CLOSED.value
    case.closed_at = datetime.now(timezone.utc)
    for entry in session.scalars(select(LogEntry).where(LogEntry.case_id == case.id)):
        entry.status = "CLOSED"
    audit.record(
        session,
        action="case.close",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        actor=actor,
    )


# --------------------------------------------------------------------- purge


@dataclass
class PurgeResult:
    cases: int
    files: int
    exports: int


def purge_closed_cases(
    session: Session,
    *,
    now: datetime | None = None,
    config: Settings | None = None,
    actor: str = "system",
) -> PurgeResult:
    """Delete documents and generated files for cases closed long enough ago.

    The clock starts at closure, so an open case never loses its evidence. Case
    records, member rows, log entries and the audit trail are always kept -- only
    the bytes go.
    """
    config = config or settings
    if not config.purge_enabled:
        return PurgeResult(0, 0, 0)

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=config.document_retention_hours)
    storage = Storage(config)
    export_storage = ExportStorage(config)

    result = PurgeResult(0, 0, 0)
    candidates = session.scalars(
        select(Case).where(
            Case.status == CaseStatus.CLOSED.value,
            Case.closed_at.is_not(None),
            Case.documents_purged_at.is_(None),
        )
    ).all()

    for case in candidates:
        closed_at = case.closed_at
        if closed_at is not None and closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        if closed_at is None or closed_at > cutoff:
            continue

        files = list(session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)))
        # Which of this case's blobs another case still references. One query for
        # the case, rather than a COUNT per file -- a purge sweep clearing a
        # day's closed cases ran one round trip per document across all of them.
        still_referenced = set(
            session.scalars(
                select(CaseFile.sha256)
                .where(
                    CaseFile.sha256.in_([record.sha256 for record in files]),
                    CaseFile.case_id != case.id,
                )
                .distinct()
            )
        )
        for record in files:
            # Only drop the blob when no other case still references it.
            if record.sha256 not in still_referenced:
                storage.delete(record.sha256)
            record.status = FileStatus.PURGED.value
            record.extracted_text = None
            result.files += 1

        result.exports += export_storage.delete_case(case.reference)
        case.documents_purged_at = now
        result.cases += 1

        audit.record(
            session,
            action="case.purge_documents",
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            actor=actor,
            detail={"files": len(files), "retention_hours": config.document_retention_hours},
        )

    session.flush()
    return result
