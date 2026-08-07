"""Client master, administration and the login mechanism."""

from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from conftest import BrowserClient  # noqa: E402
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db, master  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.models import AuditEvent, Base, Case, Client, User  # noqa: E402
from bms.web import app as web_app  # noqa: E402
from bms.web.security import hash_password  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_master_workbook(rows: list[dict]) -> bytes:
    """A minimal .xlsx with inline strings, written without a spreadsheet library."""
    headers = list(rows[0].keys())

    def col(index: int) -> str:
        name = ""
        while index > 0:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    def row_xml(values, row_number):
        cells = "".join(
            f'<c r="{col(i + 1)}{row_number}" t="inlineStr"><is><t>{v}</t></is></c>'
            for i, v in enumerate(values)
            if v
        )
        return f'<row r="{row_number}">{cells}</row>'

    body = row_xml(headers, 1) + "".join(
        row_xml([r.get(h, "") for h in headers], i) for i, r in enumerate(rows, start=2)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Clients" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        "</Types>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'master.db'}",
        data_root=tmp_path / "var",
        template_root=REPO_ROOT,
    )
    config.ensure_directories()
    monkeypatch.setattr("bms.config.settings", config)
    for module in ("bms.storage", "bms.pipeline", "bms.intake.archives", "bms.ocr.text", "bms.web.app"):
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
def client(env):
    with db.session_scope() as session:
        session.add(
            User(
                username="admin",
                display_name="Administrator",
                log_associate="JAHNVI",
                password_hash=hash_password("admin-password"),
                is_admin=True,
            )
        )
        session.add(
            User(
                username="processor",
                display_name="Processor",
                password_hash=hash_password("processor-password"),
                is_admin=False,
            )
        )
    return BrowserClient(web_app.app, follow_redirects=False)


def sign_in(http: TestClient, username: str, password: str) -> None:
    assert http.post("/login", data={"username": username, "password": password}).status_code == 303


# ------------------------------------------------------------- client master


def test_company_and_sub_groups_are_created(session):
    company, created = master.get_or_create_client(session, "ACME FACILITIES DEMO LLC", code="ACME")
    assert created is True

    first, made = master.get_or_create_sub_group(session, company, "DEMO-AUH", emirate="Abu Dhabi")
    assert made is True and first.emirate == "Abu Dhabi"

    second, made = master.get_or_create_sub_group(session, company, "DEMO-DXB", emirate="dxb")
    # Abbreviations are normalised so the deletion rule can rely on the value.
    assert second.emirate == "Dubai"

    again, made = master.get_or_create_sub_group(session, company, "DEMO-AUH")
    assert made is False and again.id == first.id


def test_company_names_are_unique(session):
    master.get_or_create_client(session, "ACME FACILITIES DEMO LLC")
    _, created = master.get_or_create_client(session, "ACME FACILITIES DEMO LLC")
    assert created is False
    assert len(list(session.scalars(select(Client)))) == 1


def test_legal_entity_keeps_the_exact_contract_literal(session):
    company, _ = master.get_or_create_client(session, "ACME FACILITIES DEMO LLC")
    entity, _ = master.get_or_create_entity(
        session,
        company,
        "ACME Estates",
        contract_name="ALDAR ESTATES INVESTMENT - SOLE PROPRIETORSHIP L.L.C (FM) - AUH",
    )
    assert entity.contract_name.endswith("(FM) - AUH")


