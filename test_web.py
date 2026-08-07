"""Web tier: authentication, the case journey and the review screen.

Exercised through the real ASGI app so routing, templates and the session cookie
are all covered.
"""

from __future__ import annotations

import io
import itertools
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import BrowserClient  # noqa: E402
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.models import Base, Case, CaseFile, Member, User  # noqa: E402
from bms.web import app as web_app  # noqa: E402
from bms.web.security import (  # noqa: E402
    hash_password,
    issue_session,
    read_session,
    verify_password,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

INSTRUCTION = """
Kindly add the below employee to the policy.
82270 Rithick Kannadhasan Single CAT C
"""

VISA_TEXT = """RESIDENCE VISA
Staff ID: 82270
U.I.D No: 58620894
Emirates ID 784-1998-0432182-8
"""


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'web.db'}",
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
    with db.session_scope() as session:
        session.add(
            User(
                username="tester",
                display_name="Test User",
                log_associate="JAHNVI",
                password_hash=hash_password("secret"),
            )
        )
    # follow_redirects=False so 303s are asserted rather than silently followed.
    return BrowserClient(web_app.app, follow_redirects=False)


def sign_in(client: TestClient) -> None:
    response = client.post("/login", data={"username": "tester", "password": "secret"})
    assert response.status_code == 303
    assert client.cookies.get("bms_session")


# ---------------------------------------------------------------- passwords


def test_password_hashing_round_trip():
    encoded = hash_password("correct horse")
    assert "pbkdf2_sha256" in encoded
    assert "correct horse" not in encoded
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong", encoded)


def test_session_token_is_tamper_evident(monkeypatch):
    monkeypatch.setenv("BMS_SECRET_KEY", "test-key-not-for-production")
    token = issue_session("user-123")
    assert read_session(token) == "user-123"
    payload, signature = token.rsplit(".", 1)
    assert read_session(f"{payload}.{'A' * len(signature)}") is None
    assert read_session(None) is None
    assert read_session("nonsense") is None


# --------------------------------------------------------------------- auth


def test_anonymous_access_is_redirected_to_login(client):
    for path in ("/", "/cases/new", "/audit"):
        assert client.get(path).status_code == 303


def test_bad_credentials_are_rejected(client):
    response = client.post("/login", data={"username": "tester", "password": "nope"})
    assert response.status_code == 200
    assert "Incorrect username or password" in response.text
    assert client.cookies.get("bms_session") is None


def test_sign_in_and_out(client):
    sign_in(client)
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 303


# ------------------------------------------------------------- case journey


def test_case_can_be_created_and_persisted(client):
    sign_in(client)
    response = client.post(
        "/cases",
        data={
            "client_name": "BASATIN",
            "insurer": "QATAR INSURANCE",
            "transaction_type": "addition",
            "bms_comments": INSTRUCTION,
            "category": "CAT C",
        },
    )
    assert response.status_code == 303

    with db.session_scope() as session:
        case = session.scalars(select(Case)).one()
        assert case.client_name == "BASATIN"
        # The pasted instruction is stored verbatim, not reformatted.
        assert case.bms_comments == INSTRUCTION

    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "BASATIN" in page.text
    assert "BMS Comments" in page.text


def test_upload_process_review_and_blocked_export(client):
    sign_in(client)
    created = client.post(
        "/cases",
        data={
            "client_name": "BASATIN",
            "insurer": "QATAR INSURANCE",
            "transaction_type": "addition",
            "bms_comments": INSTRUCTION,
            "category": "CAT C",
        },
    )
    case_url = created.headers["location"]
    case_id = case_url.rsplit("/", 1)[1]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Visa.txt", VISA_TEXT)
    upload = client.post(
        f"/cases/{case_id}/upload",
        files=[("files", ("82270.zip", buffer.getvalue(), "application/zip"))],
    )
    assert upload.status_code == 303

    assert client.post(f"/cases/{case_id}/process").status_code == 303

    page = client.get(case_url)
    assert page.status_code == 200
    assert "Rithick" in page.text

    with db.session_scope() as session:
        member = session.scalars(select(Member).where(Member.case_id == case_id)).first()
        assert member is not None
        assert member.emirates_id == "784-1998-0432182-8"
        member_id = member.id

    review = client.get(f"/cases/{case_id}/members/{member_id}")
    assert review.status_code == 200
    assert "784-1998-0432182-8" in review.text
    # Provenance is shown so a reviewer can see where a value came from.
    assert "Visa.txt" in review.text

    # Mandatory fields are still missing, so export must be refused.
    blocked = client.post(f"/cases/{case_id}/export", data={"template_key": "nas.addition.aldar.v1"})
    assert blocked.status_code == 200
    assert "critical" in blocked.text.lower()


