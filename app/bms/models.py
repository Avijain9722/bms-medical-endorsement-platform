"""Persistent domain model.

Written to be PostgreSQL-ready while running on SQLite for the prototype:
explicit string lengths, timezone-aware timestamps, JSON columns via the
dialect-neutral `JSON` type, and string UUID primary keys rather than
autoincrement integers so records can be created before they are flushed.

Retention: `CaseFile` rows and their bytes are purged after a case closes.
`Case`, `Member`, `LogEntry` and `AuditEvent` are kept indefinitely -- they are
the operational record BMS reports from.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------- enumerations


class CaseStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    EXPORTED = "exported"
    CLOSED = "closed"


class TransactionType(str, enum.Enum):
    ADDITION = "addition"
    DELETION = "deletion"


class Severity(str, enum.Enum):
    """Review severity. CRITICAL blocks export and cannot be overridden."""

    PASSED = "passed"
    WARNING = "warning"
    REVIEW_REQUIRED = "review_required"
    CRITICAL = "critical"


class DocumentType(str, enum.Enum):
    PASSPORT = "passport"
    EMIRATES_ID = "emirates_id"
    VISA = "visa"
    ENTRY_PERMIT = "entry_permit"
    BIRTH_CERTIFICATE = "birth_certificate"
    INSURANCE_CARD = "insurance_card"
    CERTIFICATE_OF_CONTINUITY = "coc"
    MEDICAL_DECLARATION = "medical_declaration"
    PHOTOGRAPH = "photograph"
    CANCELLATION = "cancellation"
    CLIENT_SHEET = "client_sheet"
    UNKNOWN = "unknown"


class FileStatus(str, enum.Enum):
    STORED = "stored"
    EXTRACTED = "extracted"
    OCR_UNAVAILABLE = "ocr_unavailable"
    UNREADABLE = "unreadable"
    PASSWORD_PROTECTED = "password_protected"
    DUPLICATE = "duplicate"
    PURGED = "purged"


# -------------------------------------------------------------------- tables


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # The BMS log's SHARED BY column is a controlled list of processing
    # associates; this binds the signed-in user to that list.
    log_associate: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # BMS asked for one operational role for everyone. This flag gates only the
    # administration area -- user accounts and the client master -- not any
    # case-processing capability, and it grants no override of a critical error.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------- client master
# The centralised library BMS asked for: clients, their sub-groups, their legal
# entities and their insurer policies, entered by hand or imported from Excel and
# shared by every user. Case creation reads from here instead of free text, so
# the contract name written into a portal workbook is a value BMS registered
# rather than something a user typed.


class Client(Base):
    """A company BMS administers medical cover for."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sub_groups: Mapped[list["SubGroup"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    legal_entities: Mapped[list["LegalEntity"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    policies: Mapped[list["ClientPolicy"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class SubGroup(Base):
    """A sub-group of a company, as the BMS log records it."""

    __tablename__ = "sub_groups"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_sub_group_per_client"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Drives the deletion effective-date rule: Abu Dhabi uses the processing
    # date, Dubai uses cancellation + 30 days. Recording it here means the rule
    # is applied from registered configuration rather than inferred from text.
    emirate: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped[Client] = relationship(back_populates="sub_groups")


class LegalEntity(Base):
    """A legal entity of a client.

    `contract_name` is the exact literal the insurer's template expects in its
    Contract Name dropdown -- spelling and spacing included, defects and all.
    """

    __tablename__ = "legal_entities"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_entity_per_client"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(191), nullable=False)
    contract_name: Mapped[str | None] = mapped_column(String(191))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped[Client] = relationship(back_populates="legal_entities")