def test_excel_import_creates_the_whole_hierarchy(session):
    data = build_master_workbook(
        [
            {
                "Client": "ACME FACILITIES DEMO LLC",
                "Code": "ACME",
                "Sub-Group": "DEMO-AUH",
                "Legal Entity": "ACME Estates",
                "Contract Name": "ACME ESTATES - SOLE PROPRIETORSHIP L.L.C - AUH",
                "Insurer": "DEMO INSURANCE (TEST)",
                "Policy No": "TEST-POL-0001",
                "Category": "CAT B",
                "Emirate": "Abu Dhabi",
                "Addition Template": "nas.addition.aldar.v1",
                "Deletion Template": "nas.deletion.v1",
            },
            {
                "Client": "ACME FACILITIES DEMO LLC",
                "Sub-Group": "DEMO-DXB",
                "Insurer": "DEMO INSURANCE (TEST)",
                "Policy No": "TEST-POL-0002",
                "Category": "CAT A",
                "Emirate": "Dubai",
            },
            {
                "Client": "SECOND DEMO COMPANY LLC",
                "Sub-Group": "HQ",
                "Insurer": "OTHER DEMO INSURER",
                "Emirate": "Northern Emirates",
            },
        ]
    )
    summary = master.import_workbook(session, data, actor="tester")

    assert summary.rows_read == 3
    assert summary.clients_created == 2
    assert summary.sub_groups_created == 3
    assert summary.legal_entities_created == 1
    assert summary.policies_created == 3

    company = session.scalar(select(Client).where(Client.name == "ACME FACILITIES DEMO LLC"))
    assert {s.name for s in company.sub_groups} == {"DEMO-AUH", "DEMO-DXB"}
    assert {s.emirate for s in company.sub_groups} == {"Abu Dhabi", "Dubai"}
    policy = next(p for p in company.policies if p.policy_no == "TEST-POL-0001")
    assert policy.addition_template_key == "nas.addition.aldar.v1"


def test_import_accepts_alternative_column_headings(session):
    data = build_master_workbook(
        [{"Company Name": "HEADER ALIAS DEMO LLC", "Sub Group": "HQ", "Insurer/TPA": "DEMO TPA"}]
    )
    summary = master.import_workbook(session, data, actor="tester")
    assert summary.clients_created == 1
    assert summary.sub_groups_created == 1


def test_import_skips_rows_without_a_company(session):
    data = build_master_workbook(
        [{"Client": "", "Sub-Group": "orphan"}, {"Client": "REAL DEMO LLC", "Sub-Group": "HQ"}]
    )
    summary = master.import_workbook(session, data, actor="tester")
    assert summary.clients_created == 1
    assert len(summary.skipped) == 1


def test_reimport_is_idempotent(session):
    data = build_master_workbook(
        [{"Client": "ACME FACILITIES DEMO LLC", "Sub-Group": "DEMO-AUH", "Insurer": "DEMO INSURANCE"}]
    )
    master.import_workbook(session, data, actor="tester")
    second = master.import_workbook(session, data, actor="tester")
    assert second.total_created == 0


def test_import_is_audited(session):
    data = build_master_workbook([{"Client": "AUDITED DEMO LLC", "Sub-Group": "HQ"}])
    master.import_workbook(session, data, actor="tester")
    session.flush()
    event = session.scalar(select(AuditEvent).where(AuditEvent.action == "master.import"))
    assert event is not None and event.actor == "tester"


def test_import_caps_the_uncompressed_workbook_size(monkeypatch):
    data = build_master_workbook([{"Client": "BOUNDED DEMO LLC"}])
    monkeypatch.setattr(master, "MAX_IMPORT_UNCOMPRESSED_BYTES", 100)

    with pytest.raises(ValueError, match="expands beyond"):
        master.read_rows(data)


def test_import_rejects_xml_document_types():
    original = build_master_workbook([{"Client": "UNTRUSTED DEMO LLC"}])
    source = BytesIO(original)
    rewritten = BytesIO()
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(rewritten, "w") as outgoing:
        for info in incoming.infolist():
            payload = incoming.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                marker = b"<worksheet "
                payload = payload.replace(
                    marker,
                    b'<!DOCTYPE worksheet [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                    + marker,
                    1,
                )
            outgoing.writestr(info, payload)

    with pytest.raises(ValueError, match="entities are not supported"):
        master.read_rows(rewritten.getvalue())


# ------------------------------------------------------------ administration


def test_administration_requires_an_administrator(client):
    sign_in(client, "processor", "processor-password")
    assert client.get("/admin").status_code == 403


def test_administrator_can_reach_administration(client):
    sign_in(client, "admin", "admin-password")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Client master" in page.text


