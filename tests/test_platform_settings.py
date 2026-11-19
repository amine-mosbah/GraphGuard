"""Tests for deployment settings and first-run provisioning.

The production guard is the interesting one: it exists to convert "someone forgot to set the
signing key" from a silent, total authentication bypass into a startup crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphguard.platform.bootstrap import BootstrapError, create_admin, user_count
from graphguard.platform.db import session_scope
from graphguard.platform.models import Role
from graphguard.platform.settings import DEV_JWT_SECRET, Settings, generate_secret

# --------------------------------------------------------------------------- settings


def test_openai_key_loads_from_dotenv_without_being_serialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=test-local-secret\n")
    settings = Settings(_env_file=env_file)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-local-secret"
    assert "test-local-secret" not in repr(settings)
    assert "openai_api_key" not in settings.model_dump()
    monkeypatch.setenv("OPENAI_API_KEY", "test-environment-secret")
    settings = Settings(_env_file=env_file)
    assert settings.openai_api_key.get_secret_value() == "test-environment-secret"


def test_development_defaults_are_usable_without_configuration() -> None:
    """A clean checkout must start with no setup, or nobody will run it."""
    settings = Settings(environment="development")
    assert settings.jwt_secret == DEV_JWT_SECRET
    assert settings.is_sqlite


def test_production_rejects_the_development_signing_key() -> None:
    """Otherwise the service would sign tokens with a value published in this repository."""
    with pytest.raises(ValueError, match="development default"):
        Settings(environment="production", jwt_secret=DEV_JWT_SECRET, database_url="postgresql://x")


def test_production_rejects_a_short_signing_key() -> None:
    with pytest.raises(ValueError, match="shorter than 32"):
        Settings(environment="production", jwt_secret="too-short", database_url="postgresql://x")


def test_production_rejects_sqlite() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        Settings(
            environment="production",
            jwt_secret=generate_secret(),
            database_url="sqlite+pysqlite:///./x.db",
        )


def test_production_accepts_a_properly_configured_deployment() -> None:
    settings = Settings(
        environment="production",
        jwt_secret=generate_secret(),
        database_url="postgresql+psycopg://user:pw@db:5432/graphguard",
    )
    assert not settings.is_sqlite


def test_generated_secrets_are_long_and_unique() -> None:
    first, second = generate_secret(), generate_secret()
    assert len(first) >= 32
    assert first != second


def test_cors_origins_accept_a_comma_separated_string() -> None:
    """Environment variables cannot carry JSON lists comfortably."""
    settings = Settings(cors_origins="http://a.test, http://b.test")  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


# --------------------------------------------------------------------------- bootstrap


def test_create_admin_creates_an_admin(platform_env: dict[str, Any]) -> None:
    with session_scope() as session:
        assert user_count(session) == 0
        user = create_admin(session, "boss@eniso.tn", "a-sufficiently-long-password")
        assert user.role is Role.ADMIN
        assert user.hashed_password != "a-sufficiently-long-password"
    with session_scope() as session:
        assert user_count(session) == 1


def test_create_admin_normalises_the_email(platform_env: dict[str, Any]) -> None:
    with session_scope() as session:
        user = create_admin(session, "  BOSS@ENISo.TN ", "a-sufficiently-long-password")
        assert user.email == "boss@eniso.tn"


def test_create_admin_refuses_a_short_password(platform_env: dict[str, Any]) -> None:
    with pytest.raises(BootstrapError, match="at least 12"), session_scope() as session:
        create_admin(session, "boss@eniso.tn", "short")


def test_create_admin_refuses_a_duplicate_email(platform_env: dict[str, Any]) -> None:
    with session_scope() as session:
        create_admin(session, "boss@eniso.tn", "a-sufficiently-long-password")
    with pytest.raises(BootstrapError, match="already exists"), session_scope() as session:
        create_admin(session, "boss@eniso.tn", "another-long-password")
