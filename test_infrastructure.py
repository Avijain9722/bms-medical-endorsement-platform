"""Virus scanning, Excel recalculation, the purge scheduler and migrations.

All four degrade rather than fail when the host lacks the capability, so most of
what matters here is that the degradation is *reported* instead of being
mistaken for success.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bms import db, pipeline, scheduler  # noqa: E402
from bms.config import Settings  # noqa: E402
from bms.cli import _prompt_password  # noqa: E402
from bms.intake import scanning  # noqa: E402
from bms.models import Base, Case, CaseFile, Export, ReviewFlag, Severity, User  # noqa: E402
from bms.outputs import recalc  # noqa: E402
from bms.web.security import hash_password  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_new_settings_instances_read_the_current_environment(monkeypatch, tmp_path):
    database_url = f"sqlite:///{tmp_path / 'environment.db'}"
    data_root = tmp_path / "environment-data"
    monkeypatch.setenv("BMS_DATABASE_URL", database_url)
    monkeypatch.setenv("BMS_DATA_ROOT", str(data_root))
    monkeypatch.setenv("BMS_MAX_ARCHIVE_MEMBERS", "37")
    monkeypatch.setenv("BMS_COOKIE_SECURE", "true")

    configured = Settings()

    assert configured.database_url == database_url
    assert configured.data_root == data_root.resolve()
    assert configured.max_archive_members == 37
    assert configured.cookie_secure is True


def test_supplied_cli_password_obeys_the_interactive_minimum():
    with pytest.raises(SystemExit, match="at least 10"):
        _prompt_password("short")


def test_valid_supplied_cli_password_is_not_reported_as_generated():
    assert _prompt_password("long-enough-password") == ("long-enough-password", False)
APP_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'infra.db'}",
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
    record = User(username="t", display_name="T", password_hash=hash_password("x" * 12))
    session.add(record)
    session.flush()
    return record


# ----------------------------------------------------------- virus scanning


def test_scanner_absence_is_reported_as_unavailable_not_clean():
    """The distinction matters: unscanned must never look like scanned."""
    result = scanning.scan_bytes(b"harmless", filename="a.txt")
    if scanning.available():
        assert result.verdict in (scanning.CLEAN, scanning.INFECTED)
    else:
        assert result.verdict == scanning.UNAVAILABLE
        assert not result.blocks_upload
        assert result.needs_notice


def test_scan_result_flags_are_consistent():
    infected = scanning.ScanResult(scanning.INFECTED, "clamscan", "Eicar-Test-Signature")
    assert infected.blocks_upload and not infected.needs_notice

    clean = scanning.ScanResult(scanning.CLEAN, "clamscan")
    assert not clean.blocks_upload and not clean.needs_notice

    unknown = scanning.ScanResult(scanning.UNAVAILABLE, "none")
    assert not unknown.blocks_upload and unknown.needs_notice


def test_uploads_record_their_scan_verdict(session, user, env):
    case = pipeline.create_case(
        session, client_name="DEMO", insurer="DEMO", transaction_type="addition",
        actor=user.username, bms_comments="",
    )
    pipeline.add_upload(session, case, "note.txt", b"synthetic", actor=user.username)
    record = session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)).one()
    assert record.scan_verdict in (scanning.CLEAN, scanning.UNAVAILABLE, scanning.ERROR)


def test_an_infected_upload_never_reaches_storage(session, user, env, monkeypatch):
    monkeypatch.setattr(
        "bms.pipeline.scanning.scan_bytes",
        lambda data, filename="": scanning.ScanResult(
            scanning.INFECTED, "clamscan", "Eicar-Test-Signature"
        ),
    )
    case = pipeline.create_case(
        session, client_name="DEMO", insurer="DEMO", transaction_type="addition",
        actor=user.username, bms_comments="",
    )
    outcome = pipeline.add_upload(session, case, "bad.txt", b"payload", actor=user.username)

    assert outcome.stored == []
    assert outcome.infected and "Eicar" in outcome.infected[0][1]
    assert session.scalars(select(CaseFile).where(CaseFile.case_id == case.id)).all() == []

    from bms.storage import Storage
    import hashlib

    assert not Storage(env).exists(hashlib.sha256(b"payload").hexdigest())


def test_unscanned_files_raise_a_review_flag(session, user, env, monkeypatch):
    monkeypatch.setattr(
        "bms.pipeline.scanning.scan_bytes",
        lambda data, filename="": scanning.ScanResult(scanning.UNAVAILABLE, "none", ""),
    )
    case = pipeline.create_case(
        session, client_name="DEMO", insurer="DEMO", transaction_type="addition",
        actor=user.username, bms_comments="Add 90001 Demo Person Single CAT B",
    )
    pipeline.add_upload(session, case, "note.txt", b"synthetic", actor=user.username)
    pipeline.build_members(session, case, actor=user.username)

    flags = list(session.scalars(select(ReviewFlag).where(ReviewFlag.case_id == case.id)))
    unscanned = [f for f in flags if f.code == "not_virus_scanned"]
    assert unscanned
    # A warning, not a blocker: the file is usable, the gap is just visible.
    assert unscanned[0].severity == Severity.WARNING.value


# ------------------------------------------------------ Excel recalculation


def test_recalculation_reports_honestly_when_excel_is_absent(tmp_path):
    target = tmp_path / "log.xlsx"
    target.write_bytes(b"PK\x03\x04placeholder")
    result = recalc.recalculate(target, template_key="bms.log.2026")

    available, _ = recalc.excel_available()
    if available:  # pragma: no cover - only on a Windows host
        assert result.performed
    else:
        assert result.pending
        assert result.engine == "unavailable"
        assert "not evaluated" in result.detail


def test_templates_without_formulas_need_no_recalculation(tmp_path):
    target = tmp_path / "nas.xlsx"
    target.write_bytes(b"PK\x03\x04placeholder")
    result = recalc.recalculate(target, template_key="nas.addition.aldar.v1")
    assert result.performed and result.engine == "not_required"


def test_the_templates_needing_excel_are_the_ones_with_live_formulas():
    assert recalc.NEEDS_RECALC == {"bms.log.2026", "daman.addition.v1"}


def test_exports_record_whether_they_were_recalculated(session, user, env):
    from datetime import date

    case = pipeline.create_case(
        session, client_name="DEMO", insurer="DEMO", transaction_type="addition",
        actor=user.username, bms_comments="Add 90001 Demo Person Single CAT B",
        owner_id=user.id,
    )
    pipeline.build_members(session, case, actor=user.username)
    member = session.scalars(select(pipeline.Member).where(pipeline.Member.case_id == case.id)).one()
    member.first_name, member.last_name = "Demo", "Person"
    member.date_of_birth, member.gender = "1990-01-01", "Female"
    member.marital_status, member.nationality = "Single", "India"
    member.relation, member.category = "Principal", "CAT B"
    member.effective_date = date.today().isoformat()
    member.emirates_id = "784-1990-1234567-1"
    member.provenance = {}
    pipeline.revalidate_member(session, case, member)
    pipeline.approve_member(session, case, member, actor=user.username)
    pipeline.export_case(
        session, case, template_key="nas.addition.aldar.v1", actor=user.username, config=env
    )

    exports = {e.kind: e for e in session.scalars(select(Export).where(Export.case_id == case.id))}
    # NAS carries no live formulas, so it is complete as written.
    assert exports["portal"].recalculated is True
    # The log does, so on a non-Windows host it is flagged as pending.
    available, _ = recalc.excel_available()
    assert exports["log"].recalculated is available
    assert exports["log"].recalc_detail


# ------------------------------------------------------------- scheduler


def test_scheduler_is_disabled_when_the_interval_is_zero(env):
    disabled = Settings(
        database_url=env.database_url, data_root=env.data_root, purge_interval_minutes=0
    )
    assert scheduler.start(disabled) is None


def test_scheduler_is_disabled_when_purging_is_off(env):
    disabled = Settings(
        database_url=env.database_url, data_root=env.data_root, purge_enabled=False
    )
    assert scheduler.start(disabled) is None


def test_scheduled_sweep_purges_an_eligible_case(session, user, env):
    case = pipeline.create_case(
        session, client_name="DEMO", insurer="DEMO", transaction_type="addition",
        actor=user.username, bms_comments="",
    )
    pipeline.add_upload(session, case, "note.txt", b"synthetic", actor=user.username)
    pipeline.close_case(session, case, actor=user.username)
    case.closed_at = datetime.now(timezone.utc) - timedelta(hours=48)
    session.commit()

    asyncio.run(scheduler.purge_loop(env, run_once=True))

    with db.session_scope() as fresh:
        record = fresh.scalars(select(CaseFile).where(CaseFile.case_id == case.id)).one()
        assert record.status == "purged"
        # The case itself is never deleted.
        assert fresh.get(Case, case.id) is not None


def test_a_failing_sweep_does_not_kill_the_loop(env, monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("bms.scheduler.purge_closed_cases", explode)
    asyncio.run(scheduler.purge_loop(env, run_once=True))
    assert "retention purge failed" in caplog.text


# ------------------------------------------------------------- migrations


def test_migrations_create_the_same_schema_the_models_describe(tmp_path):
    """Alembic head must match the model metadata, so a fresh deploy matches a
    migrated one."""
    database = tmp_path / "migrated.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=APP_ROOT,
        env={**dict(__import__("os").environ), "BMS_DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    import sqlite3

    connection = sqlite3.connect(database)
    migrated = {
        row[0] for row in connection.execute("select name from sqlite_master where type='table'")
    } - {"alembic_version"}
    expected = set(Base.metadata.tables)
    assert expected - migrated == set(), f"migration is missing tables: {expected - migrated}"


def test_migrations_can_be_reversed(tmp_path):
    database = tmp_path / "reversible.db"
    environment = {**dict(__import__("os").environ), "BMS_DATABASE_URL": f"sqlite:///{database}"}
    for command in (["upgrade", "head"], ["downgrade", "base"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command],
            cwd=APP_ROOT, env=environment, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    import sqlite3

    connection = sqlite3.connect(database)
    remaining = {
        row[0] for row in connection.execute("select name from sqlite_master where type='table'")
    } - {"alembic_version"}
    assert remaining == set()


def test_a_com_failure_degrades_instead_of_losing_the_export(tmp_path, monkeypatch):
    """Found by the first Windows CI run, which is the only place it could show.

    `excel_available()` checked that pywin32 imported and concluded Excel was
    usable. On a Windows host with pywin32 but no Excel -- or an Excel not
    registered for the service account -- COM then raised
    `com_error: Invalid class string`, which propagated out and failed the whole
    export. The workbook on disk was already correct; only its formulas were
    uncalculated, which is precisely the state the platform is designed to
    degrade to.
    """
    target = tmp_path / "log.xlsx"
    target.write_bytes(b"PK\x03\x04placeholder")

    monkeypatch.setattr(recalc, "excel_available", lambda: (True, "pretending Excel is here"))

    def excel_is_not_really_there(_path):
        raise OSError("(-2147221005, 'Invalid class string', None, None)")

    monkeypatch.setattr(recalc, "_recalculate_with_com", excel_is_not_really_there)

    result = recalc.recalculate(target, template_key="bms.log.2026")

    assert result.pending, "a failed recalculation must not be reported as done"
    assert result.engine == "failed"
    assert "Invalid class string" in result.detail
    assert "opens correctly" in result.detail
    assert target.exists(), "the generated workbook must survive"


def test_availability_is_about_excel_not_about_pywin32():
    """The probe must answer 'can Excel be driven', not 'is the bridge installed'."""
    available, reason = recalc.excel_available()
    if sys.platform != "win32":
        assert not available
        assert "Windows" in reason
    else:  # pragma: no cover - only on a Windows host
        # Either answer is legitimate; what matters is that a True here means
        # Excel really is registered, not merely that pywin32 imported.
        assert isinstance(available, bool)
        if not available:
            assert "pywin32" in reason or "Excel" in reason
