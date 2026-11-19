"""Database engine, session factory and declarative base.

Role
----
Owns the single SQLAlchemy engine for the process and hands out scoped sessions. Every module that
touches the database goes through :func:`get_session` (as a FastAPI dependency) or
:func:`session_scope` (in scripts, jobs and tests), so transaction boundaries are defined in one
place rather than at each call site.

Why the database is reachable through one URL
---------------------------------------------
The deployment target is PostgreSQL, but the test suite and a fresh checkout run on SQLite. Both
work because access is entirely through SQLAlchemy Core/ORM constructs and never through
hand-written dialect-specific SQL. The one place the dialects genuinely differ — JSON storage — is
handled by :data:`JSONColumn` in :mod:`graphguard.platform.models`, and SQLite's need for
``check_same_thread=False`` plus foreign-key enforcement is handled here.

Inputs / outputs
----------------
Input: ``Settings.database_url``.
Output: a configured :class:`~sqlalchemy.engine.Engine`, a session factory, and the declarative
:class:`Base` that the ORM models inherit from.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from graphguard.platform.settings import Settings, get_settings

#: Deterministic names for every constraint and index.
#:
#: Without this, the database assigns names itself, and a constraint whose name is not known in
#: advance cannot be dropped or altered by a later migration — the migration would have to look
#: the name up at runtime, which differs per dialect. Alembic's documentation recommends setting
#: this before the first migration precisely because retrofitting it later means renaming every
#: constraint in a live database.
#:
#: The column name is part of the check-constraint template deliberately: two columns on one table
#: can share an enum type (``alert_state_changes`` has both ``from_state`` and ``to_state``), and
#: a template without the column produces two constraints with the same name, which Postgres
#: rejects outright.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every GraphGuard ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, created on first use."""
    return _build_engine(get_settings())


def _build_engine(settings: Settings) -> Engine:
    """Create an engine configured for the dialect named in ``settings.database_url``."""
    kwargs: dict[str, Any] = {"echo": settings.database_echo, "future": True}

    if settings.is_sqlite:
        # SQLite's default threading check rejects a connection reused across
        # threads, which is exactly what the FastAPI thread pool does.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Recycle below the typical proxy/database idle timeout, and pre-ping so
        # a connection dropped by the network surfaces as a retry rather than as
        # a request failure.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20

    engine = create_engine(settings.database_url, **kwargs)

    if settings.is_sqlite:
        # SQLite does not enforce foreign keys unless asked, per connection. Without
        # this the ON DELETE CASCADE rules below are silently inert under SQLite and
        # enforced under Postgres, so the test suite would not exercise what
        # production does.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session — the FastAPI dependency form.

    The session is closed on the way out; committing is the caller's decision, because a route that
    only reads should not emit a transaction commit.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session wrapped in a transaction — the script, job and test form.

    Commits on clean exit, rolls back on any exception. Use this anywhere outside a request, where
    there is no dependency injector to manage the lifecycle.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop the cached engine and session factory.

    Needed by tests, which point the settings at a fresh database per module and would otherwise
    keep talking to the first one created in the process.
    """
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
