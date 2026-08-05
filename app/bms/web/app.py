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
from .. import logbook, master
from ..models import (
    AuditEvent,
    Case,
    CaseFile,
    CaseStatus,
    Client,
    ClientPolicy,
    DocumentType,
    Export,
    LegalEntity,
    LogEntry,
    Member,
    ReviewFlag,
    Severity,
    SubGroup,
    TransactionType,
    User,
)
from ..outputs import log as log_output
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
                is_admin=True,
            )
        )
        print(
            f"[bms] created initial administrator 'bms' with password: {password}\n"
            "[bms] change it at /admin, or with: python3 -m bms.cli reset-password --username bms",
            flush=True,
        )


# ------------------------------------------------------------------- helpers


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    user_id = read_session(request.cookies.get(COOKIE_NAME))
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def current_admin(user: User = Depends(current_user)) -> User:
    """Administration is gated; case processing is not.

    BMS asked for a single operational role, so this guards only account and
    client-master maintenance. It confers no processing privilege and no ability
    to override a critical error.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
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
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    return render(
        request,
        "case_new.html",
        user=user,
        templates_available=SUPPORTED_KEYS,
        clients=_master_snapshot(session),
    )


@app.post("/cases")
def create_case(
    client_name: str = Form(""),
    insurer: str = Form(""),
    transaction_type: str = Form(...),
    bms_comments: str = Form(""),
    sub_group: str = Form(""),
    policy_no: str = Form(""),
    contract_name: str = Form(""),
    category: str = Form(""),
    email_subject: str = Form(""),
    email_received_date: str = Form(""),
    template_key: str = Form(""),
    client_id: str = Form(""),
    sub_group_id: str = Form(""),
    legal_entity_id: str = Form(""),
    client_policy_id: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    # Values chosen from the client master win over anything typed, so a portal
    # workbook receives a registered literal rather than a user's spelling.
    client = session.get(Client, client_id) if client_id else None
    chosen_sub_group = session.get(SubGroup, sub_group_id) if sub_group_id else None
    entity = session.get(LegalEntity, legal_entity_id) if legal_entity_id else None
    policy = session.get(ClientPolicy, client_policy_id) if client_policy_id else None

    if client:
        client_name = client.name
    if chosen_sub_group:
        sub_group = chosen_sub_group.name
    if entity:
        contract_name = entity.contract_name or entity.name
    if policy:
        insurer = policy.insurer
        policy_no = policy.policy_no or policy_no
        category = policy.category or category
        if not template_key:
            template_key = (
                policy.deletion_template_key
                if transaction_type == TransactionType.DELETION.value
                else policy.addition_template_key
            ) or ""

    if not client_name.strip():
        raise HTTPException(status_code=400, detail="A client is required.")
    if not insurer.strip():
        raise HTTPException(status_code=400, detail="An insurer is required.")

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
        client_id=client.id if client else None,
        sub_group_id=chosen_sub_group.id if chosen_sub_group else None,
        legal_entity_id=entity.id if entity else None,
        client_policy_id=policy.id if policy else None,
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


# ------------------------------------------------------------ administration


def _master_snapshot(session: Session) -> list[dict]:
    """The client master, shaped for the case form's dependent dropdowns."""
    snapshot = []
    for client in master.active_clients(session):
        snapshot.append(
            {
                "id": client.id,
                "name": client.name,
                "code": client.code,
                "sub_groups": [
                    {"id": s.id, "name": s.name, "emirate": s.emirate}
                    for s in sorted(client.sub_groups, key=lambda s: s.name)
                    if s.active
                ],
                "entities": [
                    {"id": e.id, "name": e.name, "contract_name": e.contract_name}
                    for e in sorted(client.legal_entities, key=lambda e: e.name)
                    if e.active
                ],
                "policies": [
                    {
                        "id": p.id,
                        "insurer": p.insurer,
                        "network": p.network,
                        "policy_no": p.policy_no,
                        "category": p.category,
                        "emirate": p.emirate,
                        "legal_entity_id": p.legal_entity_id,
                        "addition_template_key": p.addition_template_key,
                        "deletion_template_key": p.deletion_template_key,
                    }
                    for p in client.policies
                    if p.active
                ],
            }
        )
    return snapshot


@app.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
    imported: str | None = None,
):
    users = list(session.scalars(select(User).order_by(User.username)))
    clients = list(session.scalars(select(Client).order_by(Client.name)))
    return render(
        request,
        "admin.html",
        user=admin,
        users=users,
        clients=clients,
        templates_available=SUPPORTED_KEYS,
        imported=imported,
    )


