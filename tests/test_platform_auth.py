"""Tests for authentication, authorisation and the token lifecycle.

These deliberately assert the *security* properties rather than only the happy path: a passing
login test says nothing about whether a refresh token is accepted where an access token belongs,
which is the failure that actually matters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from graphguard.platform import auth, security, tokens
from graphguard.platform.db import session_scope
from graphguard.platform.models import Role, User


def _make_app() -> FastAPI:
    """Build a minimal app carrying only the auth router plus two guarded probes."""
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/probe/any")
    def _any(user: auth.CurrentUser) -> dict[str, str]:
        return {"email": user.email}

    @app.get("/probe/admin")
    def _admin(user: User = Depends(auth.require_role(Role.ADMIN))) -> dict[str, str]:
        return {"email": user.email}

    return app


@pytest.fixture
def client(platform_env: dict[str, Any]) -> TestClient:
    """A test client with one analyst and one admin already provisioned."""
    with session_scope() as session:
        session.add_all(
            [
                User(
                    email="analyst@eniso.tn",
                    full_name="An Analyst",
                    hashed_password=security.hash_password("analyst-password-01"),
                    role=Role.ANALYST,
                ),
                User(
                    email="admin@eniso.tn",
                    full_name="An Admin",
                    hashed_password=security.hash_password("admin-password-0001"),
                    role=Role.ADMIN,
                ),
            ]
        )
    return TestClient(_make_app())


def _login(client: TestClient, email: str, password: str) -> dict[str, Any]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- sign-in


def test_login_returns_a_token_pair(client: TestClient) -> None:
    body = _login(client, "analyst@eniso.tn", "analyst-password-01")
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


def test_login_rejects_a_wrong_password(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"email": "analyst@eniso.tn", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_unknown_email_and_wrong_password_are_indistinguishable(client: TestClient) -> None:
    """The response must not reveal whether an address has an account.

    Distinguishable responses turn the login endpoint into an account-enumeration oracle, which is
    the reconnaissance step before the credential-stuffing attack this system detects.
    """
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@eniso.tn", "password": "whatever-value"}
    )
    wrong = client.post(
        "/api/auth/login", json={"email": "analyst@eniso.tn", "password": "whatever-value"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_deactivated_account_cannot_sign_in(client: TestClient) -> None:
    with session_scope() as session:
        user = session.query(User).filter_by(email="analyst@eniso.tn").one()
        user.is_active = False
    response = client.post(
        "/api/auth/login", json={"email": "analyst@eniso.tn", "password": "analyst-password-01"}
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- guards


def test_protected_route_requires_a_token(client: TestClient) -> None:
    response = client.get("/probe/any")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_protected_route_accepts_a_valid_token(client: TestClient) -> None:
    token = _login(client, "analyst@eniso.tn", "analyst-password-01")["access_token"]
    response = client.get("/probe/any", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "analyst@eniso.tn"


def test_analyst_is_refused_an_admin_route(client: TestClient) -> None:
    token = _login(client, "analyst@eniso.tn", "analyst-password-01")["access_token"]
    response = client.get("/probe/admin", headers={"Authorization": f"Bearer {token}"})
    # 403, not 401: the caller is authenticated, just not privileged. Signing in
    # again would not change the outcome.
    assert response.status_code == 403


def test_admin_reaches_an_admin_route(client: TestClient) -> None:
    token = _login(client, "admin@eniso.tn", "admin-password-0001")["access_token"]
    response = client.get("/probe/admin", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_deactivating_a_user_invalidates_their_live_token(client: TestClient) -> None:
    """Account state is re-read per request, so revocation does not wait for token expiry."""
    token = _login(client, "analyst@eniso.tn", "analyst-password-01")["access_token"]
    assert client.get("/probe/any", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    with session_scope() as session:
        session.query(User).filter_by(email="analyst@eniso.tn").one().is_active = False

    assert client.get("/probe/any", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_forged_token_is_rejected(client: TestClient) -> None:
    forged = security.jwt.encode(
        {"sub": "whoever", "role": "admin", "typ": "access", "jti": "x", "exp": 9999999999},
        "not-the-real-signing-key-but-long-enough-to-avoid-a-warning",
        algorithm="HS256",
    )
    response = client.get("/probe/any", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- token types


def test_a_refresh_token_is_not_accepted_as_an_access_token(client: TestClient) -> None:
    """The `typ` claim must be enforced.

    Without it the long-lived refresh token authenticates every endpoint, which defeats the entire
    point of a short access-token lifetime.
    """
    refresh = _login(client, "analyst@eniso.tn", "analyst-password-01")["refresh_token"]
    response = client.get("/probe/any", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


def test_an_access_token_is_not_accepted_for_refresh(client: TestClient) -> None:
    access = _login(client, "analyst@eniso.tn", "analyst-password-01")["access_token"]
    response = client.post("/api/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


def test_an_expired_token_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    with session_scope() as session:
        user_id = session.query(User).filter_by(email="analyst@eniso.tn").one().id
    expired = security.jwt.encode(
        {
            "sub": user_id,
            "role": "analyst",
            "typ": "access",
            "jti": "expired",
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        },
        "test-secret-value-of-sufficient-length-000",
        algorithm="HS256",
    )
    response = client.get("/probe/any", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- rotation


def test_refresh_returns_a_new_pair(client: TestClient) -> None:
    first = _login(client, "analyst@eniso.tn", "analyst-password-01")
    response = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200
    assert response.json()["refresh_token"] != first["refresh_token"]


def test_a_refresh_token_is_single_use(client: TestClient) -> None:
    """Rotation makes a stolen refresh token stop working the moment either party uses it."""
    first = _login(client, "analyst@eniso.tn", "analyst-password-01")
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code
        == 200
    )
    replay = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401


def test_logout_revokes_the_refresh_token(client: TestClient) -> None:
    pair = _login(client, "analyst@eniso.tn", "analyst-password-01")
    assert (
        client.post("/api/auth/logout", json={"refresh_token": pair["refresh_token"]}).status_code
        == 204
    )
    after = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert after.status_code == 401


def test_logout_with_a_junk_token_is_not_an_error(client: TestClient) -> None:
    """The desired end state already holds, so this is success, not failure."""
    response = client.post("/api/auth/logout", json={"refresh_token": "not-a-token"})
    assert response.status_code == 204


def test_revocation_entries_expire_with_the_token(platform_env: dict[str, Any]) -> None:
    """Denylist entries carry a TTL, so the list cannot grow without bound."""
    fake = platform_env["redis"]
    expires = datetime.now(UTC) + timedelta(seconds=120)
    tokens.revoke("some-jti", expires, client=fake)
    ttl = fake.ttl("graphguard:revoked-jti:some-jti")
    assert 0 < ttl <= 120


def test_an_already_expired_token_is_not_stored(platform_env: dict[str, Any]) -> None:
    fake = platform_env["redis"]
    tokens.revoke("stale", datetime.now(UTC) - timedelta(seconds=1), client=fake)
    assert not tokens.is_revoked("stale", client=fake)


# --------------------------------------------------------------------------- user management


def test_admin_can_create_a_user(client: TestClient) -> None:
    token = _login(client, "admin@eniso.tn", "admin-password-0001")["access_token"]
    response = client.post(
        "/api/auth/users",
        json={"email": "new@eniso.tn", "password": "a-long-enough-password", "role": "analyst"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    assert "password" not in response.text and "hashed" not in response.text


def test_analyst_cannot_create_a_user(client: TestClient) -> None:
    token = _login(client, "analyst@eniso.tn", "analyst-password-01")["access_token"]
    response = client.post(
        "/api/auth/users",
        json={"email": "new@eniso.tn", "password": "a-long-enough-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_short_passwords_are_refused(client: TestClient) -> None:
    token = _login(client, "admin@eniso.tn", "admin-password-0001")["access_token"]
    response = client.post(
        "/api/auth/users",
        json={"email": "new@eniso.tn", "password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_duplicate_email_is_a_conflict(client: TestClient) -> None:
    token = _login(client, "admin@eniso.tn", "admin-password-0001")["access_token"]
    response = client.post(
        "/api/auth/users",
        json={"email": "analyst@eniso.tn", "password": "a-long-enough-password"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409


def test_me_returns_the_signed_in_user_without_secrets(client: TestClient) -> None:
    token = _login(client, "admin@eniso.tn", "admin-password-0001")["access_token"]
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin@eniso.tn"
    assert body["role"] == "admin"
    assert "hashed_password" not in body


# --------------------------------------------------------------------------- hashing


def test_passwords_are_hashed_with_argon2id() -> None:
    digest = security.hash_password("some-password")
    assert digest.startswith("$argon2id$")


def test_the_same_password_hashes_differently_each_time() -> None:
    """Distinct salts, so identical passwords are not identifiable from the database."""
    a = security.hash_password("same-password")
    b = security.hash_password("same-password")
    assert a != b
    assert security.verify_password("same-password", a)
    assert security.verify_password("same-password", b)


def test_verify_returns_false_on_a_corrupt_hash() -> None:
    assert security.verify_password("anything", "not-a-valid-hash") is False