def test_correction_is_saved_and_revalidated(client):
    sign_in(client)
    created = client.post(
        "/cases",
        data={
            "client_name": "BASATIN",
            "insurer": "QATAR INSURANCE",
            "transaction_type": "addition",
            "bms_comments": INSTRUCTION,
        },
    )
    case_id = created.headers["location"].rsplit("/", 1)[1]
    client.post(f"/cases/{case_id}/process")

    with db.session_scope() as session:
        member_id = session.scalars(select(Member).where(Member.case_id == case_id)).first().id

    response = client.post(
        f"/cases/{case_id}/members/{member_id}",
        data={"nationality": "India", "gender": "Male"},
    )
    assert response.status_code == 303

    with db.session_scope() as session:
        member = session.get(Member, member_id)
        assert member.nationality == "India"
        assert member.provenance["nationality"]["reviewed"] is True


def test_reclassification_rejects_unknown_types_and_members_from_other_cases(client):
    sign_in(client)
    case_ids = []
    for staff_id in ("82270", "82271"):
        created = client.post(
            "/cases",
            data={
                "client_name": "BASATIN",
                "insurer": "QATAR INSURANCE",
                "transaction_type": "addition",
                "bms_comments": f"Kindly add {staff_id} Demo Member Single CAT C",
            },
        )
        case_id = created.headers["location"].rsplit("/", 1)[1]
        case_ids.append(case_id)
        client.post(
            f"/cases/{case_id}/upload",
            files={"files": (f"{staff_id}.txt", VISA_TEXT, "text/plain")},
        )
        client.post(f"/cases/{case_id}/process")

    with db.session_scope() as session:
        file_id = session.scalar(
            select(CaseFile.id).where(CaseFile.case_id == case_ids[0])
        )
        other_member_id = session.scalar(
            select(Member.id).where(Member.case_id == case_ids[1])
        )

    unknown = client.post(
        f"/cases/{case_ids[0]}/files/{file_id}/classify",
        data={"document_type": "forged", "member_id": ""},
    )
    cross_case = client.post(
        f"/cases/{case_ids[0]}/files/{file_id}/classify",
        data={"document_type": "visa", "member_id": other_member_id},
    )

    assert unknown.status_code == 400
    assert cross_case.status_code == 400


def test_audit_page_lists_activity(client):
    sign_in(client)
    client.post(
        "/cases",
        data={
            "client_name": "BASATIN",
            "insurer": "QATAR INSURANCE",
            "transaction_type": "addition",
            "bms_comments": INSTRUCTION,
        },
    )
    page = client.get("/audit")
    assert page.status_code == 200
    assert "case.create" in page.text


def test_health_reports_the_available_ocr_engines(client):
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert "ocr_engines" in payload
    assert payload["database"] == "sqlite"


# ------------------------------------------------- paging and browser weight
#
# The operational log is the permanent record and only ever grows. A screen that
# renders every matching row gets heavier every month until it stops being
# usable, so these fix the shape of the pages rather than trusting them to stay
# reasonable.


_seeded = itertools.count()


def _seed_log(count: int) -> list[str]:
    """`count` log entries, each on its own case. Returns their ids.

    Case references are globally unique, so the counter keeps repeat calls in
    one test from colliding.
    """
    from datetime import date

    from bms.models import LogEntry

    ids = []
    with db.session_scope() as session:
        for index in range(count):
            case = Case(
                reference=f"L{next(_seeded):05d}", client_name="DEMO CLIENT", insurer="NAS",
                transaction_type="addition", status="exported", bms_comments="",
            )
            session.add(case)
            session.flush()
            entry = LogEntry(
                case_id=case.id, client_name="DEMO CLIENT", insurer="NAS",
                beneficiary_name=f"Person {index}", relation="Principal",
                entry_type="ADDITION", effective_date=date.today().isoformat(),
                status="PENDING TO INSURER",
            )
            session.add(entry)
            session.flush()
            ids.append(entry.id)
    return ids


