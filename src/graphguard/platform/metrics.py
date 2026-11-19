"""Prometheus instrumentation for the platform.

Role
----
Defines the metrics GraphGuard exposes about itself and the middleware that records HTTP traffic.

Why this is more than box-ticking
---------------------------------
GraphGuard is a monitoring system. A monitoring system that cannot be monitored is a poor
advertisement for its own thesis, and the reflexive case is genuinely useful: the questions an
operator would ask of GraphGuard are the same ones GraphGuard asks of the platform it watches —
is the volume normal, are failures rising, is anything taking longer than it used to.

What is measured, and why each one
----------------------------------
- **Pipeline run duration.** The single number that decides whether runs can ever return to the
  request path. It measured 7.1 s on the development machine; a histogram says whether that holds
  as telemetry grows.
- **Runs by outcome.** A rising failure count is invisible from the alert queue, because a run
  that never completes simply produces no alerts — which looks exactly like a quiet day.
- **Alerts produced, by detector.** A detector that suddenly stops contributing is a
  misconfiguration; without a per-detector count the total hides it.
- **Request latency and status by route.** Ordinary service health.

Deliberately *not* measured: anything carrying an entity identifier. Prometheus labels become time
series, so labelling by API key or IP would both explode cardinality and copy security telemetry
into a metrics store with a different access-control model from the database.

Inputs / outputs
----------------
Input: pipeline events and HTTP requests.
Output: the Prometheus text exposition served at ``/metrics``.
"""

from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RUN_DURATION = Histogram(
    "graphguard_run_duration_seconds",
    "Wall-clock duration of a full detection pipeline run.",
    # Buckets straddle the measured 7.1 s so a regression past ~10 s is visible
    # immediately rather than being absorbed into a catch-all bucket.
    buckets=(1, 2.5, 5, 7.5, 10, 15, 30, 60, 120, 300),
)

RUNS_TOTAL = Counter(
    "graphguard_runs_total",
    "Pipeline runs that reached a terminal state.",
    labelnames=("status",),
)

ALERTS_PRODUCED = Counter(
    "graphguard_alerts_produced_total",
    "Alerts written by a completed run.",
    labelnames=("detector",),
)

CHAINS_PRODUCED = Counter(
    "graphguard_attack_chains_produced_total",
    "Cross-layer attack chains found by a completed run.",
)

EVENTS_INGESTED = Counter(
    "graphguard_events_ingested_total",
    "Telemetry events processed by completed runs.",
)

ALERT_TRANSITIONS = Counter(
    "graphguard_alert_transitions_total",
    "Analyst triage decisions.",
    labelnames=("to_state",),
)

RUNS_IN_PROGRESS = Gauge(
    "graphguard_runs_in_progress",
    "Pipeline runs currently executing.",
)

REQUEST_DURATION = Histogram(
    "graphguard_http_request_duration_seconds",
    "HTTP request latency.",
    labelnames=("method", "route", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


class PrometheusMiddleware:
    """Record duration and status for every HTTP request.

    Labels use the *route template* (``/api/v1/alerts/{alert_id}``) rather than the concrete path.
    Labelling by concrete path would create one time series per alert identifier, which is both an
    unbounded-cardinality bug and a leak of identifiers into the metrics store.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Time the request and record the outcome."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        # Defaults to 500: if the application raises before sending a response
        # start message, the request did fail, and recording it as anything else
        # would hide errors from the very dashboard meant to surface them.
        status_code = "500"

        async def _send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = str(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            REQUEST_DURATION.labels(
                method=scope.get("method", "GET"), route=template, status=status_code
            ).observe(time.perf_counter() - started)
