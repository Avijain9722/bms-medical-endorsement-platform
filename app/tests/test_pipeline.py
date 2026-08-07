"""End-to-end case processing against a real database and real templates."""

from __future__ import annotations

import io
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db, pipeline  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.models import (  # noqa: E402
    Base,
    Case,
    CaseFile,
    Export,
    FileStatus,
    LogEntry,
    Member,
    ReviewFlag,
    User,
)
from bms.ocr.text import PlainTextFile, TextPipeline  # noqa: E402
from bms.outputs import log as log_output  # noqa: E402
from bms.ooxml.package import file_sha256  # noqa: E402
from bms.storage import ExportStorage  # noqa: E402
from bms.web.security import hash_password  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

PASSPORT_TEXT = """PASSPORT
P<INDKANNADHASAN<<RITHICK<<<<<<<<<<<<<<<<<<<
P1234567<1IND9003071M3001019<<<<<<<<<<<<<<02
"""

VISA_TEXT = """RESIDENCE VISA
Staff ID: 82270
U.I.D No: 58620894
Emirates ID 784-1998-0432182-8
"""

INSTRUCTION = """
Dear Team,
Kindly add the below employee to the policy.
Basatin ID Name Marital Status CAT
82270 Rithick Kannadhasan Single CAT C
Regards,
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """An isolated database and data root per test."""
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_root=tmp_path / "var",
        template_root=REPO_ROOT,
    )
    config.ensure_directories()
    monkeypatch.setattr("bms.config.settings", config)
    for module in ("bms.storage", "bms.pipeline", "bms.intake.archives", "bms.ocr.text"):
        monkeypatch.setattr(f"{module}.settings", config, raising=False)

    engine = db.init_engine(config)
    Base.metadata.create_all(engine)
    return config


@pytest.fixture()
def session(env):
    with db.session_scope() as active:
        yield active


@pytest.fixture()
def user(session):
    record = User(
        username="tester",
        display_name="Test User",
        log_associate="JAHNVI",
        password_hash=hash_password("secret"),
    )
    session.add(record)
    session.flush()
    return record


def text_only_pipeline() -> TextPipeline:
    """No OCR binaries in CI, so drive the pipeline with the plain-text reader."""
    return TextPipeline([PlainTextFile()])


def make_case(session, user, config, *, transaction="addition", instruction=INSTRUCTION) -> Case:
    return pipeline.create_case(
        session,
        client_name="BASATIN",
        insurer="QATAR INSURANCE",
        transaction_type=transaction,
        actor=user.username,
        bms_comments=instruction,
        contract_name="BASATIN LANDSCAPING - SOLE PROPRIETOSHIP LLC_AUH",
        category="CAT C",
        sub_group="AUH",
        owner_id=user.id,
    )


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------- persistence


def test_case_reference_is_sequential(session, user, env):
    first = make_case(session, user, env)
    second = make_case(session, user, env)
    assert first.reference.endswith("00001")
    assert second.reference.endswith("00002")


def test_uploads_are_written_to_disk_and_survive_a_new_session(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "visa.txt", VISA_TEXT.encode(), actor=user.username)
    session.commit()

    # A fresh session, as if the user came back tomorrow.
    with db.session_scope() as later:
        reloaded = later.get(Case, case.id)
        files = list(later.scalars(select(CaseFile).where(CaseFile.case_id == reloaded.id)))
        assert len(files) == 1
        from bms.storage import Storage

        assert Storage(env).get_bytes(files[0].sha256).decode() == VISA_TEXT


def test_identical_uploads_are_deduplicated(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "a.txt", b"same bytes", actor=user.username)
    outcome = pipeline.add_upload(session, case, "b.txt", b"same bytes", actor=user.username)
    assert outcome.stored == []
    assert outcome.duplicates == ["b.txt"]


def test_zip_uploads_are_expanded(session, user, env):
    case = make_case(session, user, env)
    payload = zip_of({"Passport.txt": PASSPORT_TEXT.encode(), "Visa.txt": VISA_TEXT.encode()})
    outcome = pipeline.add_upload(session, case, "82270.zip", payload, actor=user.username)
    assert len(outcome.stored) == 2
    assert all(record.archive_path.startswith("82270.zip/") for record in outcome.stored)


# ---------------------------------------------------------------- processing


def test_processing_reads_documents_and_builds_a_member(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Passport.txt", PASSPORT_TEXT.encode(), actor=user.username)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)

    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    members = pipeline.build_members(session, case, actor=user.username)

    assert len(members) == 1
    member = members[0]
    assert member.passport_no == "P1234567"
    assert member.emirates_id == "784-1998-0432182-8"
    assert member.unified_no == "58620894"
    assert member.staff_id == "82270"
    # Every proposed value knows where it came from.
    assert member.provenance["passport_no"]["source_name"] == "Passport.txt"
    assert 0 < member.provenance["passport_no"]["confidence"] <= 1


def test_effective_date_defaults_to_the_processing_date(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    members = pipeline.build_members(session, case, actor=user.username)
    assert members[0].effective_date == date.today().isoformat()


def test_unreadable_documents_are_flagged_not_silently_skipped(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "scan.jpg", b"\xff\xd8\xff\xe0not-readable", actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    pipeline.build_members(session, case, actor=user.username)

    record = session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)).one()
    assert record.status == FileStatus.OCR_UNAVAILABLE.value
    flags = list(session.scalars(select(ReviewFlag).where(ReviewFlag.case_id == case.id)))
    assert any(flag.code == "ocr_unavailable" for flag in flags)


def test_reprocessing_does_not_duplicate_members(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    pipeline.build_members(session, case, actor=user.username)
    pipeline.build_members(session, case, actor=user.username)
    assert session.scalars(select(Member).where(Member.case_id == case.id)).all().__len__() == 1


# ------------------------------------------------------------------ review


def test_corrections_are_audited_and_marked_confirmed(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    member = pipeline.build_members(session, case, actor=user.username)[0]

    from bms.models import AuditEvent

    def audit_rows(field_key: str) -> list[AuditEvent]:
        return list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == member.id, AuditEvent.field_key == field_key
                )
            )
        )

    before = member.first_name
    pipeline.update_member_field(session, member, "first_name", "Rithik", actor=user.username)
    session.flush()

    assert member.first_name == "Rithik"
    assert member.provenance["first_name"]["reviewed"] is True
    rows = audit_rows("first_name")
    assert len(rows) == 1
    assert rows[0].old_value == before
    assert rows[0].new_value == "Rithik"
    assert rows[0].actor == user.username


def test_a_no_op_edit_writes_no_audit_row(session, user, env):
    """Re-saving an unchanged field must not pad the trail with noise."""
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    member = pipeline.build_members(session, case, actor=user.username)[0]

    from bms.models import AuditEvent

    pipeline.update_member_field(
        session, member, "staff_id", member.staff_id, actor=user.username
    )
    session.flush()
    rows = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == member.id, AuditEvent.field_key == "staff_id"
            )
        )
    )
    assert rows == []
    assert member.provenance["staff_id"]["reviewed"] is True


def test_editing_an_approved_member_requires_approval_again(session, user, env):
    case = make_case(session, user, env)
    member = Member(
        case_id=case.id,
        row_index=0,
        transaction_type="addition",
        first_name="Before",
        approved=True,
        approved_by_id=user.id,
        approved_at=datetime.now(timezone.utc),
    )
    session.add(member)
    session.flush()

    pipeline.update_member_field(
        session, member, "first_name", "After", actor=user.username
    )

    assert member.approved is False
    assert member.approved_by_id is None
    assert member.approved_at is None


def test_approval_records_the_approver(session, user, env):
    case = make_case(session, user, env)
    member = Member(case_id=case.id, row_index=0, transaction_type="addition")
    session.add(member)
    session.flush()

    pipeline.approve_member(
        session, case, member, actor=user.username, approver_id=user.id
    )

    assert member.approved_by_id == user.id


def test_a_member_with_a_critical_flag_cannot_be_approved(session, user, env):
    case = make_case(session, user, env)
    # No documents and no identity: mandatory fields will be missing.
    pipeline.build_members(session, case, actor=user.username)
    member = session.scalars(select(Member).where(Member.case_id == case.id)).first()
    assert member is not None
    with pytest.raises(PermissionError, match="cannot be overridden"):
        pipeline.approve_member(session, case, member, actor=user.username)


# ------------------------------------------------------------------ export


def _complete(member) -> None:
    member.first_name = "Rithick"
    member.last_name = "Kannadhasan"
    member.date_of_birth = "1990-03-07"
    member.gender = "Male"
    member.marital_status = "Single"
    member.nationality = "India"
    member.relation = "Principal"
    member.category = "CAT C"
    member.effective_date = date.today().isoformat()
    member.emirates_id = "784-1998-0432182-8"
    member.provenance = {}


def test_full_addition_export_produces_both_workbooks(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Passport.txt", PASSPORT_TEXT.encode(), actor=user.username)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    member = pipeline.build_members(session, case, actor=user.username)[0]

    _complete(member)
    pipeline.revalidate_member(session, case, member)
    pipeline.approve_member(session, case, member, actor=user.username)

    portal, log = pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )
    assert portal.row_count == 1 and portal.fingerprint_ok
    assert log.row_count == 1

    storage = ExportStorage(env)
    assert storage.absolute(portal.relative_path).exists()
    assert storage.absolute(log.relative_path).exists()

    # Both workbooks are staged in var/tmp on the way to export storage. They
    # used to be left there, so every case ever exported kept a scratch copy for
    # the life of the installation -- and the retention purge does not look in
    # that directory.
    leftovers = sorted(p.name for p in (env.data_root / "tmp").glob("*"))
    assert leftovers == [], f"scratch files left behind: {leftovers}"


def test_reexport_keeps_prior_files_and_records_current_hashes(session, user, env):
    case = make_case(session, user, env)
    member = Member(case_id=case.id, row_index=0, transaction_type="addition")
    _complete(member)
    session.add(member)
    session.flush()
    pipeline.approve_member(session, case, member, actor=user.username)

    first_portal, first_log = pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )
    storage = ExportStorage(env)
    first_path = storage.absolute(first_portal.relative_path)
    first_bytes = first_path.read_bytes()

    second_portal, second_log = pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )

    assert first_portal.relative_path != second_portal.relative_path
    assert first_log.relative_path != second_log.relative_path
    assert first_path.read_bytes() == first_bytes
    for record in (first_portal, first_log, second_portal, second_log):
        assert file_sha256(storage.absolute(record.relative_path)) == record.sha256


def test_later_export_adds_log_entries_for_newly_approved_members(session, user, env):
    case = make_case(session, user, env)
    first = Member(case_id=case.id, row_index=0, transaction_type="addition", staff_id="90001")
    second = Member(case_id=case.id, row_index=1, transaction_type="addition", staff_id="90002")
    for member in (first, second):
        _complete(member)
        session.add(member)
    session.flush()

    pipeline.approve_member(session, case, first, actor=user.username)
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )
    pipeline.approve_member(session, case, second, actor=user.username)
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )

    entries = session.scalars(select(LogEntry).where(LogEntry.case_id == case.id)).all()
    assert {entry.member_id for entry in entries} == {first.id, second.id}


def test_export_rejects_unknown_and_wrong_transaction_templates(session, user, env):
    case = make_case(session, user, env)
    member = Member(case_id=case.id, row_index=0, transaction_type="addition")
    _complete(member)
    member.approved = True
    session.add(member)
    session.flush()

    with pytest.raises(pipeline.ExportBlocked, match="not a supported"):
        pipeline.export_case(
            session, case, template_key="forged.template", actor=user.username, config=env
        )
    with pytest.raises(pipeline.ExportBlocked, match="does not match"):
        pipeline.export_case(
            session, case, template_key="nas.deletion.v1", actor=user.username, config=env
        )

    assert session.scalars(select(Export).where(Export.case_id == case.id)).all() == []
    assert not (env.export_root / case.reference).exists()


def test_reprocessing_is_refused_after_log_rows_exist(session, user, env):
    case = make_case(session, user, env)
    member = Member(case_id=case.id, row_index=0, transaction_type="addition")
    _complete(member)
    member.approved = True
    session.add(member)
    session.flush()
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )

    with pytest.raises(pipeline.ProcessingBlocked, match="operational log"):
        pipeline.build_members(session, case, actor=user.username)

    assert session.get(Member, member.id) is member


def test_reprocessing_does_not_query_once_per_document(session, user, env):
    """It used to issue four round trips per file, and nothing capped the count.

    The files were selected twice and their fields once per file, twice over,
    because identity building and member building each loaded their own copy. A
    120-document batch -- one large client email -- took 267 queries to assemble
    what two statements return. The count must not scale with the batch.
    """
    from sqlalchemy import event

    visa = "RESIDENCE VISA\nStaff ID: {sid}\nU.I.D No: 5862089{i}\n"

    def queries_for(document_count: int) -> int:
        case = make_case(session, user, env, instruction="Kindly add the below.")
        for index in range(document_count):
            pipeline.add_upload(
                session, case, f"Visa{index}.txt",
                visa.format(sid=80000 + index, i=index % 10).encode(), actor=user.username,
            )
        pipeline.analyse_files(
            session, case, actor=user.username, pipeline=text_only_pipeline()
        )
        session.flush()

        counted = 0

        def count(*args, **kwargs):
            nonlocal counted
            counted += 1

        engine = db.get_engine()
        event.listen(engine, "before_cursor_execute", count)
        try:
            pipeline.build_members(session, case, actor=user.username)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return counted

    few = queries_for(4)
    many = queries_for(40)
    assert few == many, f"{few} queries for 4 documents, {many} for 40 -- it scales with the batch"


def test_a_reference_is_not_reissued_after_a_case_is_deleted(session, user, env):
    """`Case.reference` is unique, so a repeated one is a 500 in front of a user.

    Deriving the next number from a row count -- rather than from the highest
    reference already issued -- repeats the last one as soon as any case is
    removed, and the very next case created then fails on the constraint.
    """
    prefix = f"BMS-{date.today().year}-"
    first = make_case(session, user, env)
    second = make_case(session, user, env)
    assert [first.reference, second.reference] == [f"{prefix}00001", f"{prefix}00002"]

    session.delete(first)
    session.flush()

    third = make_case(session, user, env)
    assert third.reference == f"{prefix}00003"


def test_a_reference_collision_is_retried_rather_than_raised(session, user, env, monkeypatch):
    """Two operators creating a case in the same instant read the same number."""
    prefix = f"BMS-{date.today().year}-"
    taken = make_case(session, user, env).reference

    # Hand back the number already used, as a racing second process would.
    stale = iter([taken, taken])
    real = pipeline.next_reference
    monkeypatch.setattr(
        pipeline, "next_reference", lambda active: next(stale, None) or real(active)
    )

    recovered = make_case(session, user, env)
    assert recovered.reference == f"{prefix}00002"


def test_export_is_blocked_while_a_critical_flag_is_open(session, user, env):
    case = make_case(session, user, env)
    pipeline.build_members(session, case, actor=user.username)
    with pytest.raises(pipeline.ExportBlocked):
        pipeline.export_case(
            session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
        )


def test_export_requires_at_least_one_approved_member(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(session, case, actor=user.username, pipeline=text_only_pipeline())
    member = pipeline.build_members(session, case, actor=user.username)[0]
    _complete(member)
    pipeline.revalidate_member(session, case, member)
    # Deliberately not approved.
    with pytest.raises(pipeline.ExportBlocked, match="approved"):
        pipeline.export_case(
            session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
        )


def test_deletion_workflow_exports_the_nas_deletion_template(session, user, env):
    case = make_case(
        session,
        user,
        env,
        transaction="deletion",
        instruction=(
            "Please cancel the below staff and share the COC.\n"
            "BASATIN ID Card Number Name\n"
            "12373 EH2F-6FJF-LFL2-FLED Pashupati Mandal\n"
        ),
    )
    pipeline.build_members(session, case, actor=user.username)
    member = session.scalars(select(Member).where(Member.case_id == case.id)).first()
    member.member_card_no = "EH2F-6FJF-LFL2-FLED"
    member.deletion_reason = "Resignation"
    member.effective_date = date.today().isoformat()
    pipeline.revalidate_member(session, case, member)
    pipeline.approve_member(session, case, member, actor=user.username)

    portal, _ = pipeline.export_case(
        session, case, template_key="nas.deletion.v1", actor=user.username, config=env
    )
    assert portal.row_count == 1
    assert portal.template_key == "nas.deletion.v1"


# --------------------------------------------------------------------- log


def test_log_entry_leaves_post_submission_fields_null(session, user, env):
    case = make_case(session, user, env)
    pipeline.build_members(session, case, actor=user.username)
    member = session.scalars(select(Member).where(Member.case_id == case.id)).first()
    entry = log_output.build_entry(case, member, shared_by="JAHNVI")

    assert entry.request_ref_no is None
    assert entry.request_sent_date_to_insurer is None
    assert entry.card_no is None
    assert entry.card_receive_and_sent_date is None
    assert entry.saiba_voucher_no is None
    assert entry.bbm_invoice_date is None


def test_recording_submission_fills_only_its_own_fields():
    entry = LogEntry(case_id="c", member_id="m")
    changes = log_output.record_event(
        entry, "submitted_to_insurer", {"request_sent_date_to_insurer": "05/08/2026"}
    )
    assert entry.request_sent_date_to_insurer == "05/08/2026"
    assert entry.card_no is None
    assert changes["request_sent_date_to_insurer"] == (None, "05/08/2026")


def test_recording_refuses_a_field_outside_the_event():
    entry = LogEntry(case_id="c", member_id="m")
    with pytest.raises(log_output.NotRecordable):
        log_output.record_event(entry, "submitted_to_insurer", {"card_no": "12345"})


@pytest.mark.parametrize("placeholder", ["N/A", "Pending", "-", "0", "TBC"])
def test_recording_refuses_placeholder_values(placeholder):
    entry = LogEntry(case_id="c", member_id="m")
    with pytest.raises(log_output.NotRecordable, match="left blank"):
        log_output.record_event(entry, "card_received", {"card_no": placeholder})


# ------------------------------------------------------------------- purge


def test_purge_removes_documents_after_closure_but_keeps_the_record(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    record = session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)).one()
    digest = record.sha256

    pipeline.close_case(session, case, actor=user.username)
    case.closed_at = datetime.now(timezone.utc) - timedelta(hours=48)
    session.flush()

    result = pipeline.purge_closed_cases(session, config=env)
    assert result.cases == 1 and result.files == 1

    from bms.storage import Storage

    assert not Storage(env).exists(digest)
    assert session.get(Case, case.id) is not None
    assert session.get(CaseFile, record.id).status == FileStatus.PURGED.value


def test_purge_leaves_recently_closed_cases_alone(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.close_case(session, case, actor=user.username)
    session.flush()
    assert pipeline.purge_closed_cases(session, config=env).cases == 0


def test_purge_never_touches_an_open_case(session, user, env):
    case = make_case(session, user, env)
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    session.flush()
    assert pipeline.purge_closed_cases(session, config=env).cases == 0


def test_purge_can_be_disabled(session, user, env):
    case = make_case(session, user, env)
    pipeline.close_case(session, case, actor=user.username)
    case.closed_at = datetime.now(timezone.utc) - timedelta(days=7)
    session.flush()
    disabled = Settings(
        database_url=env.database_url, data_root=env.data_root, purge_enabled=False
    )
    assert pipeline.purge_closed_cases(session, config=disabled).cases == 0