@app.post("/admin/users")
def admin_create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    log_associate: str = Form(""),
    password: str = Form(...),
    is_admin: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    if session.scalar(select(User).where(User.username == username.strip())):
        raise HTTPException(status_code=400, detail=f"User {username!r} already exists.")
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Choose a password of at least 10 characters.")

    user = User(
        username=username.strip(),
        display_name=display_name.strip() or username.strip(),
        log_associate=log_associate.strip() or None,
        password_hash=hash_password(password),
        is_admin=bool(is_admin),
    )
    session.add(user)
    session.flush()
    # The password itself is never written to the audit trail.
    audit.record(
        session,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        actor=admin.username,
        detail={"username": user.username, "is_admin": user.is_admin},
    )
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/password")
def admin_reset_password(
    user_id: str,
    password: str = Form(...),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Choose a password of at least 10 characters.")
    user.password_hash = hash_password(password)
    audit.record(
        session,
        action="user.reset_password",
        entity_type="user",
        entity_id=user.id,
        actor=admin.username,
        detail={"username": user.username},
    )
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/clients")
def admin_create_client(
    name: str = Form(...),
    code: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    client, created = master.get_or_create_client(session, name.strip(), code=code.strip() or None)
    if created:
        audit.record(
            session,
            action="client.create",
            entity_type="client",
            entity_id=client.id,
            actor=admin.username,
            detail={"name": client.name},
        )
    return RedirectResponse(f"/admin/clients/{client.id}", status_code=303)


@app.get("/admin/clients/{client_id}", response_class=HTMLResponse)
def admin_client_detail(
    client_id: str,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return render(
        request,
        "admin_client.html",
        user=admin,
        client=client,
        templates_available=SUPPORTED_KEYS,
        emirates=("Abu Dhabi", "Dubai", "Northern Emirates"),
    )


@app.post("/admin/clients/{client_id}/sub-groups")
def admin_add_sub_group(
    client_id: str,
    name: str = Form(...),
    emirate: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    sub_group, created = master.get_or_create_sub_group(
        session, client, name.strip(), emirate=emirate.strip() or None
    )
    if created:
        audit.record(
            session,
            action="sub_group.create",
            entity_type="sub_group",
            entity_id=sub_group.id,
            actor=admin.username,
            detail={"client": client.name, "name": sub_group.name, "emirate": sub_group.emirate},
        )
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@app.post("/admin/clients/{client_id}/entities")
def admin_add_entity(
    client_id: str,
    name: str = Form(...),
    contract_name: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    entity, created = master.get_or_create_entity(
        session, client, name.strip(), contract_name=contract_name.strip() or None
    )
    if created:
        audit.record(
            session,
            action="legal_entity.create",
            entity_type="legal_entity",
            entity_id=entity.id,
            actor=admin.username,
            detail={"client": client.name, "name": entity.name},
        )
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@app.post("/admin/clients/{client_id}/policies")
def admin_add_policy(
    client_id: str,
    insurer: str = Form(...),
    network: str = Form(""),
    policy_no: str = Form(""),
    category: str = Form(""),
    emirate: str = Form(""),
    legal_entity_id: str = Form(""),
    addition_template_key: str = Form(""),
    deletion_template_key: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    from .. import audit

    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    entity = session.get(LegalEntity, legal_entity_id) if legal_entity_id else None
    policy, created = master.get_or_create_policy(
        session,
        client,
        insurer=insurer.strip(),
        entity=entity,
        network=network.strip() or None,
        policy_no=policy_no.strip() or None,
        category=category.strip() or None,
        emirate=emirate.strip() or None,
        addition_template_key=addition_template_key or None,
        deletion_template_key=deletion_template_key or None,
    )
    if created:
        audit.record(
            session,
            action="client_policy.create",
            entity_type="client_policy",
            entity_id=policy.id,
            actor=admin.username,
            detail={"client": client.name, "insurer": policy.insurer, "policy_no": policy.policy_no},
        )
    return RedirectResponse(f"/admin/clients/{client_id}", status_code=303)


@app.post("/admin/clients/import")
async def admin_import_clients(
    workbook: UploadFile,
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    data = await workbook.read()
    if not data:
        raise HTTPException(status_code=400, detail="No file was uploaded.")
    try:
        summary = master.import_workbook(session, data, actor=admin.username)
    except Exception as error:  # noqa: BLE001 - reported to the user verbatim
        raise HTTPException(status_code=400, detail=f"Could not read the workbook: {error}") from error
    return RedirectResponse(
        f"/admin?imported={summary.rows_read} rows, {summary.total_created} record(s) created",
        status_code=303,
    )


# ---------------------------------------------------------- operational log


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@app.get("/log", response_class=HTMLResponse)
def log_screen(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    client_name: str | None = None,
    insurer: str | None = None,
    status: str | None = None,
    entry_type: str | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    criteria = logbook.LogFilter(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        client_name=client_name or None,
        insurer=insurer or None,
        status=status or None,
        entry_type=entry_type or None,
    )
    entries = logbook.query(session, criteria, limit=1000)
    cases = {
        case.id: case
        for case in session.scalars(select(Case).where(Case.id.in_({e.case_id for e in entries})))
    } if entries else {}

    return render(
        request,
        "log.html",
        user=user,
        entries=entries,
        cases=cases,
        criteria=criteria,
        filters={
            "date_from": date_from or "",
            "date_to": date_to or "",
            "client_name": client_name or "",
            "insurer": insurer or "",
            "status": status or "",
            "entry_type": entry_type or "",
        },
        clients=logbook.distinct_values(session, LogEntry.client_name),
        insurers=logbook.distinct_values(session, LogEntry.insurer),
        entry_types=logbook.distinct_values(session, LogEntry.entry_type),
        statuses=logbook.LOG_STATUSES,
        events=log_output.RECORDABLE_EVENTS,
    )


@app.post("/log/export")
def log_export(
    date_from: str = Form(""),
    date_to: str = Form(""),
    client_name: str = Form(""),
    insurer: str = Form(""),
    status: str = Form(""),
    entry_type: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    criteria = logbook.LogFilter(
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        client_name=client_name or None,
        insurer=insurer or None,
        status=status or None,
        entry_type=entry_type or None,
    )
    download = logbook.export_range(session, criteria, actor=user.username, config=settings)
    path = ExportStorage(settings).absolute(download.relative_path)
    return FileResponse(path, filename=download.filename)


@app.post("/log/{entry_id}/event")
def log_record_event(
    entry_id: str,
    event: str = Form(...),
    request_ref_no: str = Form(""),
    request_sent_date_to_insurer: str = Form(""),
    card_no: str = Form(""),
    card_receive_and_sent_date: str = Form(""),
    saiba_voucher_no: str = Form(""),
    bbm_invoice_date: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Record a post-submission event against a log row.

    The six columns start blank and stay blank until one of these actions is
    taken, which may be days after the case was exported. Only the fields the
    event owns are passed through; the rest are ignored.
    """
    entry = session.get(LogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")

    supplied = {
        "request_ref_no": request_ref_no,
        "request_sent_date_to_insurer": request_sent_date_to_insurer,
        "card_no": card_no,
        "card_receive_and_sent_date": card_receive_and_sent_date,
        "saiba_voucher_no": saiba_voucher_no,
        "bbm_invoice_date": bbm_invoice_date,
    }
    allowed = log_output.RECORDABLE_EVENTS.get(event)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"{event!r} is not a recognised event")

    values = {key: value for key, value in supplied.items() if key in allowed and value.strip()}
    if not values:
        raise HTTPException(status_code=400, detail="Nothing was entered for this event.")

    try:
        logbook.apply_event(session, entry, event, values, actor=user.username)
    except log_output.NotRecordable as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return RedirectResponse(request_referer_or("/log"), status_code=303)


@app.post("/log/{entry_id}/fields")
def log_update_fields(
    entry_id: str,
    status: str = Form(""),
    remarks: str = Form(""),
    remarks2: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Edit the operator-owned columns: Status, REMARKS and Remarks2."""
    entry = session.get(LogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    try:
        logbook.update_fields(
            session,
            entry,
            {"status": status, "remarks": remarks, "remarks2": remarks2},
            actor=user.username,
        )
    except logbook.NotPermitted as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return RedirectResponse(request_referer_or("/log"), status_code=303)


@app.post("/cases/{case_id}/reopen")
def reopen(
    case_id: str,
    reason: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """Reopen a closed case so later information can still be recorded."""
    case = _load_case(session, case_id)
    logbook.reopen_case(session, case, actor=user.username, reason=reason or None)
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


def request_referer_or(default: str) -> str:
    return default