def test_the_log_shows_one_page_however_many_rows_there_are(client):
    sign_in(client)
    _seed_log(120)

    page = client.get("/log")
    assert page.status_code == 200
    # 50 data rows plus the header row.
    assert page.text.count("<tr") == web_app.PAGE_SIZE + 1
    assert "120 row(s)" in page.text          # the total is still reported
    assert "page 1 of 3" in page.text


def test_paging_reaches_every_row_without_overlap(client):
    sign_in(client)
    _seed_log(120)

    seen = []
    for number in (1, 2, 3):
        text = client.get(f"/log?page={number}").text
        seen += [name for name in (f"Person {i}" for i in range(120)) if f">{name}<" in text]

    assert len(seen) == 120, "every row should appear exactly once across the pages"
    assert len(set(seen)) == 120


def test_out_of_range_page_numbers_are_clamped_not_crashed(client):
    """A hand-typed ?page=0 must not become a negative SQL OFFSET."""
    sign_in(client)
    _seed_log(60)

    for value in ("0", "-5", "9999"):
        response = client.get(f"/log?page={value}")
        assert response.status_code == 200, value
        assert "Person" in response.text


def test_the_log_list_carries_no_per_row_editor(client):
    """The five workflow forms live on the entry screen, not in every row.

    Rendering them per row put thousands of controls in the DOM whether or not
    anyone used them, which is what made the screen heavy.
    """
    sign_in(client)
    _seed_log(50)

    text = client.get("/log").text
    # The only form posting to a log route is the export button.
    assert text.count('action="/log/') == 1
    assert 'action="/log/export"' in text
    assert 'name="request_ref_no"' not in text
    assert 'name="saiba_voucher_no"' not in text


def test_the_entry_screen_carries_the_editor(client):
    sign_in(client)
    entry_id = _seed_log(1)[0]

    page = client.get(f"/log/{entry_id}")
    assert page.status_code == 200
    for field in ("request_ref_no", "card_no", "saiba_voucher_no", "bbm_invoice_date", "remarks"):
        assert f'name="{field}"' in page.text
    assert client.get("/log/does-not-exist").status_code == 404


def test_page_weight_does_not_grow_with_the_log(client):
    """The guarantee, stated as a number: ten times the rows, same page.

    Measured at 3.1 MB and 57,081 elements before paging, at 1,000 entries.
    """
    sign_in(client)

    _seed_log(60)
    small = client.get("/log").text

    _seed_log(600)
    large = client.get("/log").text

    assert abs(len(large) - len(small)) < 2048, "page size should not track the row count"
    for text in (small, large):
        assert len(text.encode()) < 200 * 1024
        assert text.count("<") < 5000


def test_cases_and_audit_are_paged_too(client):
    sign_in(client)
    _seed_log(120)

    cases = client.get("/")
    assert cases.text.count("<tr") <= web_app.PAGE_SIZE + 1

    audit = client.get("/audit")
    assert audit.status_code == 200
    assert audit.text.count("<tr") <= web_app.PAGE_SIZE + 1


def test_the_case_form_does_not_embed_the_whole_client_master(client):
    """It embedded every company's sub-groups, entities and policies -- 707 KB
    of JSON at 500 companies, on every visit, nearly all of it unused."""
    from bms.models import Client as ClientRecord, ClientPolicy, LegalEntity, SubGroup

    sign_in(client)
    with db.session_scope() as session:
        for index in range(40):
            record = ClientRecord(name=f"COMPANY {index:03d} TRADING L.L.C.")
            session.add(record)
            session.flush()
            for k in range(3):
                session.add(SubGroup(client_id=record.id, name=f"Division {k}", emirate="Dubai"))
                session.add(LegalEntity(client_id=record.id, name=f"Entity {index}-{k}"))
                session.add(
                    ClientPolicy(client_id=record.id, insurer="NAS", policy_no=f"P{index}{k}")
                )

    page = client.get("/cases/new")
    assert page.status_code == 200
    # The company names are needed to choose from; their contents are not.
    assert "COMPANY 000 TRADING L.L.C." in page.text
    assert "Division 0" not in page.text
    assert "Entity 0-0" not in page.text