def test_administrator_creates_a_user_without_storing_the_password(client):
    sign_in(client, "admin", "admin-password")
    response = client.post(
        "/admin/users",
        data={
            "username": "jahnvi",
            "display_name": "Jahnvi",
            "log_associate": "JAHNVI",
            "password": "a-long-enough-password",
            "is_admin": "",
        },
    )
    assert response.status_code == 303
    with db.session_scope() as session:
        user = session.scalar(select(User).where(User.username == "jahnvi"))
        assert user is not None and user.is_admin is False
        assert "a-long-enough-password" not in user.password_hash
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "user.create")))
        assert events
        assert "a-long-enough-password" not in str(events[0].detail)


def test_short_passwords_are_refused(client):
    sign_in(client, "admin", "admin-password")
    response = client.post(
        "/admin/users", data={"username": "weak", "password": "short", "is_admin": ""}
    )
    assert response.status_code == 400


def test_disabling_an_account_revokes_its_existing_session(client):
    processor = BrowserClient(web_app.app, follow_redirects=False)
    sign_in(processor, "processor", "processor-password")
    assert processor.get("/").status_code == 200

    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        processor_id = session.scalar(
            select(User.id).where(User.username == "processor")
        )
    response = client.post(
        f"/admin/users/{processor_id}/active", data={"active": "0"}
    )

    assert response.status_code == 303
    assert processor.get("/").status_code == 303
    with db.session_scope() as session:
        disabled = session.get(User, processor_id)
        assert disabled is not None and disabled.active is False
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.set_active")
        )
        assert event is not None


def test_administrator_cannot_disable_their_own_account(client):
    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        admin_id = session.scalar(select(User.id).where(User.username == "admin"))

    response = client.post(f"/admin/users/{admin_id}/active", data={"active": "0"})

    assert response.status_code == 400
    assert client.get("/admin").status_code == 200


def test_company_and_sub_group_can_be_entered_through_the_ui(client):
    sign_in(client, "admin", "admin-password")
    created = client.post(
        "/admin/clients", data={"name": "ACME FACILITIES DEMO LLC", "code": "ACME"}
    )
    assert created.status_code == 303
    client_url = created.headers["location"]
    client_id = client_url.rsplit("/", 1)[1]

    assert client.post(
        f"/admin/clients/{client_id}/sub-groups",
        data={"name": "DEMO-AUH", "emirate": "Abu Dhabi"},
    ).status_code == 303
    assert client.post(
        f"/admin/clients/{client_id}/entities",
        data={"name": "ACME Estates", "contract_name": "ACME ESTATES - L.L.C - AUH"},
    ).status_code == 303
    assert client.post(
        f"/admin/clients/{client_id}/policies",
        data={
            "insurer": "DEMO INSURANCE (TEST)",
            "policy_no": "TEST-POL-0001",
            "category": "CAT B",
            "emirate": "Abu Dhabi",
            "addition_template_key": "nas.addition.aldar.v1",
            "deletion_template_key": "nas.deletion.v1",
            "network": "",
            "legal_entity_id": "",
        },
    ).status_code == 303

    page = client.get(client_url)
    assert "DEMO-AUH" in page.text
    assert "ACME ESTATES - L.L.C - AUH" in page.text
    assert "TEST-POL-0001" in page.text


