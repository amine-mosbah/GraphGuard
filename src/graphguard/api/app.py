"""FastAPI application factory, and the legacy in-memory analysis surface.

Role
----
Builds the application: CORS, metrics, the authentication router and the persistent platform
router (:mod:`graphguard.platform.api_routes`), which together are the product's API.

It also still hosts the **legacy** read-only endpoints under ``/api/`` — ``/api/run``,
``/api/alerts``, ``/api/chains``, ``/api/graph`` — plus a bare triage page at ``/``. These predate
persistence: they serve one in-memory pipeline run, carry no authentication, and mint alert
identifiers that are stable only within a process.

**They are excluded from the OpenAPI schema and are not the product API** (``DECISIONS.md`` D24).
The public contract is ``/api/v1/*``, which is authenticated, persisted and carries triage state.
Hiding them from the schema means a generated client cannot reach them by accident — which matters,
because the legacy routes serve security telemetry with no authentication at all.

They are scheduled for deletion once the dashboard replaces the bare page; the only remaining
consumers are ``tests/test_api.py`` and that page.

Inputs / outputs
----------------
Input: the effective configuration; the legacy pipeline is computed lazily on first access.
Output: JSON under ``/api/v1/*`` (documented) and ``/api/*`` (legacy, undocumented).

Two kinds of finding
--------------------
An **alert** names an entity: one detector signal that cleared ``correlation.alert_threshold``,
ranked by score. A **chain** names a *path* — an external event linked through a shared address to
an internal identity (docs/DESIGN.md §7) — and is served separately at ``/api/chains`` precisely because
it is not another row in the same queue. Alert ids are stable within a server run, not across runs;
persistence arrives with the alert-state workflow.

Ground truth in the payload
---------------------------
Responses carry each alert's ground-truth label because the server runs on a *validation* dataset,
and seeing hits/misses is the point of looking at it. The field is named ``ground_truth`` and is
``None`` when telemetry is unlabelled, so the production shape is already correct.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from graphguard.config import GraphGuardConfig, load_config
from graphguard.correlation import find_attack_chains
from graphguard.detection.base import Signal
from graphguard.eval.runner import RunResult, run_experiment
from graphguard.platform import api_routes, assistant, auth, metrics
from graphguard.platform.settings import get_settings

#: Detector whose signals feed the alert queue by default (the configured method's name).
_METHOD_LABELS = {
    "node_outlier:dominant": "DOMINANT (GNN autoencoder)",
    "node_outlier:egonet": "Egonet baseline (OddBall)",
    "abnormal_subgraph:similarity": "Abnormal subgraph (unison clustering)",
    "community_drift:codrift": "Community drift (co-drift clustering)",
}


@dataclass(frozen=True)
class Alert:
    """One triage-queue entry: a thresholded, ranked, explained signal."""

    alert_id: str
    rank: int
    signal: Signal
    ground_truth: str | None

    def to_summary(self) -> dict[str, Any]:
        """Compact row for the queue listing."""
        return {
            "alert_id": self.alert_id,
            "rank": self.rank,
            "detector": self.signal.detector,
            "node_type": self.signal.node_type,
            "node_id": self.signal.node_id,
            "window_index": self.signal.window.index,
            "window_start": self.signal.window.start.isoformat(),
            "window_end": self.signal.window.end.isoformat(),
            "score": round(self.signal.raw_score, 4),
            "window_score": round(self.signal.normalised_score, 4),
            "ground_truth": self.ground_truth,
        }

    def to_detail(self) -> dict[str, Any]:
        """Full drill-down: summary plus the why and the evidence subgraph nodes."""
        return {
            **self.to_summary(),
            "explanation": self.signal.explain(),
            "reasons": [
                {
                    "feature": reason.feature,
                    "value": reason.value,
                    "peer_median": reason.peer_median,
                    "deviation": round(reason.deviation, 2),
                    "text": reason.text(),
                }
                for reason in self.signal.reasons
            ],
            "evidence": [{"type": t, "id": i} for t, i in self.signal.evidence],
        }


class PipelineState:
    """Everything the API serves, computed once at startup."""

    def __init__(self, cfg: GraphGuardConfig, result: RunResult, elapsed_s: float) -> None:
        """Derive alert queues per method from the raw signals."""
        self.cfg = cfg
        self.result = result
        self.elapsed_s = elapsed_s
        self.alerts_by_method: dict[str, list[Alert]] = {}
        self.alert_index: dict[str, Alert] = {}

        # Cross-layer chains are computed once alongside the alert queues. They are a distinct
        # finding type, not another alert: an alert names an entity, a chain names a *path*.
        self.chains = find_attack_chains(
            list(result.dataset.events), result.signals, cfg.correlation.cross_layer
        )

        truth = result.dataset.ground_truth
        for method, signals in result.signals.items():
            # Cross-window ranking must use raw scores: normalised_score is min-max scaled
            # *within one window*, so every window has a 1.000 top node and a queue sorted by it
            # degenerates into per-window winners regardless of how anomalous they actually are.
            # The threshold still applies to the normalised score — "was this node a standout in
            # its own window" — which is exactly what that number means.
            ranked = sorted(signals, key=lambda s: s.raw_score, reverse=True)
            kept = [s for s in ranked if s.normalised_score >= cfg.correlation.alert_threshold][
                : cfg.correlation.max_alerts
            ]

            alerts: list[Alert] = []
            for rank, signal in enumerate(kept, start=1):
                alert_id = f"{method.split(':')[-1]}-{rank:04d}"
                alert = Alert(
                    alert_id=alert_id,
                    rank=rank,
                    signal=signal,
                    ground_truth=truth.scenario_of(
                        signal.node, signal.window.start, signal.window.end
                    ),
                )
                alerts.append(alert)
                self.alert_index[alert_id] = alert
            self.alerts_by_method[method] = alerts


def create_app(cfg: GraphGuardConfig | None = None, *, eager_pipeline: bool = False) -> FastAPI:
    """Build the application.

    The legacy in-memory pipeline behind the read-only ``/api/*`` endpoints is computed **lazily**,
    on first access, and cached for the life of the process. It used to run in the lifespan hook,
    which meant every process — including one started only to serve the persistent ``/api/v1``
    surface, and every Celery worker importing this module — paid roughly seven seconds of
    numerical work at boot before it could answer anything. In a container that is the difference
    between a healthcheck passing immediately and a deployment that looks stuck.

    Pass ``eager_pipeline=True`` to restore the old behaviour where a warm process matters more
    than a fast start.
    """
    holder: dict[str, PipelineState] = {}

    def pipeline_state() -> PipelineState:
        """Return the cached in-memory run, computing it on first use."""
        state = holder.get("state")
        if state is None:
            started = time.perf_counter()
            effective = cfg or load_config()
            result = run_experiment(effective, save=False)
            state = holder["state"] = PipelineState(
                effective, result, time.perf_counter() - started
            )
        return state

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if eager_pipeline:
            pipeline_state()
        yield

    app = FastAPI(
        title="GraphGuard",
        description="Graph-based security analytics for AI-platform telemetry.",
        version="0.1.0",
        lifespan=lifespan,
    )

    settings = get_settings()

    # The dashboard is served from a different origin in development (Vite on 5173),
    # so the browser needs explicit permission. Origins are enumerated rather than
    # wildcarded because `allow_credentials` with `*` is rejected by browsers and,
    # more to the point, would let any site drive an authenticated session.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Pure-ASGI rather than a FastAPI middleware decorator, so it can read the *matched
    # route template* off the scope. Labelling by concrete path would create one time
    # series per alert id — unbounded cardinality, and identifiers leaked into metrics.
    app.add_middleware(metrics.PrometheusMiddleware)

    app.include_router(auth.router)
    app.include_router(api_routes.router)
    app.include_router(assistant.router)

    # Named `metrics_endpoint`, not `metrics`: a local `def metrics` would shadow the
    # imported module for the whole of create_app, making the middleware registration
    # above fail with an UnboundLocalError.
    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> PlainTextResponse:
        """Expose Prometheus metrics.

        GraphGuard is a monitoring system, so instrumenting its own pipeline is not an
        afterthought — run duration and alert counts are exactly the signals an operator would
        want if this were watching someone else's service.
        """
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe for the reverse proxy and the container orchestrator."""
        return {"status": "ok", "environment": settings.environment}

    # ------------------------------------------------------------------ JSON API

    @app.get("/api/run", include_in_schema=False)
    def run_info() -> dict[str, Any]:
        """Dataset, methods and evaluation metrics for the run being served."""
        state: PipelineState = pipeline_state()
        return {
            "dataset": state.result.dataset.summary(),
            "pipeline_seconds": round(state.elapsed_s, 1),
            "alert_threshold": state.cfg.correlation.alert_threshold,
            "methods": {
                method: {
                    "label": _METHOD_LABELS.get(method, method),
                    "alerts": len(alerts),
                }
                for method, alerts in state.alerts_by_method.items()
            },
            "metrics": state.result.comparison.to_dict(),
        }

    @app.get("/api/alerts", include_in_schema=False)
    def list_alerts(method: str | None = None) -> dict[str, Any]:
        """Return the ranked alert queue for one method (default: the configured detector)."""
        state: PipelineState = pipeline_state()
        method = method or state.cfg.detection.node_outlier.method.replace(
            "pygod:", "node_outlier:"
        ).replace("baseline:", "node_outlier:")
        if method not in state.alerts_by_method:
            raise HTTPException(
                404,
                f"unknown method {method!r}; available: {sorted(state.alerts_by_method)}",
            )
        return {
            "method": method,
            "alerts": [a.to_summary() for a in state.alerts_by_method[method]],
        }

    @app.get("/api/alerts/{alert_id}", include_in_schema=False)
    def alert_detail(alert_id: str) -> dict[str, Any]:
        """One alert with its explanation and evidence subgraph nodes."""
        state: PipelineState = pipeline_state()
        alert = state.alert_index.get(alert_id)
        if alert is None:
            raise HTTPException(404, f"no alert {alert_id!r}")
        return alert.to_detail()

    @app.get("/api/chains", include_in_schema=False)
    def attack_chains() -> dict[str, Any]:
        """Cross-layer attack chains: findings whose evidence is a path, not an event."""
        state: PipelineState = pipeline_state()
        truth = state.result.dataset.ground_truth
        return {
            "chains": [
                {
                    **chain.to_dict(),
                    "ground_truth": truth.scenario_of(
                        ("ip", chain.external_ip),
                        state.result.dataset.events[0].timestamp,
                        state.result.dataset.events[-1].timestamp,
                    ),
                }
                for chain in state.chains
            ]
        }

    @app.get("/api/graph", include_in_schema=False)
    def graph_summary() -> dict[str, Any]:
        """Per-window node/edge counts — the graph the detectors actually saw."""
        state: PipelineState = pipeline_state()
        # Snapshots are rebuilt cheaply from the dataset already in memory; the builder is
        # deterministic, so this matches what the detectors scored.
        from graphguard.graph import GraphBuilder

        snapshots = GraphBuilder(state.cfg.graph).build_snapshots(list(state.result.dataset.events))
        return {"windows": [s.summary() for s in snapshots]}

    # ------------------------------------------------------------------ triage page

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        """Minimal triage page: alert queue on the left, drill-down on the right."""
        return _PAGE

    return app


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GraphGuard — triage</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; --accent:#c33; --muted:#888; --line:#8884; }
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin:0; }
  header { padding:.7rem 1.2rem; border-bottom:1px solid var(--line);
           display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:1.05rem; margin:0; }
  header .sub { color:var(--muted); font-size:.85rem; }
  main { display:grid; grid-template-columns: minmax(340px,1fr) 1.2fr; gap:0; }
  #queue { border-right:1px solid var(--line); max-height:calc(100vh - 60px); overflow:auto; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:.45rem .8rem; border-bottom:1px solid var(--line); }
  th { position:sticky; top:0; background:Canvas; font-size:.78rem;
       text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:#8881; }
  tbody tr.active { background:#8882; }
  .chip { display:inline-block; padding:0 .45rem; border-radius:.6rem; font-size:.75rem;
          border:1px solid var(--line); color:var(--muted); }
  .chip.attack { border-color:var(--accent); color:var(--accent); font-weight:600; }
  .score { font-variant-numeric: tabular-nums; }
  #detail { padding:1rem 1.4rem; max-height:calc(100vh - 60px); overflow:auto; }
  #detail h2 { font-size:1rem; margin:.2rem 0 .6rem; }
  #detail pre { white-space:pre-wrap; background:#8881; padding:.8rem; border-radius:.5rem; }
  .evidence { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:.5rem; }
  .placeholder { color:var(--muted); padding:2rem; }
  .chain { border:1px solid var(--accent); border-radius:.5rem; padding:.7rem .9rem;
           margin-bottom:.8rem; }
  .chain ol { margin:.5rem 0 0; padding-left:1.2rem; }
  .chain li { margin:.25rem 0; }
  .stage { font-weight:600; }
  .sub { color:var(--muted); font-size:.78rem; }
  select { font:inherit; }
  footer { padding:.5rem 1.2rem; color:var(--muted); font-size:.78rem;
           border-top:1px solid var(--line); }
</style>
</head>
<body>
<header>
  <h1>GraphGuard</h1>
  <span class="sub">ranked alert queue — minimal preview (full dashboard: weeks 9–10)</span>
  <label style="margin-left:auto">detector
    <select id="method"></select>
  </label>
  <span class="sub" id="runinfo"></span>
</header>
<main>
  <div id="queue"><div class="placeholder">loading…</div></div>
  <div id="detail">
    <div id="chains"></div>
    <div class="placeholder">select an alert to see why it fired</div>
  </div>
</main>
<footer>
  Scores rank entities for human review; a high score is not a verdict.
  “truth” chips appear because this server runs on a labelled validation dataset.
</footer>
<script>
const queueEl = document.getElementById('queue');
const detailEl = document.getElementById('detail');
const methodEl = document.getElementById('method');

async function j(url) { const r = await fetch(url); if (!r.ok) throw new Error(await r.text()); return r.json(); }

async function boot() {
  const run = await j('/api/run');
  loadChains();
  const methods = Object.keys(run.methods);
  methodEl.innerHTML = methods.map(m =>
    `<option value="${m}">${run.methods[m].label} (${run.methods[m].alerts})</option>`).join('');
  const ds = run.dataset;
  document.getElementById('runinfo').textContent =
    `${ds.events.toLocaleString()} events · ${ds.attack_events.toLocaleString()} attack · pipeline ${run.pipeline_seconds}s`;
  methodEl.onchange = () => loadQueue(methodEl.value);
  await loadQueue(methods[0]);
}

async function loadQueue(method) {
  const data = await j('/api/alerts?method=' + encodeURIComponent(method));
  if (!data.alerts.length) { queueEl.innerHTML = '<div class="placeholder">no alerts cleared the threshold</div>'; return; }
  queueEl.innerHTML = `<table><thead><tr>
      <th>#</th><th>entity</th><th>window</th><th>score</th><th>truth</th>
    </tr></thead><tbody>` + data.alerts.map(a => `
      <tr data-id="${a.alert_id}">
        <td>${a.rank}</td>
        <td><code>${a.node_type}:${a.node_id}</code></td>
        <td>${a.window_index}</td>
        <td class="score">${a.score.toFixed(3)}</td>
        <td>${a.ground_truth
              ? `<span class="chip attack">${a.ground_truth}</span>`
              : '<span class="chip">benign</span>'}</td>
      </tr>`).join('') + '</tbody></table>';
  queueEl.querySelectorAll('tbody tr').forEach(tr =>
    tr.onclick = () => { queueEl.querySelectorAll('tr.active').forEach(x => x.classList.remove('active'));
                         tr.classList.add('active'); loadDetail(tr.dataset.id); });
}

async function loadChains() {
  const data = await j('/api/chains');
  const el = document.getElementById('chains');
  if (!data.chains.length) { el.innerHTML = ''; return; }
  el.innerHTML = '<h2>Cross-layer attack chains</h2>' + data.chains.map(c => `
    <div class="chain">
      <div><strong>${c.external_ip}</strong> &rarr; <strong>${c.api_key}</strong>
        <span class="chip attack">confidence ${c.score.toFixed(2)}</span>
        ${c.ground_truth ? `<span class="chip attack">${c.ground_truth}</span>` : '<span class="chip">unlabelled</span>'}</div>
      <ol>${c.steps.map(s => `<li><span class="stage">${s.stage}</span> &mdash; ${s.detail}
        <span class="sub">${s.at}</span></li>`).join('')}</ol>
    </div>`).join('');
}

async function loadDetail(id) {
  const a = await j('/api/alerts/' + id);
  detailEl.innerHTML = `
    <h2>${a.alert_id} — <code>${a.node_type}:${a.node_id}</code>
        ${a.ground_truth ? `<span class="chip attack">${a.ground_truth}</span>` : '<span class="chip">benign</span>'}</h2>
    <div class="sub">window ${a.window_index}: ${a.window_start} → ${a.window_end}
         · raw score ${a.score.toFixed(3)} (window-relative ${a.window_score.toFixed(2)}) · ${a.detector}</div>
    <h3>Why</h3>
    <pre>${a.explanation}</pre>
    <h3>Evidence subgraph (${a.evidence.length} neighbours)</h3>
    <div class="evidence">${a.evidence.map(e => `<span class="chip">${e.type}:${e.id}</span>`).join('')}</div>`;
}

boot().catch(e => { queueEl.innerHTML = '<div class="placeholder">failed: ' + e.message + '</div>'; });
</script>
</body>
</html>
"""


#: Module-level app for ``uvicorn graphguard.api.app:app``.
app = create_app()