def test_one_companys_options_are_fetched_on_demand(client):
    from bms.models import Client as ClientRecord, ClientPolicy, LegalEntity, SubGroup

    sign_in(client)
    with db.session_scope() as session:
        record = ClientRecord(name="ACME GROUP LLC")
        session.add(record)
        session.flush()
        session.add(SubGroup(client_id=record.id, name="Abu Dhabi Operations", emirate="Abu Dhabi"))
        session.add(LegalEntity(client_id=record.id, name="ACME Trading LLC"))
        session.add(ClientPolicy(client_id=record.id, insurer="NAS", policy_no="POL-1"))
        client_id = record.id

    payload = client.get(f"/clients/{client_id}/options")
    assert payload.status_code == 200
    body = payload.json()
    assert body["name"] == "ACME GROUP LLC"
    assert body["sub_groups"][0]["emirate"] == "Abu Dhabi"
    assert body["entities"][0]["name"] == "ACME Trading LLC"
    assert body["policies"][0]["policy_no"] == "POL-1"

    assert client.get("/clients/nope/options").status_code == 404


def test_the_client_master_is_not_readable_without_signing_in(client):
    """It is BMS commercial data, not public reference."""
    response = client.get("/clients/anything/options")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_case_form_title_is_a_title(client):
    """The whole dropdown script once sat inside {% block title %}, so it was
    rendered into <title> as well as the body."""
    sign_in(client)
    page = client.get("/cases/new")
    import re

    title = re.search(r"<title>(.*?)</title>", page.text, re.S).group(1)
    assert title.strip() == "New case"
    assert "<script" not in title
    assert page.text.count("<script") == 1


# ------------------------------------------------------------- error branches
#
# Four different refusals all mean "this must not be sent to the insurer". Only
# one of them was handled; the rest reached the browser as an unexplained 500 --
# worst of all StructuralDrift, the check that stops a damaged workbook being
# uploaded to a portal.


def _approved_case(client) -> str:
    """A case with one approved member, ready to export."""
    from datetime import date

    sign_in(client)
    created = client.post(
        "/cases",
        data={
            "client_name": "DEMO", "insurer": "NAS", "transaction_type": "addition",
            "bms_comments": "Add 90001 Demo Person Single CAT B",
        },
    )
    case_id = created.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/cases/{case_id}/process")
    with db.session_scope() as session:
        member = session.scalars(select(Member).where(Member.case_id == case_id)).one()
        member.first_name, member.last_name = "Demo", "Person"
        member.date_of_birth, member.gender = "1990-01-01", "Female"
        member.marital_status, member.nationality = "Single", "India"
        member.relation, member.category = "Principal", "CAT B"
        member.effective_date = date.today().isoformat()
        member.emirates_id = "784-1990-1234567-1"
        member.provenance = {}
        member_id = member.id
    client.post(f"/cases/{case_id}/members/{member_id}/approve")
    return case_id


@pytest.mark.parametrize(
    "exception, expected",
    [
        ("StructuralDrift", "no longer matches the master"),
        ("UnsupportedValue", "cannot express"),
        ("UnknownField", "does not exist in it"),
    ],
)
def test_every_export_refusal_is_explained_not_a_500(client, monkeypatch, exception, expected):
    case_id = _approved_case(client)

    from bms.outputs.mapping import UnsupportedValue
    from bms.registry.generate import StructuralDrift, UnknownField

    raised = {"StructuralDrift": StructuralDrift, "UnsupportedValue": UnsupportedValue,
              "UnknownField": UnknownField}[exception]

    def refuse(*args, **kwargs):
        if exception == "StructuralDrift":
            raise raised("nas.addition.aldar.v1", ["sheet order changed"])
        raise raised("Parent")

    monkeypatch.setattr("bms.web.app.pipeline.export_case", refuse)
    response = client.post(f"/cases/{case_id}/export", data={"template_key": "nas.addition.aldar.v1"})

    assert response.status_code == 200, "a refusal is shown on the case screen, not a 500"
    assert expected in response.text
    assert "Traceback" not in response.text


