"""Authentication and authorisation for the HTTP layer.

Role
----
Turns the primitives in :mod:`graphguard.platform.security` and
:mod:`graphguard.platform.tokens` into FastAPI dependencies and routes: sign in, refresh, sign
out, "who am I", and the role guards that protect every other endpoint.

Authorisation model
-------------------
Two roles. An **analyst** reads telemetry, triages alerts and exports
incidents. An **administrator** additionally edits detection configuration and manages accounts.

The split is not cosmetic. A detector threshold quietly raised is indistinguishable, from the
alert queue, from a period with no attacks — so the ability to change detection parameters is a
strictly greater privilege than the ability to read alerts, and is separated accordingly.

Note that authorisation deliberately does *not* extend to per-entity filtering: any authenticated
analyst sees all alerts. That matches how a SOC actually works and keeps the model honest; a
multi-tenant deployment would need row-level scoping, which is named in the report's future work
rather than half-implemented here.

Inputs / outputs
----------------
Input: HTTP credentials (a login form, or a bearer token on the ``Authorization`` header).
Output: token pairs, and a resolved :class:`~graphguard.platform.models.User` injected into
protected route handlers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from graphguard.platform.db import get_session
from graphguard.platform.models import Role, User
from graphguard.platform.security import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from graphguard.platform.tokens import is_revoked, revoke

# auto_error=False so a missing header produces our own 401 with a WWW-Authenticate
# challenge, rather than FastAPI's 403, which is the wrong status for "not authenticated".
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[Session, Depends(get_session)]


# --------------------------------------------------------------------------- schemas


class LoginRequest(BaseModel):
    """Credentials presented at sign-in."""

    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    """A refresh token presented to obtain a new token pair."""

    refresh_token: str


class TokenPair(BaseModel):
    """What a successful sign-in or refresh returns."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserOut(BaseModel):
    """A user as the API exposes it. Note the absence of any password field."""

    id: str
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class CreateUserRequest(BaseModel):
    """An account to create. Admin-only."""

    email: EmailStr
    password: str = Field(min_length=12, description="Minimum 12 characters.")
    full_name: str = ""
    role: Role = Role.ANALYST


# --------------------------------------------------------------------------- dependencies


def _unauthorised(detail: str) -> HTTPException:
    """Build a 401 carrying the bearer challenge required by RFC 6750."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """Resolve the bearer token on the request into a live, active user.

    The database lookup on each request is deliberate. A pure-JWT implementation would trust the
    claims and skip it, but then deactivating an account would have no effect until every issued
    access token expired. Re-reading the user bounds that window to zero for account state, while
    the token itself still carries the signature check that avoids a session lookup.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorised("Not authenticated")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as exc:
        raise _unauthorised(f"Invalid access token: {exc}") from exc

    user = session.get(User, payload.subject)
    if user is None or not user.is_active:
        raise _unauthorised("Account no longer active")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed: Role) -> Callable[[User], User]:
    """Build a dependency that admits only the listed roles.

    Used as ``Depends(require_role(Role.ADMIN))``. Returns 403 rather than 401 when the caller is
    authenticated but insufficiently privileged, since re-authenticating would not help.
    """

    def _guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(r.value for r in allowed)}",
            )
        return user

    return _guard


AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_pair(user: User) -> TokenPair:
    """Mint a fresh access/refresh pair for ``user``."""
    access, _, access_expires = create_access_token(user.id, user.role.value)
    refresh, _, _ = create_refresh_token(user.id, user.role.value)
    return TokenPair(access_token=access, refresh_token=refresh, expires_at=access_expires)


# Precomputed once at import: the unknown-email branch must verify against *some*
# hash so a wrong email and a wrong password take indistinguishable time, and
# hashing a fresh dummy per request would make the unknown-email branch do hash
# plus verify — roughly twice the work of the known-email branch, which is itself
# a measurable timing difference and doubles the CPU an attacker can burn per
# unauthenticated request.
_TIMING_EQUALISATION_HASH = hash_password("timing-equalisation")


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, session: SessionDep) -> TokenPair:
    """Exchange credentials for a token pair."""
    user = session.scalar(select(User).where(User.email == payload.email.lower()))

    # Verify a hash even when the user does not exist, so that a wrong email and a
    # wrong password take indistinguishable time. Skipping this leaks which addresses
    # hold accounts, which is the first step of a credential-stuffing campaign — the
    # very attack this system is built to detect.
    hashed = user.hashed_password if user else _TIMING_EQUALISATION_HASH
    password_ok = verify_password(payload.password, hashed)

    if user is None or not password_ok or not user.is_active:
        raise _unauthorised("Incorrect email or password")

    user.last_login_at = datetime.now(UTC)
    session.commit()
    return _issue_pair(user)


@router.post("/refresh", response_model=TokenPair)
def refresh_tokens(payload: RefreshRequest, session: SessionDep) -> TokenPair:
    """Exchange a refresh token for a new pair, invalidating the one presented.

    Rotation on every exchange makes refresh tokens single-use, so a stolen token stops working as
    soon as either party uses it.
    """
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise _unauthorised(f"Invalid refresh token: {exc}") from exc

    if is_revoked(claims.jti):
        raise _unauthorised("Refresh token has been revoked")

    user = session.get(User, claims.subject)
    if user is None or not user.is_active:
        raise _unauthorised("Account no longer active")

    revoke(claims.jti, claims.expires_at)
    return _issue_pair(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest) -> None:
    """Revoke the presented refresh token.

    The access token is intentionally left alone; it expires within minutes, and denylisting every
    access token would reintroduce a per-request store lookup. This is the documented trade-off of
    the short-access/revocable-refresh design.
    """
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except InvalidTokenError:
        # Logging out with an already-invalid token is not an error worth reporting:
        # the desired end state (that token cannot be used) already holds.
        return
    revoke(claims.jti, claims.expires_at)


@router.get("/me", response_model=UserOut)
def read_current_user(user: CurrentUser) -> User:
    """Return the signed-in user, for the dashboard's account menu."""
    return user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: CreateUserRequest, session: SessionDep, _admin: AdminUser) -> User:
    """Create an account. Administrators only."""
    email = payload.email.lower()
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email already has an account"
        )
    user = User(
        email=email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    session.add(user)
    session.commit()
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(session: SessionDep, _admin: AdminUser) -> list[User]:
    """List all accounts. Administrators only."""
    return list(session.scalars(select(User).order_by(User.created_at)))


def client_ip(request: Request) -> str:
    """Best-effort client address, for rate limiting behind the reverse proxy.

    Trusts ``X-Forwarded-For`` only because the deployment terminates TLS at Nginx, which sets it.
    Exposed directly to the internet this header is attacker-controlled and must not be trusted —
    a caveat recorded here so the deployment configuration and this assumption stay linked.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
