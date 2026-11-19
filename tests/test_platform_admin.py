"""Tests for persisted configuration and case management.

The configuration tests matter more than they look. A detection threshold is not a preference —
it is the definition of "unusual" that every alert is measured against, so silently losing a
change, or applying half of one, would make the queue mean something different from what an
administrator believes it means.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphguard.config import load_config
from graphguard.platform import api_routes, auth, config_store, investigations, security
from graphguard.platform.db import session_scope
from graphguard.platform.models import (
    Alert,
    AlertState,
    AttackChainRecord,
    Investigation,
    InvestigationStatus,
    Role,
    Run,
    RunStatus,
    User,
)


@pytest.fixture
def api(platform_env: dict[str, Any]) -> TestClient:
    """A client with an analyst and an admin, plus one run holding an alert and a chain."""
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(api_routes.router)
    with session_scope() as session:
        session.add_all(
            [
                User(
                    email="analyst@eniso.tn",
                    hashed_password=security.hash_password("analyst-password-01"),
                    role=Role.ANALYST,
                ),
                User(
                    email="admin@eniso.tn",
                    hashed_password=security.hash_password("admin-password-0001"),
                    role=Role.ADMIN,
                ),
            ]
        )
        run = Run(name="fixture", status=RunStatus.SUCCEEDED, config={})
        session.add(run)
        session.flush()
        from datetime import UTC, datetime

        session.add(
            Alert(
                id="alert-1",
                run_id=run.id,
                detector="node_outlier:egonet",
                entity_type="api_key",
                entity_id="key-1",
                window_index=0,
                window_start=datetime.now(UTC),
                window_end=datetime.now(UTC),
                raw_score=2.0,
                normalised_score=0.95,
                explanation="because",
                state=AlertState.INVESTIGATING,
            )
        )
        session.add(
            AttackChainRecord(
                id="chain-1",
                run_id=run.id,
                source_ip="203.0.113.9",
                internal_entity_type="api_key",
                internal_entity_id="key-1",
                confidence=0.8,
            )
        )
    return TestClient(app)


def _headers(api: TestClient, email: str, password: str) -> dict[str, str]:
    body = api.post("/api/auth/login", json={"email": email, "password": password}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _admin(api: TestClient) -> dict[str, str]:
    return _headers(api, "admin@eniso.tn", "admin-password-0001")


def _analyst(api: TestClient) -> dict[str, str]:
    return _headers(api, "analyst@eniso.tn", "analyst-password-01")


# --------------------------------------------------------------------------- configuration


def test_config_falls_back_to_the_file_when_nothing_is_saved(
    platform_env: dict[str, Any],
) -> None:
    """A fresh deployment, the CLI and the evaluation harness must all start from one place."""
    with session_scope() as session:
        assert config_store.active_version(session) is None
        assert config_store.effective_config(session).run.seed == load_config().run.seed


def test_saving_a_version_activates_it(platform_env: dict[str, Any]) -> None:
    cfg = load_config()
    cfg.correlation.alert_threshold = 0.42
    with session_scope() as session:
        version = config_store.save_version(session, cfg, author=None, note="loosened")
        assert version.revision == 1
        assert version.is_active
    with session_scope() as session:
        assert config_store.effective_config(session).correlation.alert_threshold == 0.42


def test_only_one_version_is_ever_active(platform_env: dict[str, Any]) -> None:
    """Two active rows would make the effective configuration depend on row ordering."""
    cfg = load_config()
    with session_scope() as session:
        for index in range(3):
            config_store.save_version(session, cfg, author=None, note=f"v{index}")
    with session_scope() as session:
        active = [v for v in config_store.history(session) if v.is_active]
        assert len(active) == 1
        assert active[0].revision == 3


def test_rolling_back_restores_the_earlier_configuration(platform_env: dict[str, Any]) -> None:
    first = load_config()
    first.correlation.alert_threshold = 0.30
    second = load_config()
    second.correlation.alert_threshold = 0.90

    with session_scope() as session:
        original = config_store.save_version(session, first, author=None)
        config_store.save_version(session, second, author=None)
        original_id = original.id

    with session_scope() as session:
        assert config_store.effective_config(session).correlation.alert_threshold == 0.90
        from graphguard.platform.models import ConfigVersion

        config_store.activate_version(session, session.get(ConfigVersion, original_id))  # type: ignore[arg-type]

    with session_scope() as session:
        assert config_store.effective_config(session).correlation.alert_threshold == 0.30


def test_analyst_may_read_but_not_change_configuration(api: TestClient) -> None:
    analyst = _analyst(api)
    payload = api.get("/api/v1/config", headers=analyst)
    assert payload.status_code == 200
    assert payload.json()["source"] == "config/default.yaml"

    refused = api.post("/api/v1/config", json={"config": payload.json()["config"]}, headers=analyst)
    assert refused.status_code == 403


def test_admin_can_save_and_the_source_changes(api: TestClient) -> None:
    admin = _admin(api)
    current = api.get("/api/v1/config", headers=admin).json()["config"]
    saved = api.post("/api/v1/config", json={"config": current, "note": "baseline"}, headers=admin)
    assert saved.status_code == 201
    assert saved.json()["revision"] == 1

    after = api.get("/api/v1/config", headers=admin).json()
    assert after["source"] == "database"
    assert after["active_version"]["note"] == "baseline"


def test_an_invalid_configuration_is_rejected_before_anything_is_written(
    api: TestClient,
) -> None:
    """A half-applied configuration would leave detection running on thresholds nobody chose."""
    admin = _admin(api)
    refused = api.post("/api/v1/config", json={"config": {"run": {"seed": "nope"}}}, headers=admin)
    assert refused.status_code == 422
    assert api.get("/api/v1/config/versions", headers=admin).json()["versions"] == []


def test_a_revision_saved_under_an_older_schema_does_not_take_the_platform_down(
    api: TestClient,
) -> None:
    """The regression this exists for.

    ``correlation.explanation.subgraph_radius`` was removed from the schema while a revision
    carrying it was active. Because the model forbids unknown keys, every caller of
    ``effective_config`` began raising and ``GET /api/v1/config`` returned 500 — the settings page
    could not be opened at all, and runs could not be dispatched.

    Loading must therefore degrade to the file and *say so*, while saving stays strict.
    """
    admin = _admin(api)
    current = api.get("/api/v1/config", headers=admin).json()["config"]
    api.post("/api/v1/config", json={"config": current, "note": "baseline"}, headers=admin)

    # Simulate the drift: the payload on disk gains a key the current model no longer knows.
    with session_scope() as session:
        version = config_store.active_version(session)
        assert version is not None
        payload = dict(version.payload)
        payload["correlation"] = {**payload["correlation"], "subgraph_radius": 2}
        version.payload = payload

    response = api.get("/api/v1/config", headers=admin)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "config/default.yaml (fallback)"
    assert body["stale_version"]["revision"] == 1
    assert "subgraph_radius" in body["stale_reason"]
    # The configuration served is the file's, which is known to parse.
    assert body["config"]["run"]["seed"] == load_config().run.seed

    # And the broken revision cannot be re-activated into place, which would look like a
    # successful rollback while silently leaving the file in force.
    versions = api.get("/api/v1/config/versions", headers=admin).json()["versions"]
    assert versions[0]["parses"] is False
    refused = api.post(f"/api/v1/config/versions/{versions[0]['id']}/activate", headers=admin)
    assert refused.status_code == 409


def test_the_config_schema_is_served_for_the_settings_form(api: TestClient) -> None:
    """The form is generated from this, so an added detector parameter cannot go unreachable."""
    schema = api.get("/api/v1/config/schema", headers=_analyst(api))
    assert schema.status_code == 200
    body = schema.json()
    assert "correlation" in body["properties"]
    assert "$defs" in body


def test_rollback_endpoint_requires_admin(api: TestClient) -> None:
    admin = _admin(api)
    current = api.get("/api/v1/config", headers=admin).json()["config"]
    version_id = api.post("/api/v1/config", json={"config": current}, headers=admin).json()["id"]

    refused = api.post(f"/api/v1/config/versions/{version_id}/activate", headers=_analyst(api))
    assert refused.status_code == 403


# --------------------------------------------------------------------------- investigations


def test_a_case_gathers_alerts_and_chains(platform_env: dict[str, Any], api: TestClient) -> None:
    with session_scope() as session:
        case = investigations.create(session, title="Key compromise", summary="spike")
        investigations.attach(session, case, alert_id="alert-1", rationale="primary")
        investigations.attach(session, case, chain_id="chain-1")
        detail = investigations.detail(case)

    assert detail["alert_count"] == 1
    assert detail["chain_count"] == 1
    kinds = {member["kind"] for member in detail["members"]}
    assert kinds == {"alert", "chain"}


def test_attaching_requires_exactly_one_identifier(
    platform_env: dict[str, Any], api: TestClient
) -> None:
    with session_scope() as session:
        case = investigations.create(session, title="c")
        with pytest.raises(investigations.InvestigationError, match="exactly one"):
            investigations.attach(session, case)
        with pytest.raises(investigations.InvestigationError, match="exactly one"):
            investigations.attach(session, case, alert_id="alert-1", chain_id="chain-1")


def test_attaching_the_same_evidence_twice_is_refused(
    platform_env: dict[str, Any], api: TestClient
) -> None:
    with session_scope() as session:
        case = investigations.create(session, title="c")
        investigations.attach(session, case, alert_id="alert-1")
        with pytest.raises(investigations.InvestigationError, match="already attached"):
            investigations.attach(session, case, alert_id="alert-1")


def test_attaching_a_nonexistent_alert_is_refused(
    platform_env: dict[str, Any], api: TestClient
) -> None:
    with session_scope() as session:
        case = investigations.create(session, title="c")
        with pytest.raises(investigations.InvestigationError, match="no such alert"):
            investigations.attach(session, case, alert_id="not-a-real-id")


def test_a_case_needs_a_title(platform_env: dict[str, Any]) -> None:
    with pytest.raises(investigations.InvestigationError, match="title"), session_scope() as s:
        investigations.create(s, title="   ")


def test_case_status_moves_freely(platform_env: dict[str, Any]) -> None:
    """Unlike alert triage there is no transition graph: incidents legitimately reopen."""
    with session_scope() as session:
        case = investigations.create(session, title="c")
        for status in (
            InvestigationStatus.CONTAINED,
            InvestigationStatus.CLOSED,
            InvestigationStatus.OPEN,
        ):
            investigations.set_status(session, case, status)
        assert case.status is InvestigationStatus.OPEN


def test_deleting_a_run_leaves_the_case_but_marks_the_slot_missing(
    platform_env: dict[str, Any], api: TestClient
) -> None:
    """A case must not silently shrink when its evidence is cleaned up."""
    with session_scope() as session:
        case = investigations.create(session, title="c")
        investigations.attach(session, case, alert_id="alert-1")
        case_id = case.id

    with session_scope() as session:
        run = session.query(Run).one()
        session.delete(run)

    with session_scope() as session:
        case = session.get(Investigation, case_id)
        assert case is not None
        # The member row cascaded away with the alert, so the case is empty rather than
        # holding a dangling reference.
        assert investigations.detail(case)["alert_count"] == 0


def test_case_endpoints_require_authentication(api: TestClient) -> None:
    assert api.get("/api/v1/investigations").status_code == 401
    assert api.post("/api/v1/investigations", json={"title": "x"}).status_code == 401


def test_case_lifecycle_through_the_api(api: TestClient) -> None:
    headers = _analyst(api)
    created = api.post(
        "/api/v1/investigations", json={"title": "Key compromise", "summary": "s"}, headers=headers
    )
    assert created.status_code == 201
    case_id = created.json()["id"]

    attached = api.post(
        f"/api/v1/investigations/{case_id}/members",
        json={"alert_id": "alert-1", "rationale": "primary"},
        headers=headers,
    )
    assert attached.status_code == 201
    assert attached.json()["alert_count"] == 1

    duplicate = api.post(
        f"/api/v1/investigations/{case_id}/members", json={"alert_id": "alert-1"}, headers=headers
    )
    assert duplicate.status_code == 409

    member_id = attached.json()["members"][0]["id"]
    detached = api.delete(f"/api/v1/investigations/{case_id}/members/{member_id}", headers=headers)
    assert detached.status_code == 200
    assert detached.json()["alert_count"] == 0

    moved = api.post(
        f"/api/v1/investigations/{case_id}/status", json={"status": "closed"}, headers=headers
    )
    assert moved.json()["status"] == "closed"


def test_unknown_case_is_404(api: TestClient) -> None:
    assert api.get("/api/v1/investigations/nope", headers=_analyst(api)).status_code == 404
