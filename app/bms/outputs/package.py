"""Supporting-document ZIP packaging.

Insurers want the member workbook plus a folder of supporting documents, and NAS
matches them by filename: whatever is written into the Member Photo or Medical
Declaration File column must appear verbatim in the ZIP.

So naming is generated here and fed back to the row builder, never typed twice.
The originals stay untouched in storage for audit; this produces a copy under
approved names, with a manifest tying every file back to its member and portal
row.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..models import CaseFile, DocumentType, Member

# Documents that belong in a submission package. Working files a client happened
# to attach -- their own spreadsheets, covering notes -- are not sent onward.
INCLUDED_TYPES = frozenset(
    {
        DocumentType.PASSPORT.value,
        DocumentType.EMIRATES_ID.value,
        DocumentType.VISA.value,
        DocumentType.ENTRY_PERMIT.value,
        DocumentType.BIRTH_CERTIFICATE.value,
        DocumentType.INSURANCE_CARD.value,
        DocumentType.CERTIFICATE_OF_CONTINUITY.value,
        DocumentType.MEDICAL_DECLARATION.value,
        DocumentType.PHOTOGRAPH.value,
        DocumentType.CANCELLATION.value,
    }
)

# Short, stable labels used in generated filenames.
TYPE_LABELS = {
    DocumentType.PASSPORT.value: "Passport",
    DocumentType.EMIRATES_ID.value: "EID",
    DocumentType.VISA.value: "Visa",
    DocumentType.ENTRY_PERMIT.value: "Entry Permit",
    DocumentType.BIRTH_CERTIFICATE.value: "Birth Certificate",
    DocumentType.INSURANCE_CARD.value: "Insurance Card",
    DocumentType.CERTIFICATE_OF_CONTINUITY.value: "COC",
    DocumentType.MEDICAL_DECLARATION.value: "MAF",
    DocumentType.PHOTOGRAPH.value: "Photo",
    DocumentType.CANCELLATION.value: "Cancellation",
}

MANIFEST_NAME = "manifest.csv"
UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]")


def safe_component(value: str, *, fallback: str = "Member") -> str:
    """A filename fragment safe on Windows, macOS and Linux alike.

    Separators are removed rather than escaped, and runs of dots are collapsed:
    a staff ID is not supposed to contain either, and `..` in a name confuses
    enough downstream tools to be worth removing even though the ZIP writer here
    is not vulnerable to traversal.
    """
    cleaned = UNSAFE.sub(" ", value or "")
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:60] or fallback


def output_name(member: Member, record: CaseFile, *, sequence: int | None = None) -> str:
    """The name a document is given inside the package.

    `<Staff ID or name> - <Document type><suffix>`, which is what the BMS team
    already writes by hand and what the insurer's matching expects.
    """
    identity = safe_component(member.staff_id or member.full_name, fallback="Member")
    label = TYPE_LABELS.get(record.document_type, "Document")
    extension = PurePosixPath(record.original_name).suffix.lower()
    ordinal = f" {sequence}" if sequence else ""
    return f"{identity} - {label}{ordinal}{extension}"


@dataclass
class PackagedFile:
    member_id: str
    member_name: str
    staff_id: str | None
    portal_row: int
    document_type: str
    original_name: str
    output_name: str
    sha256: str
    byte_size: int


@dataclass
class PackageResult:
    data: bytes = b""
    files: list[PackagedFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # Member id -> the filename to write into the workbook's attachment columns.
    photo_names: dict[str, str] = field(default_factory=dict)
    declaration_names: dict[str, str] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.files)


def build(
    members: list[Member],
    documents_by_member: dict[str, list[CaseFile]],
    *,
    read_bytes,
    case_reference: str,
    first_data_row: int = 2,
) -> PackageResult:
    """Assemble the supporting-document ZIP.

    `read_bytes(sha256) -> bytes` supplies content, so this stays independent of
    how storage is arranged. `portal_row` is the workbook row the member occupies,
    which is what makes the manifest usable when an insurer queries one line.
    """
    result = PackageResult()
    seen: dict[str, str] = {}
    used_names: set[str] = set()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, member in enumerate(members):
            portal_row = first_data_row + index
            documents = documents_by_member.get(member.id, [])
            counts: dict[str, int] = {}

            for record in documents:
                if record.document_type not in INCLUDED_TYPES:
                    result.skipped.append(
                        (record.original_name, f"{record.document_type} is not sent to the insurer")
                    )
                    continue

                # Identical content already packaged: reference it rather than
                # shipping the same scan twice under two names.
                if record.sha256 in seen:
                    result.skipped.append(
                        (record.original_name, f"duplicate content of {seen[record.sha256]}")
                    )
                    continue

                counts[record.document_type] = counts.get(record.document_type, 0) + 1
                sequence = counts[record.document_type]
                name = output_name(member, record, sequence=sequence if sequence > 1 else None)

                # Filenames must be unique across the whole package: NAS matches
                # on the name alone, so a collision would attach the wrong file.
                candidate, suffix = name, 1
                while candidate.lower() in used_names:
                    suffix += 1
                    stem = PurePosixPath(name).stem
                    extension = PurePosixPath(name).suffix
                    candidate = f"{stem} ({suffix}){extension}"
                name = candidate
                used_names.add(name.lower())

                payload = read_bytes(record.sha256)
                archive.writestr(name, payload)
                seen[record.sha256] = name

                result.files.append(
                    PackagedFile(
                        member_id=member.id,
                        member_name=member.full_name,
                        staff_id=member.staff_id,
                        portal_row=portal_row,
                        document_type=record.document_type,
                        original_name=record.archive_path or record.original_name,
                        output_name=name,
                        sha256=record.sha256,
                        byte_size=record.byte_size,
                    )
                )

                if record.document_type == DocumentType.PHOTOGRAPH.value:
                    result.photo_names.setdefault(member.id, name)
                elif record.document_type == DocumentType.MEDICAL_DECLARATION.value:
                    result.declaration_names.setdefault(member.id, name)

        manifest = io.StringIO()
        writer = csv.writer(manifest)
        writer.writerow(
            [
                "Case",
                "Portal row",
                "Member",
                "Staff ID",
                "Document type",
                "Original filename",
                "Packaged filename",
                "SHA-256",
                "Bytes",
            ]
        )
        for item in result.files:
            writer.writerow(
                [
                    case_reference,
                    item.portal_row,
                    item.member_name,
                    item.staff_id or "",
                    item.document_type,
                    item.original_name,
                    item.output_name,
                    item.sha256,
                    item.byte_size,
                ]
            )
        archive.writestr(MANIFEST_NAME, manifest.getvalue())

    result.data = buffer.getvalue()
    return result
