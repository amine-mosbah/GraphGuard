"""Tests for the operational surface: health, metrics, and lazy startup.

The startup test is the important one. Running the pipeline in the lifespan hook made every
process — including Celery workers and any container the orchestrator restarted — pay roughly
seven seconds of numerical work before it could answer a healthcheck. That is the kind of
regression that reappears the moment someone "simplifies" the factory, so it is pinned here.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from graphguard.api.app import create_app


def test_startup_does_not_run_the_pipeline(platform_env: dict[str, Any]) -> None:
    """The app must become ready without waiting on a detection run."""
    started = time.perf_counter()
    with TestClient(create_app()) as client:
        boot_seconds = time.perf_counter() - started
        assert client.get("/api/health").status_code == 200
    # A real run measures around 7 s. One second is far below that and far above
    # what an import-and-wire-routes startup needs, so this discriminates cleanly.
    assert boot_seconds < 1.0, f"startup took {boot_seconds:.2f}s — is the pipeline eager again?"


def test_health_reports_the_environment(platform_env: dict[str, Any]) -> None:
    with TestClient(create_app()) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


def test_metrics_endpoint_exposes_graphguard_metrics(platform_env: dict[str, Any]) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for metric in (
        "graphguard_run_duration_seconds",
        "graphguard_runs_total",
        "graphguard_alerts_produced_total",
        "graphguard_http_request_duration_seconds",
    ):
        assert metric in response.text, metric


def test_request_metrics_label_by_route_template_not_by_path(
    platform_env: dict[str, Any],
) -> None:
    """Labelling by concrete path would be unbounded cardinality and leak identifiers."""
    with TestClient(create_app()) as client:
        client.get("/api/v1/alerts/some-made-up-identifier")
        body = client.get("/metrics").text
    assert "some-made-up-identifier" not in body
    assert "/api/v1/alerts/{alert_id}" in body


def test_metrics_are_not_in_the_openapi_schema(platform_env: dict[str, Any]) -> None:
    """An operational endpoint should not appear in the client-facing API contract."""
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
    assert "/metrics" not in schema["paths"]
    assert "/api/health" in schema["paths"]


def test_legacy_endpoints_are_absent_from_the_public_contract(
    platform_env: dict[str, Any],
) -> None:
    """The documented API is /api/v1 only (DECISIONS.md D24).

    The legacy routes carry no authentication, so a generated client that could see them in the
    schema could be pointed at unauthenticated security telemetry by accident. Hiding them makes
    that mistake impossible rather than merely unlikely.
    """
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]

    for legacy in ("/api/run", "/api/alerts", "/api/chains", "/api/graph", "/"):
        assert legacy not in paths, f"{legacy} is still advertised in the OpenAPI schema"

    # The persistent surface must be documented.
    assert any(path.startswith("/api/v1/") for path in paths)
    assert "/api/auth/login" in paths
