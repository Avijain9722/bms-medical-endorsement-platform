"""Internal browser application.

Server-rendered, no client framework, no state in the browser. Every case, file
and correction is written to the database and the filesystem as it happens, so
work survives a refresh, a logout, a restart and the end of the day.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, pipeline
from ..config import settings
from ..db import create_all, get_session, init_engine
from .. import logbook, master, scheduler
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
from ..outputs.bindings import for_template as binding_for
from ..outputs.mapping import UnsupportedValue
from ..registry.generate import StructuralDrift, UnknownField
from ..outputs.nas import SUPPORTED_KEYS
from ..storage import ExportStorage
from .security import (
    COOKIE_NAME,
    CSRF_FIELD,
    SESSION_MAX_AGE_SECONDS,
    csrf_token,
    csrf_valid,
    hash_password,
    issue_session,
    locked_out,
    read_session,
    record_failure,
    record_success,
    verify_dummy,
    verify_password,
)

logger = logging.getLogger("bms.web")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    init_engine()
    create_all()
    _ensure_seed_user()
    purge_task = scheduler.start(settings)
    try:
        yield
    finally:
        if purge_task is not None:
            purge_task.cancel()


# Only /login is exempt, and only because no session exists yet to derive a token
# from. Everything else that changes state -- including /logout, so an attacker
# cannot sign someone out -- is checked.
CSRF_EXEMPT_PATHS = frozenset({"/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_REJECTED = (
    "This form was not submitted from a current session, so nothing was changed. "
    "If you were signed out, sign in again and retry. If you were not, tell your "
    "administrator: something else tried to act as you."
)


async def _csrf_guard(request: Request) -> None:
    """Reject a state-changing request that does not carry this session's token.

    A dependency rather than middleware, deliberately. Middleware would have to
    call `request.form()` to read the token, and that consumes the request body
    before the route handler can parse it -- every form POST would fail with a
    422. FastAPI resolves dependencies against the *same* Request the handler
    uses, and Request.form() caches, so reading it here is free.

    Registered globally on the application, so a route added later is protected
    by default rather than by remembering to opt in.
    """
    if request.method not in UNSAFE_METHODS or request.url.path in CSRF_EXEMPT_PATHS:
        return
    form = await request.form()
    submitted = form.get(CSRF_FIELD)
    if not csrf_valid(
        request.cookies.get(COOKIE_NAME), submitted if isinstance(submitted, str) else None
    ):
        logger.warning("CSRF check failed on %s %s", request.method, request.url.path)
        raise HTTPException(status_code=403, detail=CSRF_REJECTED)


app = FastAPI(
    title="BMS Medical Endorsement Platform",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    # Applied to every route, so a new one is protected by default.
    dependencies=[Depends(_csrf_guard)],
)


# The stylesheet and the one script. Served as files so they are fetched once
# and revalidated with a 304, rather than re-sent inside every page, and so the
# CSP above can refuse inline script outright.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Browser-side defences, on every response.

    The platform serves member identity data, so a page of it must not be
    frameable by another site, must not be sniffed into a different content type,
    and must not leak its URL -- which contains case and member ids -- in a
    Referer header. The CSP also enforces at the browser what the code already
    guarantees: nothing loads from anywhere but this host.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        # script-src carries no 'unsafe-inline'. With it, any markup that reached
        # a page could execute -- which is what made the escaping defect in the
        # error handler a live script injection rather than a display bug. The
        # platform's only script is served from /static, so nothing needs it.
        # style-src keeps it: the templates use style attributes for layout, and
        # a style attribute cannot run script.
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; object-src 'none'",
    )
    if settings.cookie_secure:
        # Only meaningful over TLS, and actively harmful to send otherwise.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


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
    if user is None or not user.active:
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
    # `detail` is not a fixed string: several of them quote back what the user
    # sent -- a username, a workflow event name, an uploaded filename. Interpolated
    # raw, that is a reflected script injection, and the page's own CSP allows
    # inline script, so it would run. Everything the templates render is escaped
    # by Jinja; this response is built by hand, so it has to escape here.
    return HTMLResponse(
        f"<h1>{exc.status_code}</h1><p>{escape(str(exc.detail))}</p>",
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def _unexpected_handler(request: Request, exc: Exception):
    """Anything not anticipated.

    The traceback goes to the server log, where an administrator can read it; the
    browser gets a reference to quote and nothing else. A stack trace rendered
    into the page would disclose file paths, library versions and query
    fragments to whoever triggered it.
    """
    import uuid

    reference = uuid.uuid4().hex[:12]
    logger.exception("unhandled error %s on %s %s", reference, request.method, request.url.path)
    return HTMLResponse(
        "<h1>Something went wrong</h1>"
        "<p>The error has been recorded. Nothing was submitted to an insurer.</p>"
        f"<p>Quote reference <code>{reference}</code> to your administrator, "
        "who can find it in the server log.</p>"
        '<p><a href="/">Back to cases</a></p>',
        status_code=500,
    )


# Every way a generated file can be refused. All four mean the same thing to the
# operator -- this must not be sent to the insurer -- so all four are shown on
# the case screen rather than reaching the browser as an unexplained 500.
EXPORT_REFUSALS = (
    pipeline.ExportBlocked,
    StructuralDrift,
    UnknownField,
    UnsupportedValue,
)


def _explain_refusal(error: Exception) -> str:
    """Say what the refusal means and what to do about it.

    The exception messages are precise but assume the reader knows the internals;
    the Medical Team needs to know whose problem it is.
    """
    if isinstance(error, StructuralDrift):
        return (
            "Export blocked: the generated workbook no longer matches the master "
            "template supplied by the insurer, so it must not be uploaded. This is "
            "not a problem with the member data. Report it to your administrator, "
            f"quoting: {error}"
        )
    if isinstance(error, UnsupportedValue):
        return (
            "Export blocked: this insurer's own dropdown cannot express one of the "
            "values on this case, and the platform will not substitute a nearest "
            f"match. {error}"
        )
    if isinstance(error, UnknownField):
        return (
            "Export blocked: this template is configured to write a field that does "
            f"not exist in it. Report it to your administrator, quoting: {error}"
        )
    return str(error)


def render(request: Request, name: str, **context) -> HTMLResponse:
    # Supplied to every template so no page has to remember to ask for it.
    context.setdefault("csrf_field", CSRF_FIELD)
    context.setdefault("csrf_token", csrf_token(request.cookies.get(COOKIE_NAME)))
    return templates.TemplateResponse(request, name, context)


# How many rows any list screen puts on one page. The operational log grows
# without bound, so every list is paged rather than rendered whole: a page the
# browser has to lay out in full gets slower every month until it is unusable.
PAGE_SIZE = 50


@dataclass(frozen=True)
class Paging:
    """Bounds for one page of a list, and the links either side of it."""

    total: int
    page: int
    size: int = PAGE_SIZE

    def __post_init__(self) -> None:
        # A hand-typed ?page=0 or ?page=-5 must not become a negative OFFSET.
        object.__setattr__(self, "page", max(1, self.page))

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))

    @property
    def current(self) -> int:
        return min(self.page, self.pages)

    @property
    def offset(self) -> int:
        return (self.current - 1) * self.size

    @property
    def first_row(self) -> int:
        return 0 if self.total == 0 else self.offset + 1

    @property
    def last_row(self) -> int:
        return min(self.offset + self.size, self.total)

    @property
    def has_previous(self) -> bool:
        return self.current > 1

    @property
    def has_next(self) -> bool:
        return self.current < self.pages


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


def _case_context(session: Session, case: Case, user: User, **extra) -> dict:
    """Everything case_detail.html needs.

    Built in one place because two routes render that page: the case screen, and
    a refused export re-rendering it with the reason. They had assembled the same
    six queries separately and had already drifted -- the export path listed
    exports oldest-first, so the file a user had just tried to produce moved to
    the bottom of the list exactly when they were looking for it.
    """
    flags = _flags_for(session, case)
    return {
        "case": case,
        "members": list(
            session.scalars(
                select(Member).where(Member.case_id == case.id).order_by(Member.row_index)
            )
        ),
        "files": list(session.scalars(select(CaseFile).where(CaseFile.case_id == case.id))),
        "exports": list(
            session.scalars(
                select(Export)
                .where(Export.case_id == case.id)
                .order_by(Export.created_at.desc())
            )
        ),
        "flags": flags,
        "case_flags": flags.get(None, []),
        "blocking": pipeline.blocking_flags(session, case),
        "user": user,
        "templates_available": SUPPORTED_KEYS,
        "severity": Severity,
        **extra,
    }


def _member_context(session: Session, case: Case, member: Member, user: User, **extra) -> dict:
    """Everything member.html needs, for the two routes that render it."""
    return {
        "case": case,
        "member": member,
        "documents": list(
            session.scalars(select(CaseFile).where(CaseFile.member_id == member.id))
        ),
        "flags": list(session.scalars(select(ReviewFlag).where(ReviewFlag.member_id == member.id))),
        "user": user,
        "provenance": member.provenance or {},
        "document_types": [t.value for t in DocumentType],
        **extra,
    }


def _load_member(session: Session, case: Case, member_id: str) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.case_id != case.id:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


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
    # Throttle on the username and on the caller's address: the first stops one
    # account being ground down, the second stops one client working through many
    # accounts. Checked BEFORE hashing, so a locked-out caller cannot keep
    # spending 240,000 PBKDF2 rounds of server CPU per attempt.
    client_key = f"ip:{request.client.host if request.client else 'unknown'}"
    user_key = f"user:{username.strip().lower()}"
    for key in (user_key, client_key):
        remaining = locked_out(key)
        if remaining:
            logger.warning("login refused, throttled: %s", key)
            return render(
                request,
                "login.html",
                error=(
                    "Too many failed sign-in attempts. Try again in "
                    f"{max(1, remaining // 60)} minute(s), or ask an administrator "
                    "to reset your password."
                ),
            )

    # Accounts are created with the username stripped, so a stray trailing space
    # typed into the form used to fail against an account that does exist.
    user = session.scalar(select(User).where(User.username == username.strip()))
    if user is None:
        # Spend the work anyway. Returning early here answered "no such user"
        # faster than "wrong password", which times the account list out loud.
        verify_dummy(password)
    if user is None or not verify_password(password, user.password_hash) or not user.active:
        # A disabled account fails identically to a wrong password, so the form
        # cannot be used to discover which accounts exist or are still enabled.
        for key in (user_key, client_key):
            record_failure(key)
        return render(request, "login.html", error="Incorrect username or password.")

    for key in (user_key, client_key):
        record_success(key)
    audit.record(
        session,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor=user.username,
    )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        issue_session(user.id),
        httponly=True,
        samesite="lax",
        # Set BMS_COOKIE_SECURE=true behind TLS. This used to be hard-coded False,
        # so enabling it meant editing source on the production host.
        secure=settings.cookie_secure,
        max_age=SESSION_MAX_AGE_SECONDS,
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
    page: int = 1,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    total = int(session.scalar(select(func.count()).select_from(Case)) or 0)
    paging = Paging(total=total, page=page, size=PAGE_SIZE)
    cases = list(
        session.scalars(
            select(Case)
            .order_by(Case.created_at.desc())
            .offset(paging.offset)
            .limit(paging.size)
        )
    )
    return render(request, "cases.html", cases=cases, user=user, paging=paging)


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
        clients=_client_index(session),
    )


@app.get("/clients/{client_id}/options")
def client_options(
    client_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """The sub-groups, entities and policies of one company.

    Signed-in only, like every other route: the client master is BMS commercial
    data, not public reference.
    """
    client = session.get(Client, client_id)
    if client is None or not client.active:
        raise HTTPException(status_code=404, detail="Client not found")
    return _client_options(client)


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
    if transaction_type not in {item.value for item in TransactionType}:
        raise HTTPException(status_code=400, detail="Choose a valid transaction type.")

    # Values chosen from the client master win over anything typed, so a portal
    # workbook receives a registered literal rather than a user's spelling.
    client = session.get(Client, client_id) if client_id else None
    chosen_sub_group = session.get(SubGroup, sub_group_id) if sub_group_id else None
    entity = session.get(LegalEntity, legal_entity_id) if legal_entity_id else None
    policy = session.get(ClientPolicy, client_policy_id) if client_policy_id else None

    supplied_records = (
        (client_id, client, "client"),
        (sub_group_id, chosen_sub_group, "sub-group"),
        (legal_entity_id, entity, "legal entity"),
        (client_policy_id, policy, "policy"),
    )
    for supplied_id, record, label in supplied_records:
        if supplied_id and (record is None or not record.active):
            raise HTTPException(status_code=400, detail=f"The selected {label} is not available.")

    children = (
        (chosen_sub_group, "sub-group"),
        (entity, "legal entity"),
        (policy, "policy"),
    )
    for record, label in children:
        if record is not None and (client is None or record.client_id != client.id):
            raise HTTPException(
                status_code=400,
                detail=f"The selected {label} does not belong to the selected client.",
            )

    if policy and policy.legal_entity_id:
        if entity and entity.id != policy.legal_entity_id:
            raise HTTPException(
                status_code=400,
                detail="The selected legal entity does not belong to the selected policy.",
            )
        if entity is None:
            entity = session.get(LegalEntity, policy.legal_entity_id)
            if entity is None or not entity.active or entity.client_id != client.id:
                raise HTTPException(status_code=400, detail="The selected policy is not valid.")

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

    if template_key:
        if (
            template_key not in SUPPORTED_KEYS
            or binding_for(template_key).transaction != transaction_type
        ):
            raise HTTPException(
                status_code=400,
                detail="The selected export template does not match the transaction type.",
            )

    if not client_name.strip():
        raise HTTPException(status_code=400, detail="A client is required.")
    if not insurer.strip():
        raise HTTPException(status_code=400, detail="An insurer is required.")

    received = None
    if email_received_date:
        try:
            received = datetime.fromisoformat(email_received_date)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="Choose a valid email received date."
            ) from error

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
    return render(request, "case_detail.html", **_case_context(session, case, user))


@app.post("/cases/{case_id}/comments")
def update_comments(
    case_id: str,
    bms_comments: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
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
        data = await _read_bounded(upload_file, settings.max_upload_bytes)
        if data is None:
            # Over the limit. add_upload records the same refusal for a file that
            # arrives whole, so the operator sees one consistent message.
            pipeline.record_oversize_upload(
                session, case, upload_file.filename or "upload", actor=user.username
            )
            continue
        if not data:
            continue
        pipeline.add_upload(
            session, case, upload_file.filename or "upload", data, actor=user.username
        )
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


async def _read_bounded(upload_file: UploadFile, limit: int) -> bytes | None:
    """Read at most `limit` bytes, or give up.

    `await upload_file.read()` pulls the whole body into memory and the size was
    only checked afterwards, so the limit could not prevent what it existed to
    prevent: a 10 GB upload was fully resident before anything rejected it. This
    stops at the first chunk that crosses the limit, so peak memory is bounded by
    the limit itself no matter what is sent.

    Returns None when the file is too large.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload_file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/cases/{case_id}/process")
