"""Tests for the FastAPI service.

Runs the real pipeline on a deliberately small dataset — the API's value is that it serves real
pipeline output, so stubbing the pipeline out would test nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from graphguard.api.app import create_app
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


@pytest.fixture(scope="module")
def client() -> TestClient:
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
                # Drift off: this population is too small to host a cohort, and these tests
                # exercise API mechanics rather than detection quality.
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
    with TestClient(create_app(cfg)) as test_client:  # `with` triggers the lifespan pipeline run
        yield test_client


def test_run_info_reports_dataset_and_both_methods(client: TestClient) -> None:
    payload = client.get("/api/run").json()
    assert payload["dataset"]["events"] > 0
    assert payload["dataset"]["attack_events"] > 0
    assert set(payload["methods"]) == {
        "node_outlier:egonet",
        "node_outlier:dominant",
        "abnormal_subgraph:similarity",
        "community_drift:codrift",
    }
    assert set(payload["metrics"]["pooled"]) >= {"node_outlier:egonet", "node_outlier:dominant"}
    assert "stolen_key" in payload["metrics"]["per_scenario"]["node_outlier:egonet"]


def test_alert_queue_is_ranked_by_raw_score_across_windows(client: TestClient) -> None:
    """The queue must not degenerate into per-window winners (each window has a 1.0 top node)."""
    alerts = client.get("/api/alerts?method=node_outlier:egonet").json()["alerts"]
    assert alerts
    scores = [a["score"] for a in alerts]
    assert scores == sorted(scores, reverse=True)


def test_default_method_is_the_configured_detector(client: TestClient) -> None:
    payload = client.get("/api/alerts").json()
    assert payload["method"] == "node_outlier:dominant"


def test_alert_detail_carries_explanation_and_evidence(client: TestClient) -> None:
    alerts = client.get("/api/alerts?method=node_outlier:dominant").json()["alerts"]
    detail = client.get(f"/api/alerts/{alerts[0]['alert_id']}").json()
    assert detail["explanation"]
    assert isinstance(detail["evidence"], list)
    assert {"type", "id"} <= set(detail["evidence"][0])


def test_unknown_alert_and_method_return_404(client: TestClient) -> None:
    assert client.get("/api/alerts/nope-9999").status_code == 404
    assert client.get("/api/alerts?method=nope").status_code == 404


def test_graph_summary_reports_windows(client: TestClient) -> None:
    windows = client.get("/api/graph").json()["windows"]
    assert len(windows) == 6
    assert all("nodes" in w and "edges" in w for w in windows)


def test_triage_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "GraphGuard" in response.text
