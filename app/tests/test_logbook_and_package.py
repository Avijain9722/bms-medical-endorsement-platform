"""The operational log workflow and supporting-document packaging."""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db, logbook, pipeline  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.models import (  # noqa: E402
    AuditEvent,
    Base,
    Case,
    CaseStatus,
    DocumentType,
    Export,
    LogEntry,
    Member,
    User,
)
from bms.ocr.text import PlainTextFile, TextPipeline  # noqa: E402
from bms.outputs import package as package_output  # noqa: E402
from bms.storage import ExportStorage  # noqa: E402
from bms.web import app as web_app  # noqa: E402
from bms.web.security import hash_password  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

VISA_TEXT = """RESIDENCE VISA
Staff ID: 90001
U.I.D No: 11223344
Emirates ID 784-1988-1234567-1
"""

INSTRUCTION = "Kindly add employee 90001 Demo Testcase Single CAT B under the policy."


@pytest.fixture()
def env(tmp_path, monkeypatch):
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'log.db'}",
        data_root=tmp_path / "var",
        template_root=REPO_ROOT,
    )
    config.ensure_directories()
    monkeypatch.setattr("bms.config.settings", config)
    for module in (
        "bms.storage",
        "bms.pipeline",
        "bms.intake.archives",
        "bms.ocr.text",
        "bms.web.app",
        "bms.logbook",
    ):
        monkeypatch.setattr(f"{module}.settings", config, raising=False)
    monkeypatch.setenv("BMS_SECRET_KEY", "test-key-not-for-production")
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
        display_name="Tester",
        log_associate="JAHNVI",
        password_hash=hash_password("a-long-test-password"),
        is_admin=True,
    )
    session.add(record)
    session.flush()
    return record


def exported_case(session, user, env) -> Case:
    """A case taken all the way through to export, so log rows exist."""
    case = pipeline.create_case(
        session,
        client_name="ACME FACILITIES DEMO LLC",
        insurer="DEMO INSURANCE (TEST)",
        transaction_type="addition",
        actor=user.username,
        bms_comments=INSTRUCTION,
        sub_group="DEMO-AUH",
        contract_name="ACME ESTATES - L.L.C - AUH",
        category="CAT B",
        owner_id=user.id,
    )
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(
        session, case, actor=user.username, pipeline=TextPipeline([PlainTextFile()])
    )
    member = pipeline.build_members(session, case, actor=user.username)[0]
    member.first_name = "Demo"
    member.last_name = "Testcase"
    member.date_of_birth = "1988-04-12"
    member.gender = "Female"
    member.marital_status = "Single"
    member.nationality = "United Kingdom"
    member.relation = "Principal"
    member.category = "CAT B"
    member.effective_date = date.today().isoformat()
    member.provenance = {}
    pipeline.revalidate_member(session, case, member)
    pipeline.approve_member(session, case, member, actor=user.username)
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )
    return case


# ----------------------------------------------------- post-submission events


def test_log_rows_start_with_every_post_submission_column_blank(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()
    for field_name in (
        "request_ref_no",
        "request_sent_date_to_insurer",
        "card_no",
        "card_receive_and_sent_date",
        "saiba_voucher_no",
        "bbm_invoice_date",
    ):
        assert getattr(entry, field_name) is None


def test_recording_submission_fills_only_its_own_fields_and_audits(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()

    changes = logbook.apply_event(
        session,
        entry,
        "submitted_to_insurer",
        {"request_ref_no": "NAS-REQ-0001", "request_sent_date_to_insurer": "05/08/2026"},
        actor=user.username,
    )
    assert set(changes) == {"request_ref_no", "request_sent_date_to_insurer"}
    assert entry.request_ref_no == "NAS-REQ-0001"
    # Untouched by this event.
    assert entry.card_no is None
    assert entry.saiba_voucher_no is None

    rows = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == entry.id, AuditEvent.field_key == "request_ref_no"
            )
        )
    )
    assert rows and rows[0].old_value is None and rows[0].new_value == "NAS-REQ-0001"
    assert rows[0].actor == user.username


