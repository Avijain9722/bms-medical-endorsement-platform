"""Member grouping, principal linkage and the validation rules."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms.matching.grouping import (  # noqa: E402
    DocumentIdentity,
    MemberGroup,
    archive_root,
    group_documents,
    link_principals,
)
from bms.models import DocumentType, Severity, TransactionType  # noqa: E402
from bms.validation.rules import (  # noqa: E402
    NEWBORN_PLACEHOLDER,
    NO_SURNAME_MARKER,
    check_duplicates,
    check_member,
    resolve_effective_date,
)


def member(**overrides):
    """A member record shaped like the ORM object, without needing a database."""
    base = dict(
        id="m1",
        transaction_type=TransactionType.ADDITION.value,
        first_name="Rithick",
        middle_name=None,
        last_name="Kannadhasan",
        date_of_birth="1990-03-07",
        gender="Male",
        marital_status="Single",
        nationality="India",
        passport_no="P1234567",
        passport_expiry="2030-01-01",
        emirates_id="784-1998-0432182-8",
        unified_no="58620894",
        visa_file_number=None,
        birth_certificate_number=None,
        staff_id="82270",
        relation="Principal",
        category="CAT C",
        contract_name="BASATIN",
        effective_date=date.today().isoformat(),
        member_card_no=None,
        deletion_reason=None,
        principal_member_id=None,
        principal_card_no=None,
        provenance={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- grouping


def test_archive_root_identifies_the_source_zip():
    assert archive_root("K18659.zip/Passport Front.jpg") == "K18659.zip"
    assert archive_root(None) is None


def test_documents_group_onto_the_member_sharing_an_identifier():
    group = MemberGroup(key="hint:0", staff_id="82270", identifiers={"staff_id": "82270"})
    passport = DocumentIdentity(
        file_id="f1",
        filename="Passport.jpg",
        identifiers={"passport_no": "P1234567", "staff_id": "82270"},
    )
    visa = DocumentIdentity(
        file_id="f2", filename="Visa.pdf", identifiers={"passport_no": "P1234567"}
    )
    groups, unassigned = group_documents([group], [passport, visa])
    assert unassigned == []
    assert set(groups[0].file_ids) == {"f1", "f2"}


def test_documents_from_one_archive_stay_together():
    """Clients send one ZIP per staff ID; that is strong grouping evidence."""
    group = MemberGroup(key="hint:0", staff_id="K18659")
    photo = DocumentIdentity(
        file_id="f1", filename="PIC.jpg", archive_root="K18659.zip", identifiers={}
    )
    passport = DocumentIdentity(
        file_id="f2",
        filename="Passport Front.jpg",
        archive_root="K18659.zip",
        identifiers={"passport_no": "X9999999"},
    )
    groups, unassigned = group_documents([group], [passport, photo])
    assert unassigned == []
    assert len(groups) == 1
    assert set(groups[0].file_ids) == {"f1", "f2"}


def test_a_document_identifying_an_unmentioned_person_creates_a_member():
    """The instruction is not always complete; a passport is still a person."""
    group = MemberGroup(key="hint:0", staff_id="1111")
    stranger = DocumentIdentity(
        file_id="f9", filename="Passport.pdf", identifiers={"passport_no": "Z7777777"}
    )
    groups, unassigned = group_documents([group], [stranger])
    assert unassigned == []
    assert len(groups) == 2
    assert groups[1].origin == "document"


def test_documents_with_no_identity_are_left_for_the_reviewer():
    group = MemberGroup(key="hint:0", staff_id="1111")
    blank = DocumentIdentity(file_id="f5", filename="scan.jpg", identifiers={})
    groups, unassigned = group_documents([group], [blank])
    assert [item.file_id for item in unassigned] == ["f5"]
    assert groups[0].file_ids == []


def test_principal_linkage_by_staff_id():
    principal = MemberGroup(key="p", staff_id="82270", relation="Principal")
    child = MemberGroup(key="c", staff_id="82271", relation="Child", principal_staff_id="82270")
    resolved = link_principals([principal, child])
    assert len(resolved) == 1
    dependant, found, reason = resolved[0]
    assert dependant is child and found is principal
    assert "staff id" in reason


def test_dependant_without_a_principal_is_reported():
    child = MemberGroup(key="c", staff_id="99", relation="Child", principal_staff_id="12345")
    _, found, reason = link_principals([child])[0]
    assert found is None
    assert "not in this case" in reason


# -------------------------------------------------------------- date rules


def test_addition_uses_the_processing_date():
    today = date(2026, 8, 5)
    value, reason = resolve_effective_date("addition", processing_date=today)
    assert value == today
    assert "processing date" in reason


def test_abu_dhabi_deletion_uses_the_processing_date():
    today = date(2026, 8, 5)
    value, _ = resolve_effective_date(
        "deletion", processing_date=today, emirate="Abu Dhabi", cancellation_date=date(2026, 7, 1)
    )
    assert value == today


def test_dubai_deletion_is_thirty_days_after_cancellation():
    value, reason = resolve_effective_date(
        "deletion",
        processing_date=date(2026, 8, 5),
        emirate="Dubai",
        cancellation_date=date(2026, 7, 1),
    )
    assert value == date(2026, 7, 31)
    assert "30 days" in reason


def test_dubai_deletion_without_a_cancellation_date_says_so():
    value, reason = resolve_effective_date(
        "deletion", processing_date=date(2026, 8, 5), emirate="Dubai"
    )
    assert value == date(2026, 8, 5)
    assert "cancellation date" in reason


# ------------------------------------------------------------- member rules


def test_a_complete_member_raises_nothing_critical():
    findings = check_member(member(), documents=[SimpleNamespace(document_type="passport")])
    assert [f for f in findings if f.severity is Severity.CRITICAL] == []


def test_missing_mandatory_fields_block_export():
    findings = check_member(member(first_name=None, category=None))
    codes = {f.code for f in findings if f.blocks_export}
    assert "missing_mandatory_field" in codes


def test_retroactive_effective_date_is_critical():
    """BMS was explicit: no retroactive tolerance."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    findings = check_member(member(effective_date=yesterday))
    assert any(f.code == "retroactive_effective_date" and f.blocks_export for f in findings)