def test_a_refusal_partway_through_leaves_no_half_export(client, monkeypatch):
    """The portal workbook is recorded before the log is built.

    So a refusal raised by the log step used to commit an Export row for a case
    whose export never completed -- the request's session commits on the way out
    regardless of what the route rendered. A refused export must leave nothing
    behind but the reason.
    """
    from bms.models import Export
    from bms.registry.generate import StructuralDrift

    case_id = _approved_case(client)

    def write_the_portal_row_then_refuse(session, case, **kwargs):
        """Exactly the shape of the real thing: portal recorded, log refuses."""
        session.add(
            Export(
                case_id=case.id,
                kind="portal",
                filename="half.xlsx",
                relative_path="x/half.xlsx",
                sha256="0" * 64,
            )
        )
        session.flush()
        raise StructuralDrift("bms.log.2026", ["header row moved"])

    monkeypatch.setattr("bms.web.app.pipeline.export_case", write_the_portal_row_then_refuse)
    refused = client.post(
        f"/cases/{case_id}/export", data={"template_key": "nas.addition.aldar.v1"}
    )
    assert refused.status_code == 200
    assert "no longer matches the master" in refused.text

    with db.session_scope() as session:
        recorded = session.scalars(select(Export).where(Export.case_id == case_id)).all()
    assert recorded == [], f"a refused export recorded {len(recorded)} file(s)"


def test_a_refused_export_shows_the_case_screen_the_case_screen_shows(client, monkeypatch):
    """The two routes that render case_detail.html had drifted apart.

    Each assembled the page's context separately, and the refusal path had lost
    the ordering: it listed exports oldest-first, so the file the operator had
    just tried to produce dropped to the bottom of the list at exactly the moment
    they were looking for it. Both now build the context in one place.
    """
    from bms.models import Export

    case_id = _approved_case(client)
    with db.session_scope() as session:
        for index, name in enumerate(["first.xlsx", "second.xlsx", "third.xlsx"]):
            session.add(
                Export(
                    case_id=case_id,
                    kind="portal",
                    filename=name,
                    relative_path=f"x/{name}",
                    sha256="0" * 64,
                    created_at=datetime(2026, 1, 1 + index, tzinfo=timezone.utc),
                )
            )

    def refuse(*args, **kwargs):
        raise web_app.pipeline.ExportBlocked("nothing approved")

    monkeypatch.setattr("bms.web.app.pipeline.export_case", refuse)
    refused = client.post(
        f"/cases/{case_id}/export", data={"template_key": "nas.addition.aldar.v1"}
    )
    shown = client.get(f"/cases/{case_id}")

    names = ("first.xlsx", "second.xlsx", "third.xlsx")

    def order(page: str) -> list[str]:
        """The filenames in the order the page actually puts them in."""
        assert all(name in page for name in names)
        return sorted(names, key=page.index)

    assert order(refused.text) == order(shown.text) == ["third.xlsx", "second.xlsx", "first.xlsx"]


def test_an_unexpected_error_gives_a_reference_and_no_stack_trace(client, monkeypatch):
    """A traceback in the page would disclose paths, versions and query text."""
    import re

    sign_in(client)

    def explode(*args, **kwargs):
        raise RuntimeError("database connection lost mid-query")

    monkeypatch.setattr("bms.web.app.logbook.count", explode)
    unhandled = BrowserClient(web_app.app, raise_server_exceptions=False)
    unhandled.cookies = client.cookies
    response = unhandled.get("/log")

    assert response.status_code == 500
    assert "database connection lost" not in response.text
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    assert re.search(r"<code>[0-9a-f]{12}</code>", response.text), "a reference to quote"


