"""Password hashing and JWT issuance — the cryptographic primitives, with no web framework.

Role
----
Turns a password into a verifiable hash, and a user identity into a signed token. Deliberately
free of FastAPI imports so the rules can be unit-tested directly and reused by the CLI (which
seeds the first admin account) without starting a web server.

Design notes worth defending
----------------------------
**argon2id for passwords.** Chosen over bcrypt because it is the OWASP Password Storage Cheat
Sheet's first recommendation and the Password Hashing Competition winner. Unlike bcrypt it is
*memory*-hard, so an attacker with GPUs or ASICs gains far less advantage per unit of hardware
than they do against a purely CPU-hard function. bcrypt additionally truncates input at 72 bytes,
which silently weakens long passphrases — exactly the passwords a security-conscious user picks.

**Two token types, with different lifetimes and different revocability.** A JWT cannot be
un-issued: anyone holding a validly signed token is authenticated until it expires. The standard
resolution, used here, is to make the token that travels on every request short-lived (minutes),
and to hold the long-lived credential (days) as a *refresh* token that is checked against a
server-side denylist on use. That yields the operational property a security tool needs — logout
and compromise response actually revoke access — while keeping the per-request path stateless.

Every token carries a ``jti`` (unique id) so an individual token can be denied, and a ``typ``
claim so an access token can never be presented where a refresh token is required. Omitting
``typ`` is a real vulnerability rather than an oversight: without it, the long-lived refresh token
is silently accepted as an access token on every endpoint, defeating the short access lifetime
entirely.

Inputs / outputs
----------------
Input: plaintext passwords, user identities, encoded tokens.
Output: password hashes, signed JWTs, and validated :class:`TokenPayload` objects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from graphguard.platform.settings import Settings, get_settings

TokenType = Literal["access", "refresh"]

#: Single hasher instance — constructing one per call would repeat parameter setup on every login.
_password_hash = PasswordHash((Argon2Hasher(),))


class InvalidTokenError(Exception):
    """Raised when a token is malformed, expired, wrongly signed, or of the wrong type."""


def hash_password(password: str) -> str:
    """Return an argon2id hash of ``password``, salt included in the encoded output."""
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Return whether ``password`` matches ``hashed``.

    Returns ``False`` rather than raising on a malformed hash, so that a corrupted row cannot be
    distinguished from a wrong password by an attacker timing or probing the login endpoint.
    """
    try:
        return _password_hash.verify(password, hashed)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """The validated claims of a decoded token."""

    subject: str
    role: str
    token_type: TokenType
    jti: str
    expires_at: datetime


def _create_token(
    *,
    subject: str,
    role: str,
    token_type: TokenType,
    ttl: timedelta,
    settings: Settings,
) -> tuple[str, str, datetime]:
    """Encode one token; returns ``(encoded, jti, expires_at)``."""
    now = datetime.now(UTC)
    expires_at = now + ttl
    jti = uuid.uuid4().hex
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "typ": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    encoded = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded, jti, expires_at


def create_access_token(
    subject: str, role: str, *, settings: Settings | None = None
) -> tuple[str, str, datetime]:
    """Issue a short-lived access token for ``subject``."""
    settings = settings or get_settings()
    return _create_token(
        subject=subject,
        role=role,
        token_type="access",
        ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        settings=settings,
    )


def create_refresh_token(
    subject: str, role: str, *, settings: Settings | None = None
) -> tuple[str, str, datetime]:
    """Issue a long-lived, revocable refresh token for ``subject``."""
    settings = settings or get_settings()
    return _create_token(
        subject=subject,
        role=role,
        token_type="refresh",
        ttl=timedelta(days=settings.refresh_token_ttl_days),
        settings=settings,
    )


def decode_token(
    token: str, *, expected_type: TokenType, settings: Settings | None = None
) -> TokenPayload:
    """Validate ``token`` and return its claims.

    ``expected_type`` is mandatory rather than optional: making the caller state which kind of
    token it expects means a refresh token can never be silently accepted in place of an access
    token, which would nullify the short access-token lifetime.

    Raises
    ------
    InvalidTokenError
        If the signature is wrong, the token has expired, a required claim is missing, or the
        ``typ`` claim does not match ``expected_type``.

    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    token_type = claims.get("typ")
    if token_type != expected_type:
        raise InvalidTokenError(f"expected a {expected_type} token, got {token_type!r}")

    return TokenPayload(
        subject=str(claims["sub"]),
        role=str(claims.get("role", "")),
        token_type=expected_type,
        jti=str(claims["jti"]),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )
