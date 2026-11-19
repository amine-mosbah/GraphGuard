"""Shared fixtures for the platform tests.

Each test module that touches the database gets its own throwaway SQLite file and its own
``fakeredis`` instance, so tests neither need a running server nor leak state into one another.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def isolate_local_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep developer credentials and assistant choices out of the regression suite."""
    from graphguard.platform.settings import Settings, get_settings

    # Explicit _env_file arguments still work in the dotenv-loading test. The
    # repository's real .env must not select a live provider for scripted-fake tests.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in (
        "GRAPHGUARD_ASSISTANT_ENABLED",
        "GRAPHGUARD_ASSISTANT_PROVIDER",
        "GRAPHGUARD_ASSISTANT_MODEL",
        "GRAPHGUARD_ASSISTANT_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def platform_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Point the platform at a temporary database and an in-memory Redis.

    Yields a dict holding the fake Redis client, so tests can assert on revocation state directly.
    """
    import fakeredis

    from graphguard.platform import db, tokens
    from graphguard.platform.settings import get_settings

    # Default to a throwaway SQLite file so the suite needs no running server. Setting
    # GRAPHGUARD_TEST_DATABASE_URL points the same tests at Postgres instead, which is
    # what proves the persistence layer really is portable across the two — the claim is
    # only worth making if it is executed, so CI runs the suite both ways.
    db_path = tmp_path / "test.db"
    override = os.environ.get("GRAPHGUARD_TEST_DATABASE_URL")
    monkeypatch.setenv("GRAPHGUARD_DATABASE_URL", override or f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("GRAPHGUARD_JWT_SECRET", "test-secret-value-of-sufficient-length-000")
    monkeypatch.setenv("GRAPHGUARD_ENVIRONMENT", "test")

    # The settings, engine and Redis client are all process-cached; clear them so this
    # test's environment is the one that takes effect.
    get_settings.cache_clear()
    db.reset_engine_cache()
    tokens.reset_redis_cache()

    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(tokens, "get_redis", lambda: fake)

    from graphguard.platform import models  # noqa: F401  (registers tables)

    db.Base.metadata.create_all(db.get_engine())

    yield {"redis": fake, "db_path": db_path}

    db.Base.metadata.drop_all(db.get_engine())
    get_settings.cache_clear()
    db.reset_engine_cache()
    # `tokens.get_redis` is still monkeypatched here (pytest restores it after this
    # finalizer runs), so calling reset_redis_cache() would hit the lambda, which has
    # no cache to clear. Setup clears it per test, which is what actually matters.
    os.environ.pop("GRAPHGUARD_DATABASE_URL", None)