def test_an_error_message_never_reflects_input_as_live_markup(client):
    """Several 400s quote back what the user sent, and that page is hand-built.

    The templates are escaped by Jinja, but this response is assembled as a
    string, so an unescaped detail is a reflected script injection -- and the
    page's own CSP permits inline script, so it would execute rather than merely
    render. Here the attacker's channel is the username field.
    """
    sign_in(client)
    with db.session_scope() as session:
        session.scalar(select(User).where(User.username == "tester")).is_admin = True

    payload = "<script>alert('xss')</script>"
    form = {"username": payload, "display_name": "x", "password": "password123"}
    assert client.post("/admin/users", data=form).status_code == 303

    reflected = client.post("/admin/users", data=form)
    assert reflected.status_code == 400
    assert payload not in reflected.text, "the raw script tag reached the browser"
    assert "&lt;script&gt;" in reflected.text, "it should be shown, escaped"


def test_an_administrator_import_is_bounded_like_every_other_upload(client, monkeypatch):
    """This route read the whole body first and checked the size never.

    The limit that protects /cases/{id}/upload did not apply here at all, so one
    oversized workbook was fully resident in memory before anything looked at it.
    """
    sign_in(client)
    with db.session_scope() as session:
        session.scalar(select(User).where(User.username == "tester")).is_admin = True
    import dataclasses

    # Settings is frozen by design, so swap the object rather than mutate it.
    monkeypatch.setattr(
        web_app, "settings", dataclasses.replace(web_app.settings, max_upload_bytes=1024)
    )

    response = client.post(
        "/admin/clients/import",
        files={"workbook": ("big.xlsx", io.BytesIO(b"x" * 5000), "application/vnd.ms-excel")},
    )
    assert response.status_code == 400
    assert "maximum upload size" in response.text


# --------------------------------------------------- hardening and efficiency


def test_repeated_wrong_passwords_lock_the_account_out(client):
    """Unthrottled, the login form is two problems at once: an unlimited guessing
    oracle, and a way for one unauthenticated client to burn the host's CPU --
    every attempt costs 240,000 PBKDF2 rounds by design."""
    from bms.web import security

    security.reset_throttle()
    try:
        for _ in range(security.FAILURE_LIMIT):
            refused = client.post("/login", data={"username": "tester", "password": "wrong"})
            assert "Incorrect username or password" in refused.text

        blocked = client.post("/login", data={"username": "tester", "password": "wrong"})
        assert "Too many failed sign-in attempts" in blocked.text

        # The correct password is refused too while the lockout stands, so the
        # throttle cannot be stepped around by guessing right on the next try.
        assert client.post(
            "/login", data={"username": "tester", "password": "secret"}
        ).status_code == 200
    finally:
        security.reset_throttle()


def test_a_successful_sign_in_clears_the_failure_count(client):
    from bms.web import security

    security.reset_throttle()
    try:
        for _ in range(security.FAILURE_LIMIT - 1):
            client.post("/login", data={"username": "tester", "password": "wrong"})
        assert client.post(
            "/login", data={"username": "tester", "password": "secret"}
        ).status_code == 303
        assert security.locked_out("user:tester") == 0
    finally:
        security.reset_throttle()


def test_a_disabled_account_fails_exactly_like_a_wrong_password(client):
    """So the form cannot be used to discover which accounts exist or are live."""
    from bms.web import security

    security.reset_throttle()
    try:
        with db.session_scope() as session:
            session.scalar(select(User).where(User.username == "tester")).active = False
        refused = client.post("/login", data={"username": "tester", "password": "secret"})
        assert refused.status_code == 200
        assert "Incorrect username or password" in refused.text
        assert client.cookies.get("bms_session") is None
    finally:
        security.reset_throttle()


def test_every_response_carries_the_browser_defences(client):
    """The platform serves member identity data: a page of it must not be
    frameable, sniffable, or leak its case and member ids through Referer."""
    for path in ("/login", "/"):
        headers = client.get(path).headers
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        policy = headers["Content-Security-Policy"]
        assert "default-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "object-src 'none'" in policy


def test_the_policy_refuses_inline_script(client):
    """With 'unsafe-inline' on script-src, any markup that reaches a page runs.

    That is what turned an unescaped error message into a live script injection
    rather than a display bug. Nothing needs the relaxation: the platform's only
    script is served from /static.
    """
    policy = client.get("/login").headers["Content-Security-Policy"]
    script_src = next(d for d in policy.split(";") if d.strip().startswith("script-src"))
    assert "unsafe-inline" not in script_src, script_src