def test_each_event_owns_a_distinct_set_of_fields(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()

    logbook.apply_event(session, entry, "card_received",
                        {"card_no": "CARD-0001", "card_receive_and_sent_date": "12/08/2026"},
                        actor=user.username)
    logbook.apply_event(session, entry, "voucher_recorded",
                        {"saiba_voucher_no": "SV-9001"}, actor=user.username)
    logbook.apply_event(session, entry, "invoice_dated",
                        {"bbm_invoice_date": "31/08/2026"}, actor=user.username)

    assert entry.card_no == "CARD-0001"
    assert entry.saiba_voucher_no == "SV-9001"
    assert entry.bbm_invoice_date == "31/08/2026"
    # Never set, because no event claimed it.
    assert entry.request_ref_no is None


@pytest.mark.parametrize("placeholder", ["N/A", "Pending", "-", "0", "nil"])
def test_placeholders_are_still_refused_through_the_service(session, user, env, placeholder):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()
    from bms.outputs.log import NotRecordable

    with pytest.raises(NotRecordable, match="left blank"):
        logbook.apply_event(
            session, entry, "card_received", {"card_no": placeholder}, actor=user.username
        )
    assert entry.card_no is None


def test_status_is_restricted_to_the_workbook_vocabulary(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()

    logbook.update_fields(session, entry, {"status": "BOOKED"}, actor=user.username)
    assert entry.status == "BOOKED"

    with pytest.raises(logbook.NotPermitted, match="controlled statuses"):
        logbook.update_fields(session, entry, {"status": "INVOICED"}, actor=user.username)


def test_derived_columns_cannot_be_edited_on_a_log_row(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()
    with pytest.raises(logbook.NotPermitted, match="not editable"):
        logbook.update_fields(session, entry, {"beneficiary_name": "Someone Else"}, actor="t")


def test_remarks_are_free_text_and_audited(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()
    logbook.update_fields(
        session, entry, {"remarks": "Awaiting EID copy"}, actor=user.username
    )
    assert entry.remarks == "Awaiting EID copy"
    rows = list(
        session.scalars(select(AuditEvent).where(AuditEvent.field_key == "remarks"))
    )
    assert rows and rows[0].new_value == "Awaiting EID copy"


# --------------------------------------------------------------- reopen


def test_a_closed_case_can_be_reopened_to_record_later_information(session, user, env):
    case = exported_case(session, user, env)
    pipeline.close_case(session, case, actor=user.username)
    session.flush()
    assert case.status == CaseStatus.CLOSED.value

    logbook.reopen_case(session, case, actor=user.username, reason="card number arrived")
    assert case.status == CaseStatus.REVIEW.value
    assert case.closed_at is None

    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "case.reopen"))
    assert event is not None and event.reason == "card number arrived"


def test_reopening_does_not_pretend_purged_documents_returned(session, user, env):
    from datetime import datetime, timezone

    case = exported_case(session, user, env)
    pipeline.close_case(session, case, actor=user.username)
    case.closed_at = datetime.now(timezone.utc) - timedelta(hours=48)
    session.flush()
    pipeline.purge_closed_cases(session, config=env)
    assert case.documents_purged_at is not None

    logbook.reopen_case(session, case, actor=user.username)
    # The purge already happened; reopening restores editing, not the files.
    assert case.documents_purged_at is not None


# ------------------------------------------------------------ date-range log


def test_log_can_be_filtered_and_exported_for_a_date_range(session, user, env):
    exported_case(session, user, env)

    everything = logbook.query(session, logbook.LogFilter())
    assert len(everything) == 1

    today = date.today()
    inside = logbook.query(
        session, logbook.LogFilter(date_from=today, date_to=today)
    )
    assert len(inside) == 1

    outside = logbook.query(
        session,
        logbook.LogFilter(date_from=today + timedelta(days=1), date_to=today + timedelta(days=2)),
    )
    assert outside == []


def test_filters_narrow_by_client_insurer_and_type(session, user, env):
    exported_case(session, user, env)
    assert logbook.query(session, logbook.LogFilter(client_name="ACME FACILITIES DEMO LLC"))
    assert logbook.query(session, logbook.LogFilter(client_name="SOMEONE ELSE")) == []
    assert logbook.query(session, logbook.LogFilter(entry_type="ADDITION"))
    assert logbook.query(session, logbook.LogFilter(entry_type="DELETION")) == []


def test_range_export_writes_the_approved_workbook(session, user, env):
    exported_case(session, user, env)
    download = logbook.export_range(
        session, logbook.LogFilter(), actor=user.username, config=env
    )
    assert download.row_count == 1
    path = ExportStorage(env).absolute(download.relative_path)
    assert path.exists()

    with zipfile.ZipFile(path) as archive:
        # The approved log structure survived the range export.
        assert "xl/worksheets/sheet1.xml" in archive.namelist()

    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "log.export"))
    assert event is not None


