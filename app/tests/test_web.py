"""Web tier: authentication, the case journey and the review screen.

Exercised through the real ASGI app so routing, templates and the session cookie
are all covered.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.models import Base, Case, Member, User  # noqa: E402
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
    return TestClient(web_app.app, follow_redirects=False)


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