def test_case_form_offers_the_master_and_uses_its_values(client):
    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        company, _ = master.get_or_create_client(session, "ACME FACILITIES DEMO LLC")
        sub_group, _ = master.get_or_create_sub_group(
            session, company, "DEMO-DXB", emirate="Dubai"
        )
        entity, _ = master.get_or_create_entity(
            session, company, "ACME Estates", contract_name="ACME ESTATES - L.L.C - DXB"
        )
        policy, _ = master.get_or_create_policy(
            session,
            company,
            insurer="DEMO INSURANCE (TEST)",
            entity=entity,
            policy_no="TEST-POL-0002",
            category="CAT A",
            emirate="Dubai",
            addition_template_key="nas.addition.aldar.v1",
        )
        ids = (company.id, sub_group.id, entity.id, policy.id)

    form = client.get("/cases/new")
    assert "ACME FACILITIES DEMO LLC" in form.text

    company_id, sub_group_id, entity_id, policy_id = ids
    created = client.post(
        "/cases",
        data={
            "transaction_type": "addition",
            "client_id": company_id,
            "sub_group_id": sub_group_id,
            "legal_entity_id": entity_id,
            "client_policy_id": policy_id,
            "bms_comments": "Synthetic instruction for a master-driven case.",
        },
    )
    assert created.status_code == 303

    with db.session_scope() as session:
        case = session.scalars(select(Case)).one()
        # Master values populate the case; nothing was typed.
        assert case.client_name == "ACME FACILITIES DEMO LLC"
        assert case.sub_group == "DEMO-DXB"
        assert case.contract_name == "ACME ESTATES - L.L.C - DXB"
        assert case.insurer == "DEMO INSURANCE (TEST)"
        assert case.policy_no == "TEST-POL-0002"
        assert case.category == "CAT A"
        assert case.template_key == "nas.addition.aldar.v1"
        assert case.client_id == company_id


def test_case_still_accepts_free_text_when_the_company_is_not_in_the_master(client):
    sign_in(client, "admin", "admin-password")
    created = client.post(
        "/cases",
        data={
            "transaction_type": "addition",
            "client_name": "ONE-OFF DEMO CLIENT",
            "insurer": "DEMO INSURANCE (TEST)",
            "bms_comments": "Synthetic instruction.",
        },
    )
    assert created.status_code == 303
    with db.session_scope() as session:
        case = session.scalars(select(Case)).one()
        assert case.client_name == "ONE-OFF DEMO CLIENT"
        assert case.client_id is None


def test_case_rejects_master_records_from_another_client(client):
    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        first, _ = master.get_or_create_client(session, "FIRST DEMO LLC")
        second, _ = master.get_or_create_client(session, "SECOND DEMO LLC")
        subgroup, _ = master.get_or_create_sub_group(session, second, "OTHER-HQ")
        first_id, subgroup_id = first.id, subgroup.id

    response = client.post(
        "/cases",
        data={
            "transaction_type": "addition",
            "client_id": first_id,
            "sub_group_id": subgroup_id,
            "insurer": "DEMO INSURANCE (TEST)",
        },
    )

    assert response.status_code == 400
    with db.session_scope() as session:
        assert session.scalar(select(Case)) is None


def test_policy_rejects_a_legal_entity_from_another_client(client):
    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        first, _ = master.get_or_create_client(session, "POLICY OWNER DEMO LLC")
        second, _ = master.get_or_create_client(session, "ENTITY OWNER DEMO LLC")
        entity, _ = master.get_or_create_entity(session, second, "OTHER ENTITY")
        first_id, entity_id = first.id, entity.id

    response = client.post(
        f"/admin/clients/{first_id}/policies",
        data={
            "insurer": "DEMO INSURANCE (TEST)",
            "legal_entity_id": entity_id,
        },
    )

    assert response.status_code == 400


def test_a_case_needs_a_client_from_somewhere(client):
    sign_in(client, "admin", "admin-password")
    response = client.post("/cases", data={"transaction_type": "addition", "bms_comments": ""})
    assert response.status_code == 400


def test_sub_group_emirate_drives_the_deletion_date(client):
    """Registered configuration, not text inference, decides the Dubai rule."""

    from bms import pipeline

    sign_in(client, "admin", "admin-password")
    with db.session_scope() as session:
        company, _ = master.get_or_create_client(session, "DUBAI DEMO LLC")
        sub_group, _ = master.get_or_create_sub_group(session, company, "HQ", emirate="Dubai")
        company_id, sub_group_id = company.id, sub_group.id

    client.post(
        "/cases",
        data={
            "transaction_type": "deletion",
            "client_id": company_id,
            "sub_group_id": sub_group_id,
            "insurer": "DEMO INSURANCE (TEST)",
            "bms_comments": "Please cancel staff 90001, card ABCD-EFGH-IJKL-MNOP.",
        },
    )
    with db.session_scope() as session:
        case = session.scalars(select(Case)).one()
        assert pipeline._case_emirate(case, session) == "Dubai"