def test_range_export_carries_recorded_values_through(session, user, env):
    exported_case(session, user, env)
    entry = session.scalars(select(LogEntry)).one()
    logbook.apply_event(
        session, entry, "submitted_to_insurer",
        {"request_ref_no": "NAS-REQ-0007"}, actor=user.username,
    )
    download = logbook.export_range(session, logbook.LogFilter(), actor=user.username, config=env)

    import xml.etree.ElementTree as ET

    from bms.ooxml.package import M, OoxmlPackage
    from bms.ooxml.sheet import sheet_part_names

    pkg = OoxmlPackage.open(ExportStorage(env).absolute(download.relative_path))
    parts = sheet_part_names(pkg.read("xl/workbook.xml"), pkg.read("xl/_rels/workbook.xml.rels"))
    shared = [
        "".join(t.text or "" for t in si.iter(M + "t"))
        for si in ET.fromstring(pkg.read("xl/sharedStrings.xml"))
    ]
    root = ET.fromstring(pkg.read(parts["MAIN DATA"]))
    values = {}
    for row in root.findall(f"{M}sheetData/{M}row"):
        for cell in row:
            node = cell.find(M + "v")
            if node is None:
                continue
            values[cell.get("r")] = shared[int(node.text)] if cell.get("t") == "s" else node.text

    assert values.get("G2") == "NAS-REQ-0007"   # Request Ref.No. now populated
    assert "R2" not in values                    # CARD # still genuinely blank


# ------------------------------------------------------------ packaging


def _member(**overrides):
    base = dict(
        id="m1", staff_id="90001", first_name="Demo", middle_name=None, last_name="Testcase"
    )
    base.update(overrides)
    namespace = SimpleNamespace(**base)
    namespace.full_name = " ".join(
        p for p in (namespace.first_name, namespace.middle_name, namespace.last_name) if p
    )
    return namespace


def _file(name, doc_type, sha, size=10):
    return SimpleNamespace(
        original_name=name, archive_path=None, document_type=doc_type, sha256=sha, byte_size=size
    )