class ClientPolicy(Base):
    """An insurer policy held by a client, and the template it exports to."""

    __tablename__ = "client_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), nullable=False)
    legal_entity_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("legal_entities.id"))

    insurer: Mapped[str] = mapped_column(String(128), nullable=False)
    network: Mapped[str | None] = mapped_column(String(128))
    policy_no: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    emirate: Mapped[str | None] = mapped_column(String(32))
    addition_template_key: Mapped[str | None] = mapped_column(String(64))
    deletion_template_key: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    client: Mapped[Client] = relationship(back_populates="policies")
    legal_entity: Mapped[LegalEntity | None] = relationship()


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # Denormalised names are kept alongside the foreign keys so a closed case
    # still reads correctly if a client is later renamed in the master.
    client_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("clients.id"))
    sub_group_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sub_groups.id"))
    legal_entity_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("legal_entities.id"))
    client_policy_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("client_policies.id"))

    client_name: Mapped[str] = mapped_column(String(128), nullable=False)
    sub_group: Mapped[str | None] = mapped_column(String(128))
    legal_entity: Mapped[str | None] = mapped_column(String(191))
    insurer: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_no: Mapped[str | None] = mapped_column(String(64))
    contract_name: Mapped[str | None] = mapped_column(String(191))
    category: Mapped[str | None] = mapped_column(String(64))

    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(64))

    email_subject: Mapped[str | None] = mapped_column(String(512))
    email_received_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str | None] = mapped_column(String(255))

    # The pasted client email body. Stored verbatim and never modified -- it is
    # the source document for everything parsed out of it.
    bms_comments: Mapped[str] = mapped_column(Text, default="")
    user_notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.DRAFT.value)
    owner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    documents_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list["CaseFile"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    members: Mapped[list["Member"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    flags: Mapped[list["ReviewFlag"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    exports: Mapped[list["Export"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseFile(Base):
    __tablename__ = "case_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), nullable=False)

    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # Path inside the uploaded archive, when the file came from a ZIP.
    archive_path: Mapped[str | None] = mapped_column(String(1024))
    parent_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("case_files.id"))

    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[str] = mapped_column(String(32), default=FileStatus.STORED.value)
    document_type: Mapped[str] = mapped_column(String(48), default=DocumentType.UNKNOWN.value)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    manually_classified: Mapped[bool] = mapped_column(Boolean, default=False)

    extracted_text: Mapped[str | None] = mapped_column(Text)
    text_source: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)

    member_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="files")
    member: Mapped["Member | None"] = relationship(back_populates="documents")
    fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class ExtractedField(Base):
    """One value read out of one document, with where it came from.

    Every critical value the platform proposes must be traceable to a source
    file and carry a confidence, so a reviewer can see why it was suggested.
    """

    __tablename__ = "extracted_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("case_files.id"), nullable=False)

    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(512))
    normalised_value: Mapped[str | None] = mapped_column(String(512))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(48), default="regex")
    page: Mapped[int | None] = mapped_column(Integer)

    file: Mapped[CaseFile] = relationship(back_populates="fields")


class Member(Base):
    """One internal record per human in the case.

    Portal values are never written straight from OCR. They are assembled here,
    reviewed, corrected and approved, and only then mapped into a template.
    """

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, default=0)

    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)

    first_name: Mapped[str | None] = mapped_column(String(128))
    middle_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    date_of_birth: Mapped[str | None] = mapped_column(String(32))
    gender: Mapped[str | None] = mapped_column(String(16))
    marital_status: Mapped[str | None] = mapped_column(String(32))
    nationality: Mapped[str | None] = mapped_column(String(64))

    passport_no: Mapped[str | None] = mapped_column(String(64))
    passport_expiry: Mapped[str | None] = mapped_column(String(32))
    emirates_id: Mapped[str | None] = mapped_column(String(32))
    unified_no: Mapped[str | None] = mapped_column(String(32))
    visa_file_number: Mapped[str | None] = mapped_column(String(64))
    birth_certificate_number: Mapped[str | None] = mapped_column(String(64))

    staff_id: Mapped[str | None] = mapped_column(String(64))
    relation: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(64))
    contract_name: Mapped[str | None] = mapped_column(String(191))
    effective_date: Mapped[str | None] = mapped_column(String(32))

    # Deletion-side fields.
    member_card_no: Mapped[str | None] = mapped_column(String(64))
    deletion_reason: Mapped[str | None] = mapped_column(String(64))
    cancellation_date: Mapped[str | None] = mapped_column(String(32))

    principal_member_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("members.id"))
    principal_card_no: Mapped[str | None] = mapped_column(String(64))

    email: Mapped[str | None] = mapped_column(String(255))
    mobile_no: Mapped[str | None] = mapped_column(String(64))

    # Per-field provenance: {field_key: {"source": file id, "confidence": 0-1,
    # "method": "mrz", "reviewed": bool}}. Kept as JSON so adding a tracked
    # field does not require a migration.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)

    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="members")
    documents: Mapped[list[CaseFile]] = relationship(back_populates="member")
    flags: Mapped[list["ReviewFlag"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p and p != "....").strip()


class ReviewFlag(Base):
    """An exception raised against a case or a member."""

    __tablename__ = "review_flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), nullable=False)
    member_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("members.id"))

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)

    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="flags")
    member: Mapped[Member | None] = relationship(back_populates="flags")


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), nullable=False)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # portal | log | zip
    template_key: Mapped[str | None] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)

    created_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="exports")


class LogEntry(Base):
    """One BMS operational log row per member.

    The six post-submission columns are nullable and stay NULL until a user
    records the corresponding event. They are never defaulted to a placeholder.
    """

    __tablename__ = "log_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("cases.id"), nullable=False)
    member_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("members.id"))

    shared_by: Mapped[str | None] = mapped_column(String(64))
    client_name: Mapped[str | None] = mapped_column(String(128))
    sub_group: Mapped[str | None] = mapped_column(String(128))
    insurer: Mapped[str | None] = mapped_column(String(128))
    policy_no: Mapped[str | None] = mapped_column(String(64))
    beneficiary_name: Mapped[str | None] = mapped_column(String(191))
    relation: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(64))
    staff_id: Mapped[str | None] = mapped_column(String(64))
    emirates_id: Mapped[str | None] = mapped_column(String(32))
    entry_type: Mapped[str | None] = mapped_column(String(48))
    effective_date: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(48))
    request_receive_date: Mapped[str | None] = mapped_column(String(32))
    remarks: Mapped[str | None] = mapped_column(Text)
    remarks2: Mapped[str | None] = mapped_column(Text)

    # --- post-submission: NULL until an explicit user action records them ---
    request_ref_no: Mapped[str | None] = mapped_column(String(128))
    request_sent_date_to_insurer: Mapped[str | None] = mapped_column(String(32))
    card_no: Mapped[str | None] = mapped_column(String(64))
    card_receive_and_sent_date: Mapped[str | None] = mapped_column(String(32))
    saiba_voucher_no: Mapped[str | None] = mapped_column(String(64))
    bbm_invoice_date: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditEvent(Base):
    """Append-only audit trail. Never updated, never deleted."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)

    field_key: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