def test_no_page_carries_inline_script_the_policy_would_block(client):
    """A page written with an inline <script> or an on* handler is now dead code
    in the browser, and would fail silently rather than loudly."""
    templates = Path(__file__).resolve().parents[1] / "bms" / "web" / "templates"
    for path in sorted(templates.glob("*.html")):
        body = path.read_text()
        assert "<script>" not in body, f"{path.name}: inline script the CSP blocks"
        assert not re.search(r"\son[a-z]+=", body), f"{path.name}: inline event handler"


def test_the_stylesheet_and_script_are_served_and_revalidate(client):
    """Served as files rather than re-sent inside every page.

    The stylesheet was 3.3 KB of identical bytes on all eleven screens; as a file
    the browser fetches it once and revalidates with a 304 after that.
    """
    for asset, kind in (("/static/bms.css", "css"), ("/static/case_new.js", "javascript")):
        first = client.get(asset)
        assert first.status_code == 200
        assert kind in first.headers["content-type"]
        assert first.headers.get("etag"), "no etag, so the browser cannot revalidate"

        again = client.get(asset, headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304, "a repeat visit re-downloads the whole file"


def test_hsts_is_only_sent_when_the_cookie_is_secure(client):
    """Promising HTTPS-only over a plain-HTTP deployment would lock users out."""
    assert "Strict-Transport-Security" not in client.get("/login").headers


def test_an_oversized_upload_is_refused_without_being_held_in_memory(client, monkeypatch):
    """The limit was only applied after `await read()` had pulled the whole body
    into RAM, so it could not prevent what it existed to prevent."""
    sign_in(client)
    created = client.post(
        "/cases",
        data={"client_name": "DEMO", "insurer": "NAS", "transaction_type": "addition",
              "bms_comments": ""},
    )
    case_id = created.headers["location"].rsplit("/", 1)[-1]

    import dataclasses

    # Settings is frozen by design, so swap the object rather than mutate it.
    monkeypatch.setattr(
        web_app, "settings", dataclasses.replace(web_app.settings, max_upload_bytes=4096)
    )
    huge = b"x" * (256 * 1024)
    response = client.post(
        f"/cases/{case_id}/upload", files={"files": ("huge.txt", io.BytesIO(huge), "text/plain")}
    )
    assert response.status_code == 303

    from bms.models import CaseFile

    with db.session_scope() as session:
        stored = session.scalars(select(CaseFile).where(CaseFile.case_id == case_id)).all()
    assert stored == [], "an oversized file must never reach storage"


def test_reading_stops_at_the_limit_rather_than_buffering_everything():
    """Peak memory is bounded by the limit, whatever the client sends."""
    import asyncio

    class Endless:
        """A body that never ends -- the shape of the attack."""

        def __init__(self):
            self.served = 0

        async def read(self, size: int = -1) -> bytes:
            self.served += size
            return b"y" * size

    body = Endless()
    result = asyncio.run(web_app._read_bounded(body, 1024 * 1024))
    assert result is None
    # Bounded to roughly the limit plus the one chunk that crossed it.
    assert body.served <= 3 * 1024 * 1024


# ------------------------------------------------------------------ CSRF
#
# SameSite=Lax already blocks the classic cross-site form POST. These cover the
# rest: a same-site attack, a browser that does not honour SameSite, and any
# future deployment reachable from outside the BMS network.


def test_a_state_changing_post_without_a_token_is_rejected(client):
    sign_in(client)
    refused = client.post(
        "/cases",
        data={"client_name": "DEMO", "insurer": "NAS", "transaction_type": "addition"},
        csrf=False,
    )
    assert refused.status_code == 403
    assert "not submitted from a current session" in refused.text

    with db.session_scope() as session:
        assert session.scalars(select(Case)).all() == [], "nothing may be created"


def test_a_token_from_another_session_is_rejected(client):
    """The token is derived from the session, so one user's cannot act for another."""
    from bms.web.security import CSRF_FIELD, csrf_token

    sign_in(client)
    foreign = csrf_token("a-different-sessions-cookie-value")
    refused = client.post(
        "/cases",
        data={
            "client_name": "DEMO", "insurer": "NAS", "transaction_type": "addition",
            CSRF_FIELD: foreign,
        },
        csrf=False,
    )
    assert refused.status_code == 403


def test_signing_out_is_protected_too(client):
    """Otherwise an attacker can sign a user out at will -- minor, but free to stop."""
    sign_in(client)
    assert client.post("/logout", csrf=False).status_code == 403
    assert client.post("/logout").status_code == 303


def test_signing_out_works_with_only_what_the_page_actually_contains(client):
    """The regression the counting test below could not see.

    BrowserClient attaches a token to every POST, so it proves the guard accepts
    a token -- not that the rendered page carries one where the browser will find
    it. The hidden field sat one line outside the sign-out form, so a real
    browser submitted nothing and every sign-out was refused as a forgery. This
    submits exactly the fields inside that form and nothing else.
    """
    sign_in(client)
    page = client.get("/").text
    form = re.search(r'<form[^>]*action="/logout".*?</form>', page, re.S)
    assert form, "the sign-out form is not on the page at all"
    fields = dict(
        re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(0))
    )
    assert client.post("/logout", data=fields, csrf=False).status_code == 303


