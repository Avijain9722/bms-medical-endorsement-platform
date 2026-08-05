"""Pasted-instruction parsing and archive expansion.

The instruction samples below are modelled on the shapes seen in the supplied
development emails -- prose, a flattened HTML table, and a forwarded newborn
request -- with the personal details replaced.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.config import Settings  # noqa: E402
from bms.intake import archives, instructions  # noqa: E402

PROSE_ADDITION = """
Dear Team,
Kindly Add the employee (ID#T1225) under the CAT D Policy as per the attached
required documents. Note: the employee is married.
Best regards,
"""

TABLE_DEPENDANT = """
Dear Team,
Please find the attached Insurance addition request for your review.
Basatin ID Principal Designation Marital Status CAT (Insurance) Category
82270 RITHICK KANNADHASAN Dependent of (82270) Single Cat-C
Regards,
"""

DELETION = """
Dear Team,
Please cancel the below staff (and dependents if applicable) and share the COC.
BASATIN ID SAP ID Card Number Company Beneficial Name CAT (Insurance)
12373 27804 EH2F-6FJF-LFL2-FLED BASATIN Pashupati Mandal QIC-TECH
Regards,
"""

NEWBORN = """
Please find the attached Insurance addition request NEW BORN for your review.
DATE COMPANY ID NAME MARTIAL STATUS P OR T CAT POLICY
05/02/2026 Khidmah 81411 Ahmad Ali child Single P CAT C QIC
"""


def test_detects_addition_from_prose():
    parsed = instructions.parse(PROSE_ADDITION)
    assert parsed.transaction_type == "addition"
    assert parsed.transaction_confidence > 0.5
    assert parsed.category == "CAT D"
    assert "T1225" in parsed.staff_ids


def test_detects_deletion_and_card_number():
    parsed = instructions.parse(DELETION)
    assert parsed.transaction_type == "deletion"
    assert "EH2F-6FJF-LFL2-FLED" in parsed.card_numbers


def test_reads_a_dependant_row_including_its_principal():
    parsed = instructions.parse(TABLE_DEPENDANT)
    assert parsed.transaction_type == "addition"
    member = next(m for m in parsed.members if m.principal_staff_id == "82270")
    assert member.relation == "Child"
    assert member.marital_status == "Single"
    assert member.category == "CAT C"


def test_detects_a_newborn_request():
    parsed = instructions.parse(NEWBORN)
    assert parsed.is_newborn is True
    assert parsed.transaction_type == "addition"


def test_boilerplate_is_ignored():
    noisy = "CAUTION: This email originated from outside of the organization.\n" + PROSE_ADDITION
    assert instructions.parse(noisy).transaction_type == "addition"


def test_unclear_instruction_reports_rather_than_guesses():
    parsed = instructions.parse("Hello, please see attached.")
    assert parsed.transaction_type is None
    assert any("transaction type" in note for note in parsed.notes)


def test_mixed_wording_keeps_confidence_low():
    """One email in the discovery set said 'CAT B' in the subject and 'CAT D' in
    the body; mixed add/delete wording deserves the same suspicion."""
    parsed = instructions.parse("Please add the spouse and cancel the previous member.")
    assert parsed.transaction_confidence < 0.7


def test_empty_instruction_is_safe():
    parsed = instructions.parse("")
    assert parsed.members == []
    assert parsed.transaction_type is None


# ------------------------------------------------------------------ archives


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_extracts_members_and_records_their_paths():
    data = _zip({"Passport Front.jpg": b"a", "Cancelled.pdf": b"b"})
    result = archives.extract(data, prefix="K18659.zip")
    names = {member.name for member in result.members}
    assert names == {"Passport Front.jpg", "Cancelled.pdf"}
    assert all(member.archive_path.startswith("K18659.zip/") for member in result.members)


def test_nested_archives_are_expanded():
    inner = _zip({"Photo.jpeg": b"x"})
    outer = _zip({"inner.zip": inner, "Passport.jpg": b"y"})
    result = archives.extract(outer, prefix="batch.zip")
    assert {m.name for m in result.members} == {"Photo.jpeg", "Passport.jpg"}


def test_path_traversal_is_refused():
    """A crafted archive must not be able to write outside its own tree."""
    data = _zip({"../../etc/passwd": b"root:x:0:0"})
    result = archives.extract(data)
    assert result.members == []
    assert any("unsafe path" in reason for _, reason in result.skipped)


def test_member_count_is_capped():
    data = _zip({f"file{i}.jpg": b"x" for i in range(30)})
    result = archives.extract(data, config=Settings(max_archive_members=10))
    assert len(result.members) == 10
    assert result.truncated is True


def test_uncompressed_size_is_capped():
    data = _zip({f"file{i}.bin": b"x" * 4096 for i in range(10)})
    result = archives.extract(data, config=Settings(max_archive_bytes=8192))
    assert result.truncated is True


def test_corrupt_archive_is_reported_not_raised():
    result = archives.extract(b"this is not a zip file")
    assert result.members == []
    assert result.skipped