def test_package_names_files_by_staff_id_and_document_type():
    member = _member()
    documents = {
        "m1": [
            _file("Passport Front.jpg", DocumentType.PASSPORT.value, "aaa"),
            _file("PIC.jpg", DocumentType.PHOTOGRAPH.value, "bbb"),
        ]
    }
    result = package_output.build(
        [member], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    names = {f.output_name for f in result.files}
    assert names == {"90001 - Passport.jpg", "90001 - Photo.jpg"}


def test_package_reports_the_photo_name_for_the_workbook():
    """NAS matches on the filename, so the workbook must carry exactly this."""
    member = _member()
    documents = {"m1": [_file("PIC.jpg", DocumentType.PHOTOGRAPH.value, "bbb")]}
    result = package_output.build(
        [member], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    assert result.photo_names["m1"] == "90001 - Photo.jpg"


def test_package_excludes_documents_not_sent_to_the_insurer():
    member = _member()
    documents = {
        "m1": [
            _file("client list.xlsx", DocumentType.CLIENT_SHEET.value, "ccc"),
            _file("notes.txt", DocumentType.UNKNOWN.value, "ddd"),
            _file("Passport.jpg", DocumentType.PASSPORT.value, "aaa"),
        ]
    }
    result = package_output.build(
        [member], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    assert [f.output_name for f in result.files] == ["90001 - Passport.jpg"]
    assert len(result.skipped) == 2


def test_package_deduplicates_identical_content():
    """One discovery email carried the same attachment twice, byte-identical."""
    member = _member()
    documents = {
        "m1": [
            _file("Passport.jpg", DocumentType.PASSPORT.value, "same"),
            _file("Passport copy.jpg", DocumentType.PASSPORT.value, "same"),
        ]
    }
    result = package_output.build(
        [member], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    assert len(result.files) == 1
    assert any("duplicate content" in reason for _, reason in result.skipped)


def test_package_filenames_are_unique_across_members():
    first = _member(id="m1", staff_id=None, first_name="Demo", last_name="Testcase")
    second = _member(id="m2", staff_id=None, first_name="Demo", last_name="Testcase")
    documents = {
        "m1": [_file("a.jpg", DocumentType.PASSPORT.value, "aaa")],
        "m2": [_file("b.jpg", DocumentType.PASSPORT.value, "bbb")],
    }
    result = package_output.build(
        [first, second], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    names = [f.output_name for f in result.files]
    assert len(set(names)) == 2


def test_package_manifest_links_each_file_to_its_member_and_portal_row():
    first = _member(id="m1", staff_id="90001")
    second = _member(id="m2", staff_id="90002", first_name="Second", last_name="Person")
    documents = {
        "m1": [_file("p1.jpg", DocumentType.PASSPORT.value, "aaa")],
        "m2": [_file("p2.jpg", DocumentType.PASSPORT.value, "bbb")],
    }
    result = package_output.build(
        [first, second],
        documents,
        read_bytes=lambda sha: b"x",
        case_reference="BMS-2026-00001",
        first_data_row=2,
    )
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert package_output.MANIFEST_NAME in archive.namelist()
        rows = list(csv.DictReader(io.StringIO(archive.read("manifest.csv").decode())))

    assert [r["Portal row"] for r in rows] == ["2", "3"]
    assert [r["Staff ID"] for r in rows] == ["90001", "90002"]
    assert rows[0]["Packaged filename"] == "90001 - Passport.jpg"
    assert rows[0]["Case"] == "BMS-2026-00001"


def test_unsafe_characters_are_stripped_from_generated_names():
    member = _member(staff_id="90001/../etc")
    documents = {"m1": [_file("p.jpg", DocumentType.PASSPORT.value, "aaa")]}
    result = package_output.build(
        [member], documents, read_bytes=lambda sha: b"x", case_reference="BMS-2026-00001"
    )
    name = result.files[0].output_name
    assert "/" not in name and ".." not in name


def test_export_produces_a_supporting_zip_alongside_the_workbook(session, user, env):
    case = pipeline.create_case(
        session,
        client_name="ACME FACILITIES DEMO LLC",
        insurer="DEMO INSURANCE (TEST)",
        transaction_type="addition",
        actor=user.username,
        bms_comments=INSTRUCTION,
        owner_id=user.id,
    )
    pipeline.add_upload(session, case, "Visa.txt", VISA_TEXT.encode(), actor=user.username)
    pipeline.analyse_files(
        session, case, actor=user.username, pipeline=TextPipeline([PlainTextFile()])
    )
    member = pipeline.build_members(session, case, actor=user.username)[0]
    member.first_name, member.last_name = "Demo", "Testcase"
    member.date_of_birth, member.gender = "1988-04-12", "Female"
    member.marital_status, member.nationality = "Single", "United Kingdom"
    member.relation, member.category = "Principal", "CAT B"
    member.effective_date = date.today().isoformat()
    member.provenance = {}
    pipeline.revalidate_member(session, case, member)
    pipeline.approve_member(session, case, member, actor=user.username)
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )

    exports = list(session.scalars(select(Export).where(Export.case_id == case.id)))
    kinds = {e.kind for e in exports}
    # The visa is a packaged document type, so a ZIP is produced.
    assert "portal" in kinds and "log" in kinds and "zip" in kinds
    zip_export = next(e for e in exports if e.kind == "zip")
    with zipfile.ZipFile(ExportStorage(env).absolute(zip_export.relative_path)) as archive:
        names = archive.namelist()
    assert package_output.MANIFEST_NAME in names
    assert any(name.startswith("90001 - Visa") for name in names)


# -------------------------------------------------------------------- web


def test_log_screen_and_event_recording_through_the_ui(session, user, env):
    exported_case(session, user, env)
    session.commit()

    http = TestClient(web_app.app, follow_redirects=False)
    assert http.post(
        "/login", data={"username": "tester", "password": "a-long-test-password"}
    ).status_code == 303

    page = http.get("/log")
    assert page.status_code == 200
    assert "Operational log" in page.text
    assert "ACME FACILITIES DEMO LLC" in page.text

    with db.session_scope() as fresh:
        entry_id = fresh.scalars(select(LogEntry)).one().id

    recorded = http.post(
        f"/log/{entry_id}/event",
        data={"event": "submitted_to_insurer", "request_ref_no": "NAS-REQ-0002"},
    )
    assert recorded.status_code == 303

    with db.session_scope() as fresh:
        assert fresh.get(LogEntry, entry_id).request_ref_no == "NAS-REQ-0002"

    rejected = http.post(
        f"/log/{entry_id}/event", data={"event": "card_received", "card_no": "N/A"}
    )
    assert rejected.status_code == 400

    download = http.post("/log/export", data={"date_from": "", "date_to": ""})
    assert download.status_code == 200
    assert download.content[:2] == b"PK"