def process(
    case_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    case = _load_case(session, case_id)
    try:
        case.status = CaseStatus.PROCESSING.value
        pipeline.analyse_files(session, case, actor=user.username)
        pipeline.build_members(session, case, actor=user.username)
    except pipeline.ProcessingBlocked as error:
        session.rollback()
        case = _load_case(session, case_id)
        return render(
            request,
            "case_detail.html",
            **_case_context(session, case, user, error=str(error)),
        )
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
    member = _load_member(session, case, member_id)
    return render(request, "member.html", **_member_context(session, case, member, user))


EDITABLE_FIELDS = (
    "first_name", "middle_name", "last_name", "date_of_birth", "gender",
    "marital_status", "nationality", "passport_no", "passport_expiry",
    "emirates_id", "unified_no", "visa_file_number", "birth_certificate_number",
    "staff_id", "relation", "category", "contract_name", "effective_date",
    "member_card_no", "deletion_reason", "cancellation_date", "principal_card_no",
    "email", "mobile_no",
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
    member = _load_member(session, case, member_id)

    form = await request.form()
    for field_key in EDITABLE_FIELDS:
        if field_key in form:
            pipeline.update_member_field(
                session, member, field_key, str(form[field_key]), actor=user.username
            )
    pipeline.apply_dubai_deletion_date_rule(
        session, case, member, actor=user.username
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
    member = _load_member(session, case, member_id)
    try:
        pipeline.approve_member(
            session, case, member, actor=user.username, approver_id=user.id
        )
    except PermissionError as error:
        return render(
            request,
            "member.html",
            **_member_context(session, case, member, user, error=str(error)),
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
    record = session.get(CaseFile, file_id)
    if record is None or record.case_id != case_id:
        raise HTTPException(status_code=404, detail="File not found")
    if document_type not in {item.value for item in DocumentType}:
        raise HTTPException(status_code=400, detail="Choose a valid document type.")
    if member_id:
        member = session.get(Member, member_id)
        if member is None or member.case_id != case_id:
            raise HTTPException(status_code=400, detail="The selected member is not in this case.")

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
        audit.record_field_change(
            session,
            entity_type="case_file",
            entity_id=record.id,
            case_id=case_id,
            field_key="member_id",
            old_value=record.member_id,
            new_value=member_id,
            actor=user.username,
        )
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
    except EXPORT_REFUSALS as error:
        # Four different refusals, all of which mean "this file must not be sent
        # to the insurer", and all of which the operator has to be able to act
        # on. Only ExportBlocked was handled before; the rest reached the browser
        # as an unexplained 500 -- worst of all StructuralDrift, which is the
        # check that stops a structurally damaged workbook being uploaded.
        #
        # Roll back before rendering. An export writes the portal workbook's row
        # before it builds the log, so a refusal raised by the log step left the
        # portal Export recorded against a case whose export never completed --
        # the session commits on the way out of the request either way. A refused
        # export must leave no record that it half happened.
        session.rollback()
        return render(
            request,
            "case_detail.html",
            **_case_context(session, case, user, error=_explain_refusal(error)),
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
    page: int = 1,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    counter = select(func.count()).select_from(AuditEvent)
    query = select(AuditEvent).order_by(AuditEvent.at.desc())
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
        counter = counter.where(AuditEvent.case_id == case_id)

    # The audit trail is append-only and never purged, so it only ever grows.
    paging = Paging(total=int(session.scalar(counter) or 0), page=page, size=PAGE_SIZE)
    events = list(session.scalars(query.offset(paging.offset).limit(paging.size)))
    return render(
        request, "audit.html", events=events, user=user, case_id=case_id, paging=paging
    )


@app.get("/health")
def health():
    """What this host can actually do.

    Deliberately explicit about missing capabilities: a deployment without OCR,
    without a virus scanner or without Excel still works, but the gaps must be
    visible rather than discovered later.
    """
    from ..intake import scanning
    from ..ocr.text import TextPipeline
    from ..outputs import recalc

    return {
        "status": "ok",
        "database": settings.database_url.split("://", 1)[0],
        "ocr_engines": TextPipeline().available_engines(),
        "templates": list(SUPPORTED_KEYS),
        "today": date.today().isoformat(),
        "transaction_types": [t.value for t in TransactionType],
        "retention_hours": settings.document_retention_hours,
        "purge_interval_minutes": settings.purge_interval_minutes if settings.purge_enabled else 0,
        **scanning.describe_host(),
        **recalc.describe_host(),
    }


# ------------------------------------------------------------ administration


def _client_options(client) -> dict:
    """One company's sub-groups, entities and policies, for the case form.

    Fetched for the selected company only. The whole master used to be embedded
    in the page -- 707 KB of JSON at 500 companies, on every visit, nearly all of
    it for companies the operator was not choosing.
    """
    return (
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


def _client_index(session: Session) -> list[dict]:
    """Just enough to populate the company dropdown: id, name, code."""
    return [
        {"id": c.id, "name": c.name, "code": c.code}
        for c in master.active_clients(session)
    ]


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
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="A username is required.")
    if session.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=400, detail=f"User {username!r} already exists.")
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Choose a password of at least 10 characters.")

    user = User(
        username=username,
        display_name=display_name.strip() or username,
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


@app.post("/admin/users/{user_id}/active")
def admin_set_user_active(
    user_id: str,
    active: str = Form(...),
    session: Session = Depends(get_session),
    admin: User = Depends(current_admin),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    enabled = active == "1"
    if active not in {"0", "1"}:
        raise HTTPException(status_code=400, detail="Choose a valid account status.")
    if user.id == admin.id and not enabled:
        raise HTTPException(status_code=400, detail="You cannot disable your own account.")
    if user.active != enabled:
        user.active = enabled
        audit.record(
            session,
            action="user.set_active",
            entity_type="user",
            entity_id=user.id,
            actor=admin.username,
            old_value=not enabled,
            new_value=enabled,
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
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A company name is required.")
    client, created = master.get_or_create_client(session, name, code=code.strip() or None)
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
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="A sub-group name is required.")
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
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="A legal-entity name is required.")
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
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if not insurer.strip():
        raise HTTPException(status_code=400, detail="An insurer is required.")
    entity = session.get(LegalEntity, legal_entity_id) if legal_entity_id else None
    if legal_entity_id and (entity is None or not entity.active):
        raise HTTPException(status_code=400, detail="The selected legal entity is not available.")
    if entity is not None and entity.client_id != client.id:
        raise HTTPException(
            status_code=400,
            detail="The selected legal entity does not belong to this client.",
        )
    for template_key, transaction_type in (
        (addition_template_key, TransactionType.ADDITION.value),
        (deletion_template_key, TransactionType.DELETION.value),
    ):
        if template_key and (
            template_key not in SUPPORTED_KEYS
            or binding_for(template_key).transaction != transaction_type
        ):
            raise HTTPException(
                status_code=400,
                detail="Choose an export template that matches the policy transaction.",
            )
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
    # Bounded like every other upload. This route read the whole body into memory
    # first and checked nothing, so the limit that protects /cases/{id}/upload
    # did not apply here at all.
    data = await _read_bounded(workbook, settings.max_upload_bytes)
    if data is None:
        raise HTTPException(status_code=400, detail="That workbook exceeds the maximum upload size.")
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
    page: int = 1,
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
    # One page at a time. The log grows without bound -- it is the permanent
    # operational record -- so rendering every matching row would make the screen
    # heavier every month until it stopped being usable.
    total = logbook.count(session, criteria)
    paging = Paging(total=total, page=page, size=PAGE_SIZE)
    entries = logbook.query(session, criteria, limit=paging.size, offset=paging.offset)
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
        paging=paging,
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
    return FileResponse(
        path,
        filename=download.filename,
        background=BackgroundTask(_remove_transient_download, path),
    )


def _remove_transient_download(path: Path) -> None:
    """Delete an on-demand log workbook once the response has sent it."""
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


@app.get("/log/{entry_id}", response_class=HTMLResponse)
def log_entry_screen(
    entry_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """One log row, with the workflow actions that write its blank columns.

    These forms used to be rendered inside every row of the list. Five forms per
    row meant the list carried thousands of controls the browser had to lay out
    whether or not anyone touched them; on demand, it carries none.
    """
    entry = session.get(LogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return render(
        request,
        "log_entry.html",
        user=user,
        entry=entry,
        case=session.get(Case, entry.case_id),
        statuses=logbook.LOG_STATUSES,
        events=log_output.RECORDABLE_EVENTS,
    )


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
    return RedirectResponse("/log", status_code=303)


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
    return RedirectResponse("/log", status_code=303)


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
