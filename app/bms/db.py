"""Database engine and session handling.

SQLite is permitted for the prototype; the model layer is written so the same
schema runs on PostgreSQL unchanged. The only dialect-specific handling is here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, settings
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    """Make SQLite behave enough like PostgreSQL to be a fair prototype.

    Foreign keys are off by default in SQLite, which would let the prototype
    accept relationships PostgreSQL would reject. WAL keeps reads working while
    a background job writes.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def init_engine(config: Settings | None = None, *, echo: bool = False) -> Engine:
    global _engine, _SessionFactory
    config = config or settings
    url = config.database_url

    kwargs: dict = {"echo": echo, "future": True}
    if url.startswith("sqlite"):
        # check_same_thread=False so the FastAPI worker threads share the file.
        kwargs["connect_args"] = {"check_same_thread": False}

    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _configure_sqlite(_engine)

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def create_all() -> None:
    """Create the schema.

    Adequate for the prototype. Production should move to Alembic migrations so
    schema changes are reviewable and reversible.
    """
    Base.metadata.create_all(get_engine())


def session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
