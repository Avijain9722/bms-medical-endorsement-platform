"""Internal browser application.

Server-rendered, no client framework, no state in the browser. Every case, file
and correction is written to the database and the filesystem as it happens, so
work survives a refresh, a logout, a restart and the end of the day.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pipeline
from ..config import settings
from ..db import create_all, get_session, init_engine
from ..models import (
    AuditEvent,
    Case,
    CaseFile,
    CaseStatus,
    DocumentType,
    Export,
    Member,
    ReviewFlag,
    Severity,
    TransactionType,
    User,
)
from ..outputs.nas import SUPPORTED_KEYS
from ..storage import ExportStorage
from .security import COOKIE_NAME, hash_password, issue_session, read_session, verify_password

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    init_engine()
    create_all()
    _ensure_seed_user()
    yield


app = FastAPI(
    title="BMS Medical Endorsement Platform",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


def _ensure_seed_user() -> None:
    """Create a first account so the prototype is usable out of the box.

    The password is printed once at startup and should be changed immediately.
    """
    from ..db import session_scope

    with session_scope() as session:
        if session.scalar(select(User).limit(1)):
            return
        import os
        import secrets

        password = os.environ.get("BMS_SEED_PASSWORD") or secrets.token_urlsafe(12)
        session.add(
            User(
                username="bms",
                display_name="BMS Medical Team",
                log_associate="JAHNVI",
                password_hash=hash_password(password),
            )
        )
        print(f"[bms] created initial user 'bms' with password: {password}", flush=True)


# ------------------------------------------------------------------- helpers


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    user_id = read_session(request.cookies.get(COOKIE_NAME))
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


@app.exception_handler(HTTPException)
async def _redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


def render(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


def _load_case(session: Session, case_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _flags_for(session: Session, case: Case) -> dict[str | None, list[ReviewFlag]]:
    grouped: dict[str | None, list[ReviewFlag]] = {}
    for flag in session.scalars(select(ReviewFlag).where(ReviewFlag.case_id == case.id)):
        grouped.setdefault(flag.member_id, []).append(flag)
    return grouped


# --------------------------------------------------------------------- auth


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return render(request, "login.html", error="Incorrect username or password.")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        issue_session(user.id),
        httponly=True,
        samesite="lax",
        # The prototype is served inside the BMS network; set secure=True behind TLS.
        secure=False,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# -------------------------------------------------------------------- cases


@app.get("/", response_class=HTMLResponse)
def case_list(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    cases = list(session.scalars(select(Case).order_by(Case.created_at.desc()).limit(200)))
    return render(request, "cases.html", cases=cases, user=user)


@app.get("/cases/new", response_class=HTMLResponse)
def new_case_form(
    request: Request,
    user: User = Depends(current_user),
):
    return render(request, "case_new.html", user=user, templates_available=SUPPORTED_KEYS)


@app.post("/cases")
def create_case(
    client_name: str = Form(...),
    insurer: str = Form(...),
    transaction_type: str = Form(...),
    bms_comments: str = Form(""),
    sub_group: str = Form(""),
    policy_no: str = Form(""),
    contract_name: str = Form(""),
    category: str = Form(""),
    email_subject: str = Form(""),
    email_received_date: str = Form(""),
    template_key: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    received = None
    if email_received_date:
        try:
            received = datetime.fromisoformat(email_received_date)
        except ValueError:
            received = None

    case = pipeline.create_case(
        session,
        client_name=client_name.strip(),
        insurer=insurer.strip(),
        transaction_type=transaction_type,
        actor=user.username,
        bms_comments=bms_comments,
        sub_group=sub_group.strip() or None,
        policy_no=policy_no.strip() or None,
        contract_name=contract_name.strip() or None,
        category=category.strip() or None,
        email_subject=email_subject.strip() or None,
        email_received_date=received,
        template_key=template_key or None,
        owner_id=user.id,
    )
    return RedirectResponse(f"/cases/{case.id}", status_code=303)


@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(
    case_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    members = list(
        session.scalars(select(Member).where(Member.case_id == case.id).order_by(Member.row_index))
    )
    files = list(session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)))
    exports = list(
        session.scalars(select(Export).where(Export.case_id == case.id).order_by(Export.created_at.desc()))
    )
    flags = _flags_for(session, case)
    blocking = pipeline.blocking_flags(session, case)

    return render(
        request,
        "case_detail.html",
        case=case,
        members=members,
        files=files,
        exports=exports,
        flags=flags,
        case_flags=flags.get(None, []),
        blocking=blocking,
        user=user,
        templates_available=SUPPORTED_KEYS,
        severity=Severity,
    )


@app.post("/cases/{case_id}/comments")
def update_comments(
    case_id: str,
    bms_comments: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    from .. import audit

    audit.record_field_change(
        session,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        field_key="bms_comments",
        old_value=(case.bms_comments or "")[:2000],
        new_value=bms_comments[:2000],
        actor=user.username,
    )
    case.bms_comments = bms_comments
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/upload")
async def upload(
    case_id: str,
    files: list[UploadFile],
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    for upload_file in files:
        data = await upload_file.read()
        if not data:
            continue
        pipeline.add_upload(
            session, case, upload_file.filename or "upload", data, actor=user.username
        )
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/process")
def process(
    case_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    case.status = CaseStatus.PROCESSING.value
    pipeline.analyse_files(session, case, actor=user.username)
    pipeline.build_members(session, case, actor=user.username)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


# ------------------------------------------------------------------ members


@app.get("/cases/{case_id}/members/{member_id}", response_class=HTMLResponse)
def member_detail(
    case_id: str,
    member_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    member = session.get(Member, member_id)
    if member is None or member.case_id != case.id:
        raise HTTPException(status_code=404, detail="Member not found")

    documents = list(session.scalars(select(CaseFile).where(CaseFile.member_id == member.id)))
    flags = list(session.scalars(select(ReviewFlag).where(ReviewFlag.member_id == member.id)))
    return render(
        request,
        "member.html",
        case=case,
        member=member,
        documents=documents,
        flags=flags,
        user=user,
        provenance=member.provenance or {},
        document_types=[t.value for t in DocumentType],
    )


EDITABLE_FIELDS = (
    "first_name", "middle_name", "last_name", "date_of_birth", "gender",
    "marital_status", "nationality", "passport_no", "passport_expiry",
    "emirates_id", "unified_no", "visa_file_number", "birth_certificate_number",
    "staff_id", "relation", "category", "contract_name", "effective_date",
    "member_card_no", "deletion_reason", "principal_card_no", "email", "mobile_no",
)


@app.post("/cases/{case_id}/members/{member_id}")
async def update_member(
    case_id: str,
    member_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    member = session.get(Member, member_id)
    if member is None or member.case_id != case.id:
        raise HTTPException(status_code=404, detail="Member not found")

    form = await request.form()
    for field_key in EDITABLE_FIELDS:
        if field_key in form:
            pipeline.update_member_field(
                session, member, field_key, str(form[field_key]), actor=user.username
            )
    pipeline.revalidate_member(session, case, member)
    return RedirectResponse(f"/cases/{case_id}/members/{member_id}", status_code=303)


@app.post("/cases/{case_id}/members/{member_id}/approve")
def approve(
    case_id: str,
    member_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    member = session.get(Member, member_id)
    if member is None or member.case_id != case.id:
        raise HTTPException(status_code=404, detail="Member not found")
    try:
        pipeline.approve_member(session, case, member, actor=user.username)
    except PermissionError as error:
        documents = list(session.scalars(select(CaseFile).where(CaseFile.member_id == member.id)))
        flags = list(session.scalars(select(ReviewFlag).where(ReviewFlag.member_id == member.id)))
        return render(
            request,
            "member.html",
            case=case,
            member=member,
            documents=documents,
            flags=flags,
            user=user,
            provenance=member.provenance or {},
            document_types=[t.value for t in DocumentType],
            error=str(error),
        )
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/files/{file_id}/classify")
def reclassify(
    case_id: str,
    file_id: str,
    document_type: str = Form(...),
    member_id: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from .. import audit

    record = session.get(CaseFile, file_id)
    if record is None or record.case_id != case_id:
        raise HTTPException(status_code=404, detail="File not found")

    audit.record_field_change(
        session,
        entity_type="case_file",
        entity_id=record.id,
        case_id=case_id,
        field_key="document_type",
        old_value=record.document_type,
        new_value=document_type,
        actor=user.username,
    )
    record.document_type = document_type
    record.manually_classified = True
    record.classification_confidence = 1.0
    if member_id:
        record.member_id = member_id
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


# ------------------------------------------------------------------ exports


@app.post("/cases/{case_id}/export")
def export(
    case_id: str,
    request: Request,
    template_key: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    try:
        pipeline.export_case(session, case, template_key=template_key, actor=user.username)
    except (pipeline.ExportBlocked, Exception) as error:  # noqa: BLE001 - surfaced to the user
        if not isinstance(error, pipeline.ExportBlocked):
            raise
        members = list(
            session.scalars(select(Member).where(Member.case_id == case.id).order_by(Member.row_index))
        )
        files = list(session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)))
        exports = list(session.scalars(select(Export).where(Export.case_id == case.id)))
        flags = _flags_for(session, case)
        return render(
            request,
            "case_detail.html",
            case=case,
            members=members,
            files=files,
            exports=exports,
            flags=flags,
            case_flags=flags.get(None, []),
            blocking=pipeline.blocking_flags(session, case),
            user=user,
            templates_available=SUPPORTED_KEYS,
            severity=Severity,
            error=str(error),
        )
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.get("/cases/{case_id}/exports/{export_id}/download")
def download(
    case_id: str,
    export_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    record = session.get(Export, export_id)
    if record is None or record.case_id != case_id:
        raise HTTPException(status_code=404, detail="Export not found")
    path = ExportStorage(settings).absolute(record.relative_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="This export has been purged under retention.")
    from .. import audit

    audit.record(
        session,
        action="export.download",
        entity_type="export",
        entity_id=record.id,
        case_id=case_id,
        actor=user.username,
        detail={"filename": record.filename},
    )
    return FileResponse(path, filename=record.filename)


@app.post("/cases/{case_id}/close")
def close(
    case_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    pipeline.close_case(session, case, actor=user.username)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


# -------------------------------------------------------------------- audit


@app.get("/audit", response_class=HTMLResponse)
def audit_log(
    request: Request,
    case_id: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    query = select(AuditEvent).order_by(AuditEvent.at.desc()).limit(500)
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
    events = list(session.scalars(query))
    return render(request, "audit.html", events=events, user=user, case_id=case_id)


@app.get("/health")
def health():
    from ..ocr.text import TextPipeline

    return {
        "status": "ok",
        "database": settings.database_url.split("://", 1)[0],
        "ocr_engines": TextPipeline().available_engines(),
        "today": date.today().isoformat(),
        "transaction_types": [t.value for t in TransactionType],
    }
