"""Deployment settings — the platform's runtime environment, kept apart from analytics config.

Role
----
Holds everything the *deployed service* needs and the *analysis* does not: database URL, Redis
URL, JWT signing key and token lifetimes, CORS origins, rate limits.

Why this is a separate model from :class:`~graphguard.config.GraphGuardConfig`
-----------------------------------------------------------------------------
``GraphGuardConfig`` is analytical configuration: window sizes, detector hyper-parameters,
simulator deltas. ``DECISIONS.md`` D4 requires every experiment run to snapshot that config next
to its results so a run is reproducible from its own artifacts.

Deployment settings must never take part in that. A JWT signing key or a database password
serialised into ``runs/<name>/config.yaml`` would be a credential leak committed to the repository
the first time someone saved a run. Keeping the two models separate makes that mistake impossible
rather than merely discouraged: nothing in the analytics path can reach a secret, because the
secret does not live on the object the analytics path serialises.

The split also matches how the two are supplied. Analytical config is a YAML file that belongs in
version control and is deliberately edited between experiments. Deployment settings come from the
environment, differ per host, and are never committed.

Inputs / outputs
----------------
Input: process environment, optionally seeded by a local ``.env`` file (development only).
Output: a validated :class:`Settings`, consumed by the database session factory, the auth layer
and the FastAPI application factory.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    """Environment-sourced deployment settings.

    Every field carries a development default so the service starts from a clean checkout with no
    setup, but the defaults that would be dangerous in production are rejected when
    ``environment`` is ``production`` (see :meth:`_reject_dev_defaults_in_production`).
    """

    model_config = SettingsConfigDict(
        env_prefix="GRAPHGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"

    # --- persistence -------------------------------------------------------
    # Default is SQLite so tests and a fresh checkout need no running server;
    # deployment sets a Postgres URL. Because access goes through SQLAlchemy,
    # this really is the only line that changes between the two.
    database_url: str = "sqlite+pysqlite:///./graphguard.db"
    database_echo: bool = False

    # --- cache / broker ----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- auth --------------------------------------------------------------
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    # Short-lived access tokens limit the damage of a leaked token; the refresh
    # token is long-lived but revocable through the Redis denylist, which is the
    # pairing that makes stateless JWT auth revocable at all.
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_ttl_days: int = Field(default=14, ge=1, le=90)

    # --- http --------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- assistant ---------------------------------------------------------
    # Off by default, deliberately: the report describes the platform without a
    # language model behind the assistant, so the deployed default must match the
    # reported system. Turning it on also requires the `assistant` extra and an
    # Anthropic credential in the environment (see platform/assistant.py).
    assistant_enabled: bool = False
    # "anthropic" (native driver, reasoning summaries) or "openai" (chat-completions
    # driver — OpenAI itself, or any compatible endpoint via assistant_base_url, e.g.
    # OpenRouter). Model names follow the provider: claude-opus-5, gpt-5.6-sol,
    # openai/gpt-5.6-sol on OpenRouter, and so on.
    assistant_provider: Literal["anthropic", "openai"] = "anthropic"
    assistant_model: str = "claude-opus-5"
    # Only read by the "openai" provider; empty means the SDK default endpoint.
    assistant_base_url: str = ""
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENAI_API_KEY", exclude=True, repr=False
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, since env vars cannot carry JSON lists comfortably."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _reject_dev_defaults_in_production(self) -> Settings:
        """Fail loudly at startup rather than silently serving production on dev credentials.

        The failure mode this prevents is specific: a deployment that forgets to set
        ``GRAPHGUARD_JWT_SECRET`` would otherwise sign tokens with a value published in this
        repository, so anyone could mint an admin token. That has to be a startup crash, not a
        log line.
        """
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("GRAPHGUARD_JWT_SECRET is still the development default")
        if len(self.jwt_secret) < 32:
            problems.append("GRAPHGUARD_JWT_SECRET is shorter than 32 characters")
        if self.database_url.startswith("sqlite"):
            problems.append("GRAPHGUARD_DATABASE_URL points at SQLite")
        if problems:
            raise ValueError(
                "refusing to start in production with insecure settings: " + "; ".join(problems)
            )
        return self

    @property
    def is_sqlite(self) -> bool:
        """Whether the configured database is SQLite, which needs connect-args special cases."""
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once.

    Cached because settings are immutable for the life of the process; tests that need different
    values call :meth:`cache_clear` on this function.
    """
    return Settings()


def generate_secret() -> str:
    """Return a cryptographically strong value suitable for ``GRAPHGUARD_JWT_SECRET``."""
    return secrets.token_urlsafe(48)