def test_newborn_placeholder_is_refused_for_emirates_id():
    findings = check_member(member(emirates_id=NEWBORN_PLACEHOLDER))
    assert any(f.code == "eid_placeholder_misuse" and f.blocks_export for f in findings)


def test_malformed_emirates_id_is_critical():
    findings = check_member(member(emirates_id="800220260210090601960"))
    assert any(f.code == "eid_format_invalid" and f.blocks_export for f in findings)


def test_dependant_without_a_principal_cannot_be_exported():
    findings = check_member(member(relation="Child"))
    assert any(f.code == "principal_unresolved" and f.blocks_export for f in findings)


def test_dependant_with_a_principal_card_passes():
    findings = check_member(member(relation="Child", principal_card_no="EH2F-6FJF-LFL2-FLED"))
    assert not any(f.code == "principal_unresolved" for f in findings)


def test_single_name_marker_is_a_warning_not_a_blocker():
    findings = check_member(member(last_name=NO_SURNAME_MARKER))
    single = next(f for f in findings if f.code == "single_name_member")
    assert single.severity is Severity.WARNING
    assert not single.blocks_export


def test_expired_passport_is_review_not_critical():
    findings = check_member(member(passport_expiry="2020-01-01"))
    expired = next(f for f in findings if f.code == "passport_expired")
    assert expired.severity is Severity.REVIEW_REQUIRED


def test_low_confidence_values_must_be_confirmed():
    findings = check_member(
        member(provenance={"passport_no": {"confidence": 0.4, "source_name": "scan.jpg"}})
    )
    assert any(f.code == "low_confidence_value" for f in findings)


def test_confirmed_values_stop_being_flagged():
    findings = check_member(
        member(
            provenance={
                "passport_no": {"confidence": 0.4, "source_name": "scan.jpg", "reviewed": True}
            }
        )
    )
    assert not any(f.code == "low_confidence_value" for f in findings)


def test_deletion_needs_a_card_number_or_emirates_id():
    findings = check_member(
        member(transaction_type=TransactionType.DELETION.value, emirates_id=None, member_card_no=None)
    )
    assert any(f.code == "deletion_identifier_missing" and f.blocks_export for f in findings)


def test_deletion_with_a_card_number_passes():
    findings = check_member(
        member(
            transaction_type=TransactionType.DELETION.value,
            emirates_id=None,
            member_card_no="EH2F-6FJF-LFL2-FLED",
        )
    )
    assert not any(f.code == "deletion_identifier_missing" for f in findings)


def test_newborn_without_a_birth_certificate_is_raised():
    findings = check_member(member(relation="Child", principal_card_no="X"), is_newborn=True)
    codes = {f.code for f in findings}
    assert "newborn_missing_birth_certificate" in codes
    assert "newborn_missing_document" in codes


def test_newborn_with_a_birth_certificate_document_is_accepted():
    findings = check_member(
        member(relation="Child", principal_card_no="X", birth_certificate_number="111111"),
        documents=[SimpleNamespace(document_type=DocumentType.BIRTH_CERTIFICATE.value)],
        is_newborn=True,
    )
    assert not any(f.code.startswith("newborn_") for f in findings)


def test_duplicate_members_in_one_case_are_critical():
    first = member(id="a")
    second = member(id="b")
    findings = check_duplicates([first, second])
    assert findings
    assert all(finding.blocks_export for _, finding in findings)