def test_login_is_exempt_because_it_has_no_session_yet(client):
    """The only exemption, and it must keep working."""
    assert client.post(
        "/login", data={"username": "tester", "password": "secret"}
    ).status_code == 303


def test_every_rendered_form_carries_a_token(client):
    """A form added later without one would fail at 403 in front of a user.

    Checked against the templates rather than a rendered page, so forms on
    screens this test never visits are covered too.

    Counting tokens per file is not enough and used to be all this did: the
    sign-out form and its token were both present in base.html, the counts
    matched, and the token was outside the form where no browser would send it.
    Each POST form is now checked for a token between its own tags.
    """
    templates = Path(__file__).resolve().parents[1] / "bms" / "web" / "templates"
    for path in sorted(templates.glob("*.html")):
        if path.name == "login.html":
            continue  # exempt: no session exists yet
        body = path.read_text()
        for match in re.finditer(r'<form[^>]*method="post"[^>]*>', body):
            end = body.find("</form>", match.end())
            assert end != -1, f"{path.name}: unclosed POST form"
            inside = body[match.end() : end]
            assert "csrf_field" in inside, (
                f"{path.name}: a POST form has no token inside it "
                f"-- {match.group(0)[:60]}"
            )


def test_the_token_is_not_guessable_from_the_session_id(client):
    """It is an HMAC under the signing key, not a transform of the cookie."""
    from bms.web.security import csrf_token

    cookie = "some-session-value"
    token = csrf_token(cookie)
    assert cookie not in token
    assert len(token) == 64
    assert csrf_token(cookie + "x") != token


def test_the_login_throttle_cannot_be_used_to_exhaust_memory(client):
    """The tracking must not become the denial of service it prevents.

    Every distinct username tried creates an entry and the attacker chooses the
    usernames, so an unbounded table is memory exhaustion by unauthenticated
    request. Measured at 50,000 distinct names before the cap existed.
    """
    from bms.web import security

    security.reset_throttle()
    try:
        for index in range(security.MAX_TRACKED_KEYS * 3):
            security.record_failure(f"user:flood-{index}")
        assert len(security._failures) <= security.MAX_TRACKED_KEYS
    finally:
        security.reset_throttle()


def test_a_real_lockout_survives_a_flood_of_other_names(client):
    """Eviction must not become a way to clear your own lockout."""
    from bms.web import security

    security.reset_throttle()
    try:
        for _ in range(security.FAILURE_LIMIT):
            security.record_failure("user:victim")
        assert security.locked_out("user:victim") > 0

        for index in range(security.MAX_TRACKED_KEYS * 3):
            security.record_failure(f"user:noise-{index}")

        assert security.locked_out("user:victim") > 0, "a lockout must not be evictable"
        assert len(security._failures) <= security.MAX_TRACKED_KEYS
    finally:
        security.reset_throttle()
