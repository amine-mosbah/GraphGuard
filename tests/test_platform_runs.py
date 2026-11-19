"""Tests for run persistence, the triage workflow and the platform API routes.

The pipeline is exercised for real on a deliberately small dataset rather than stubbed: the point
of this layer is that it faithfully materialises what the detectors produced, which a stub cannot
verify.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from graphguard.config import (
    BenignConfig,
    GraphGuardConfig,
    NodeOutlierConfig,
    PyGODConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.platform import api_routes, auth, security, triage
from graphguard.platform.db import session_scope
from graphguard.platform.models import Alert, AlertState, Role, Run, RunStatus, User
from graphguard.platform.runs import build_timeline, create_run, execute_run
from graphguard.platform.triage import TransitionError


def small_config() -> GraphGuardConfig:
    """A configuration small enough to run in a test, but still producing real signals."""
    cfg = GraphGuardConfig(
        run=RunConfig(seed=7),
        simulator=SimulatorConfig(
            start=datetime(2026, 3, 2, tzinfo=UTC),
            days=6,
            benign=BenignConfig(
                n_users=15,
                persona_mix={"researcher": 0.4, "app_developer": 0.4, "casual": 0.2},
            ),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(enabled=True, n_victims=1, onset_fraction=0.5),
                slow_group_drift=SlowGroupDriftConfig(enabled=False),
            ),
        ),
    )
    cfg.detection.node_outlier = NodeOutlierConfig(
        method="pygod:dominant",
        pygod=PyGODConfig(epochs=15, hid_dim=16),
        contamination=0.1,
        score_node_types=["api_key"],
    )
    return cfg


@pytest.fixture(scope="function")
def executed_run(platform_env: dict[str, Any]) -> str:
    """A completed run, persisted. Returns its id."""
    cfg = small_config()
    with session_scope() as session:
        run = create_run(session, cfg, name="test run")
        execute_run(session, run)
        return run.id


# --------------------------------------------------------------------------- persistence


def test_a_run_persists_as_succeeded(executed_run: str) -> None:
    with session_scope() as session:
        run = session.get(Run, executed_run)
        assert run is not None
        assert run.status is RunStatus.SUCCEEDED
        assert run.event_count > 0
        assert run.finished_at is not None and run.started_at is not None
        assert run.error is None


def test_the_config_travels_with_the_run(executed_run: str) -> None:
    """D4: a run is reproducible from its own artifacts, not from a file that may have moved on."""
    with session_scope() as session:
        run = session.get(Run, executed_run)
        assert run is not None
        assert run.config["run"]["seed"] == 7
        # It must round-trip back into a usable configuration object.
        assert GraphGuardConfig.model_validate(run.config).run.seed == 7


def test_alerts_are_materialised_with_their_evidence(executed_run: str) -> None:
    with session_scope() as session:
        alerts = list(session.query(Alert).filter_by(run_id=executed_run))
        assert alerts, "the run produced no alerts"
        assert all(a.state is AlertState.NEW for a in alerts)
        assert all(a.explanation for a in alerts)
        # Explanations must be stored, not regenerated, so an analyst's judgement stays
        # attached to the evidence they actually saw.
        with_reasons = [a for a in alerts if a.reasons]
        assert with_reasons, "no alert carried contributing factors"
        reason = with_reasons[0].reasons[0]
        assert {"feature", "value", "peer_median", "deviation", "text"} <= set(reason)


def test_only_signals_clearing_the_threshold_are_kept(executed_run: str) -> None:
    """Queue membership is frozen at write time, so the queue an analyst works is the one written.

    Ordering does not need to be stored: it is a deterministic function of `raw_score`, which is.
    What *is* a property of the run, and therefore persisted, is which signals made the cut.
    """
    with session_scope() as session:
        run = session.get(Run, executed_run)
        assert run is not None
        threshold = GraphGuardConfig.model_validate(run.config).correlation.alert_threshold
        alerts = list(session.query(Alert).filter_by(run_id=executed_run))
        assert alerts
        assert all(a.normalised_score >= threshold for a in alerts)


def test_a_timeline_is_precomputed(executed_run: str) -> None:
    with session_scope() as session:
        run = session.get(Run, executed_run)
        assert run is not None
        assert run.timeline, "no timeline buckets were stored"
        bucket = run.timeline[0]
        assert {"at", "total", "by_layer", "errors", "refusals"} <= set(bucket)
        assert sum(b["total"] for b in run.timeline) == run.event_count


def test_a_failed_run_records_its_error(platform_env: dict[str, Any]) -> None:
    """A broken run must be diagnosable from the API, not only from worker logs."""
    with session_scope() as session:
        run = create_run(session, small_config(), name="doomed")
        run.config = {"nonsense": True}
        run_id = run.id
    with pytest.raises(ValidationError), session_scope() as session:
        execute_run(session, session.get(Run, run_id))  # type: ignore[arg-type]
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status is RunStatus.FAILED
        assert run.error


def test_build_timeline_on_no_events_is_empty() -> None:
    class _Empty:
        class dataset:  # noqa: D106,N801
            events: list[Any] = []

    assert build_timeline(_Empty) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- triage rules


def test_legal_transition_records_history(executed_run: str) -> None:
    with session_scope() as session:
        alert = session.query(Alert).filter_by(run_id=executed_run).first()
        assert alert is not None
        triage.transition(session, alert, AlertState.INVESTIGATING, actor=None, rationale="looking")
        assert alert.state is AlertState.INVESTIGATING
        changes = triage.history(session, alert)
        assert len(changes) == 1
        assert changes[0].from_state is AlertState.NEW
        assert changes[0].to_state is AlertState.INVESTIGATING
        assert changes[0].rationale == "looking"


def test_illegal_transition_is_refused(executed_run: str) -> None:
    """new -> true_positive skips investigation, so it is not allowed."""
    with session_scope() as session:
        alert = session.query(Alert).filter_by(run_id=executed_run).first()
        assert alert is not None
        with pytest.raises(TransitionError, match="cannot move"):
            triage.transition(session, alert, AlertState.TRUE_POSITIVE, actor=None)


def test_transition_to_the_same_state_is_refused(executed_run: str) -> None:
    with session_scope() as session:
        alert = session.query(Alert).filter_by(run_id=executed_run).first()
        assert alert is not None
        with pytest.raises(TransitionError, match="already"):
            triage.transition(session, alert, AlertState.NEW, actor=None)


def test_a_verdict_can_be_reopened_without_erasing_it(executed_run: str) -> None:
    """A correction must be visible as a correction, not as a rewrite of history."""
    with session_scope() as session:
        alert = session.query(Alert).filter_by(run_id=executed_run).first()
        assert alert is not None
        triage.transition(session, alert, AlertState.INVESTIGATING, actor=None)
        triage.transition(session, alert, AlertState.FALSE_POSITIVE, actor=None)
        triage.transition(session, alert, AlertState.INVESTIGATING, actor=None, rationale="wrong")
        states = [c.to_state for c in triage.history(session, alert)]
        assert states == [
            AlertState.INVESTIGATING,
            AlertState.FALSE_POSITIVE,
            AlertState.INVESTIGATING,
        ]


# --------------------------------------------------------------------------- API


@pytest.fixture
def api(platform_env: dict[str, Any]) -> TestClient:
    """A client with the platform routes mounted and two accounts provisioned."""
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
    return TestClient(app)


def _token(api: TestClient, email: str, password: str) -> dict[str, str]:
    body = api.post("/api/auth/login", json={"email": email, "password": password}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_platform_routes_require_authentication(api: TestClient) -> None:
    for path in ("/api/v1/runs", "/api/v1/alerts", "/api/v1/stats", "/api/v1/config"):
        assert api.get(path).status_code == 401, path


def test_only_an_admin_may_start_a_run(api: TestClient) -> None:
    analyst = _token(api, "analyst@eniso.tn", "analyst-password-01")
    assert api.post("/api/v1/runs", json={"name": "x"}, headers=analyst).status_code == 403


def test_an_analyst_can_read_the_queue(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    body = api.get("/api/v1/alerts", headers=headers).json()
    assert body["total"] > 0
    assert body["alerts"][0]["state"] == "new"


def test_the_queue_can_be_filtered_to_open_alerts(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    alert_id = api.get("/api/v1/alerts", headers=headers).json()["alerts"][0]["id"]
    api.post(
        f"/api/v1/alerts/{alert_id}/transition",
        json={"state": "investigating"},
        headers=headers,
    )
    api.post(
        f"/api/v1/alerts/{alert_id}/transition",
        json={"state": "false_positive", "rationale": "known batch job"},
        headers=headers,
    )
    open_ids = {
        a["id"] for a in api.get("/api/v1/alerts?open_only=true", headers=headers).json()["alerts"]
    }
    assert alert_id not in open_ids


def test_alert_detail_carries_explanation_and_allowed_moves(
    api: TestClient, executed_run: str
) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    alert_id = api.get("/api/v1/alerts", headers=headers).json()["alerts"][0]["id"]
    body = api.get(f"/api/v1/alerts/{alert_id}", headers=headers).json()
    assert body["explanation"]
    assert body["allowed_transitions"] == ["false_positive", "investigating"]
    assert body["history"] == []


def test_an_illegal_transition_returns_409(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    alert_id = api.get("/api/v1/alerts", headers=headers).json()["alerts"][0]["id"]
    response = api.post(
        f"/api/v1/alerts/{alert_id}/transition", json={"state": "exported"}, headers=headers
    )
    assert response.status_code == 409


def test_only_a_confirmed_alert_can_be_exported(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    alert_id = api.get("/api/v1/alerts", headers=headers).json()["alerts"][0]["id"]
    assert api.post(f"/api/v1/alerts/{alert_id}/export", headers=headers).status_code == 409

    api.post(
        f"/api/v1/alerts/{alert_id}/transition", json={"state": "investigating"}, headers=headers
    )
    api.post(
        f"/api/v1/alerts/{alert_id}/transition", json={"state": "true_positive"}, headers=headers
    )
    export = api.post(f"/api/v1/alerts/{alert_id}/export", headers=headers)
    assert export.status_code == 200
    document = export.json()
    assert document["incident"]["alert_id"] == alert_id
    assert document["provenance"]["exported_by"] == "analyst@eniso.tn"
    assert document["audit_trail"]

    # Exporting moves the alert on, so the queue records that it left the system.
    after = api.get(f"/api/v1/alerts/{alert_id}", headers=headers).json()
    assert after["state"] == "exported"


def test_notes_can_be_attached(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    alert_id = api.get("/api/v1/alerts", headers=headers).json()["alerts"][0]["id"]
    created = api.post(
        f"/api/v1/alerts/{alert_id}/notes", json={"body": "matches last week"}, headers=headers
    )
    assert created.status_code == 201
    detail = api.get(f"/api/v1/alerts/{alert_id}", headers=headers).json()
    assert detail["notes"][0]["body"] == "matches last week"


def test_timeline_endpoint_returns_buckets(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    body = api.get(f"/api/v1/runs/{executed_run}/timeline", headers=headers).json()
    assert body["buckets"]
    assert body["buckets"][0]["total"] > 0


def test_stats_summarise_the_platform(api: TestClient, executed_run: str) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    body = api.get("/api/v1/stats", headers=headers).json()
    assert body["runs"] == 1
    assert body["alerts"] > 0
    assert body["open_alerts"] == body["alerts"]
    assert body["last_run"]["status"] == "succeeded"


def test_config_is_readable_by_an_analyst_but_validation_is_admin_only(api: TestClient) -> None:
    analyst = _token(api, "analyst@eniso.tn", "analyst-password-01")
    admin = _token(api, "admin@eniso.tn", "admin-password-0001")
    assert api.get("/api/v1/config", headers=analyst).status_code == 200
    # The endpoint returns an envelope — the configuration plus which saved revision it
    # came from — so the configuration itself has to be unwrapped before validating it.
    payload = api.get("/api/v1/config", headers=analyst).json()["config"]
    assert api.put("/api/v1/config/validate", json=payload, headers=analyst).status_code == 403
    assert api.put("/api/v1/config/validate", json=payload, headers=admin).status_code == 200


def test_invalid_config_is_rejected_with_422(api: TestClient) -> None:
    admin = _token(api, "admin@eniso.tn", "admin-password-0001")
    response = api.put("/api/v1/config/validate", json={"run": {"seed": "nope"}}, headers=admin)
    assert response.status_code == 422


def test_unknown_ids_are_404(api: TestClient) -> None:
    headers = _token(api, "analyst@eniso.tn", "analyst-password-01")
    assert api.get("/api/v1/runs/nope", headers=headers).status_code == 404
    assert api.get("/api/v1/alerts/nope", headers=headers).status_code == 404


# --------------------------------------------------------------------------- jobs


def test_dispatch_runs_eagerly_in_test_environment(platform_env: dict[str, Any]) -> None:
    """Eager mode runs the same task function the worker would, with no broker.

    That matters more than it looks: a test that stubbed the task out would prove nothing about
    the code the worker actually executes.
    """
    from graphguard.platform.jobs import build_celery_app, execute_run_task

    app = build_celery_app(eager=True)
    assert app.conf.task_always_eager

    with session_scope() as session:
        run = create_run(session, small_config(), name="dispatched")
        run_id = run.id

    execute_run_task.apply(args=(run_id,))

    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert run.status is RunStatus.SUCCEEDED
        assert session.query(Alert).filter_by(run_id=run_id).count() > 0


def test_a_redelivered_task_does_not_rerun_a_finished_run(executed_run: str) -> None:
    """Celery may redeliver a message; re-executing would duplicate alerts and reset triage."""
    from graphguard.platform.jobs import execute_run_task

    with session_scope() as session:
        before = session.query(Alert).filter_by(run_id=executed_run).count()

    result = execute_run_task.apply(args=(executed_run,)).get()
    assert result["skipped"] is True

    with session_scope() as session:
        assert session.query(Alert).filter_by(run_id=executed_run).count() == before


def test_dispatch_on_an_unknown_run_raises(platform_env: dict[str, Any]) -> None:
    from graphguard.platform.jobs import execute_run_task

    with pytest.raises(ValueError, match="no such run"):
        execute_run_task.apply(args=("nonexistent",)).get()


def test_persisted_alerts_carry_framework_tags(executed_run: str) -> None:
    """Alerts carry the threat-model taxonomy tags for their detector, per config.

    The report (Ch.1 §1.6, Ch.4 §4.3.7) promises findings arrive tagged against MITRE ATLAS and
    the OWASP LLM Top 10; this pins the promise to the persisted row rather than to prose. The
    mapping is docs/THREAT_MODEL.md's, and an empty `tag_frameworks` must disable tagging.
    """
    from graphguard.correlation.tags import DETECTOR_TAGS, tags_for

    with session_scope() as session:
        run = session.get(Run, executed_run)
        assert run is not None
        frameworks = GraphGuardConfig.model_validate(run.config).correlation.tag_frameworks
        assert frameworks, "the default config must enable both frameworks"
        alerts = list(session.query(Alert).filter_by(run_id=executed_run))
        assert alerts
        tagged = [a for a in alerts if a.tags]
        assert tagged, "no persisted alert carried a framework tag"
        for alert in alerts:
            family = alert.detector.split(":", 1)[0]
            assert alert.tags == tags_for(alert.detector, frameworks), (
                alert.detector,
                alert.tags,
            )
            assert set(alert.tags) <= set(DETECTOR_TAGS.get(family, ()))

    assert tags_for("node_outlier:egonet", []) == []
