"""Materialising a pipeline run into persistent rows.

Role
----
The bridge between the analytics core and the platform. :func:`execute_run` runs the pipeline
exactly as the CLI does, then writes what an analyst needs to keep: the run itself, its ranked
alerts, and any cross-layer attack chains.

Why alerts are copied rather than referenced
--------------------------------------------
Every score, explanation and contributing factor is written out verbatim rather than recomputed on
read. That is deliberate. An analyst who marks an alert a false positive is recording a judgement
about *specific evidence*; if a later configuration change altered what that alert says, the audit
trail would claim they judged something they never saw. Storing the evidence freezes it against
the decision.

Ranking is reproduced here rather than imported from the API layer, because ranking is a property
of the run — the queue an analyst worked is the queue that was written, not one recomputed with
today's threshold.

Inputs / outputs
----------------
Input: a :class:`~graphguard.config.GraphGuardConfig` and an optional owning user.
Output: a ``runs`` row with its ``alerts`` and ``attack_chains``, committed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from graphguard.config import GraphGuardConfig
from graphguard.correlation import find_attack_chains, tags_for
from graphguard.detection.base import Signal
from graphguard.eval.runner import RunResult, run_experiment
from graphguard.graph import OWNS, USED_FROM, GraphBuilder, NodeType
from graphguard.platform import metrics
from graphguard.platform.models import Alert, AlertState, AttackChainRecord, Run, RunStatus
from graphguard.schema import Attr, Outcome, TraceEvent
from graphguard.simulator.labels import GroundTruth

logger = logging.getLogger(__name__)


def create_run(
    session: Session,
    cfg: GraphGuardConfig,
    *,
    name: str,
    created_by_id: str | None = None,
) -> Run:
    """Record a queued run, snapshotting the configuration it will use.

    The config is stored now, at dispatch, rather than read from disk when the worker starts. D4
    requires a run to be reproducible from its own artifacts; if the worker re-read
    ``config/default.yaml`` an edit between queuing and execution would silently change what ran.
    """
    run = Run(
        name=name,
        status=RunStatus.QUEUED,
        config=cfg.model_dump(mode="json"),
        created_by_id=created_by_id,
    )
    session.add(run)
    session.flush()
    return run


def execute_run(session: Session, run: Run) -> Run:
    """Run the pipeline for ``run`` and persist its output.

    Failures are recorded on the row rather than raised, so a broken run is diagnosable from the
    API instead of only from worker logs. The exception is re-raised afterwards so the job system
    also sees it.
    """
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    session.flush()

    started = time.perf_counter()
    metrics.RUNS_IN_PROGRESS.inc()
    try:
        cfg = GraphGuardConfig.model_validate(run.config)
        result = run_experiment(cfg, save=False)
    except Exception as exc:
        metrics.RUNS_IN_PROGRESS.dec()
        metrics.RUNS_TOTAL.labels(status=RunStatus.FAILED.value).inc()
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(UTC)
        # Commit, not flush. The caller's transaction is about to be rolled back by the
        # exception propagating out of `session_scope`, and a rollback would discard the
        # failure record along with the partial work — leaving the run stuck at `queued`
        # forever with no indication anything went wrong. Committing the failure state
        # first makes it survive the rollback.
        session.commit()
        logger.exception("run %s failed", run.id)
        raise

    alerts = _persist_alerts(session, run, cfg, result)
    chains = _persist_chains(session, run, cfg, result)

    run.timeline = build_timeline(result)
    run.graph = build_network_graph(cfg, result, alerts)
    run.event_count = len(result.dataset.events)
    run.metrics = result.comparison.to_dict()
    run.status = RunStatus.SUCCEEDED
    run.finished_at = datetime.now(UTC)
    session.flush()

    metrics.RUNS_IN_PROGRESS.dec()
    metrics.RUN_DURATION.observe(time.perf_counter() - started)
    metrics.RUNS_TOTAL.labels(status=RunStatus.SUCCEEDED.value).inc()
    metrics.EVENTS_INGESTED.inc(run.event_count)
    metrics.CHAINS_PRODUCED.inc(len(chains))
    for detector in {alert.detector for alert in alerts}:
        metrics.ALERTS_PRODUCED.labels(detector=detector).inc(
            sum(1 for alert in alerts if alert.detector == detector)
        )
    return run


def _persist_alerts(
    session: Session, run: Run, cfg: GraphGuardConfig, result: RunResult
) -> list[Alert]:
    """Write the ranked, thresholded queue for every detector that produced signals."""
    truth: GroundTruth = result.dataset.ground_truth
    rows: list[Alert] = []

    for method, signals in result.signals.items():
        for signal in _rank(signals, cfg):
            rows.append(
                Alert(
                    run_id=run.id,
                    detector=method,
                    entity_type=signal.node_type,
                    entity_id=signal.node_id,
                    window_index=signal.window.index,
                    window_start=signal.window.start,
                    window_end=signal.window.end,
                    raw_score=signal.raw_score,
                    normalised_score=signal.normalised_score,
                    explanation=signal.explain(),
                    reasons=[
                        {
                            "feature": reason.feature,
                            "value": reason.value,
                            "peer_median": reason.peer_median,
                            "deviation": round(reason.deviation, 4),
                            "text": reason.text(),
                        }
                        for reason in signal.reasons
                    ],
                    evidence=[
                        {"type": node_type, "id": node_id} for node_type, node_id in signal.evidence
                    ],
                    tags=tags_for(method, cfg.correlation.tag_frameworks),
                    ground_truth=truth.scenario_of(
                        signal.node, signal.window.start, signal.window.end
                    ),
                    state=AlertState.NEW,
                )
            )

    session.add_all(rows)
    session.flush()
    return rows


def _rank(signals: list[Signal], cfg: GraphGuardConfig) -> list[Signal]:
    """Order and cut one detector's signals into the queue an analyst sees.

    Sorting is on the raw score and thresholding on the normalised one, and the two are not
    interchangeable. ``normalised_score`` is min-max scaled *within a window*, so every window
    contains a 1.0 top node; sorting by it would produce a queue of per-window winners regardless
    of how anomalous any of them actually are. The threshold, in contrast, is asking exactly the
    question normalisation answers — "did this node stand out among its own contemporaries" — so
    that is where the normalised value belongs.
    """
    ranked = sorted(signals, key=lambda s: s.raw_score, reverse=True)
    kept = [s for s in ranked if s.normalised_score >= cfg.correlation.alert_threshold]
    return kept[: cfg.correlation.max_alerts]


def _persist_chains(
    session: Session, run: Run, cfg: GraphGuardConfig, result: RunResult
) -> list[AttackChainRecord]:
    """Write the cross-layer attack chains (docs/DESIGN.md §7).

    Chains are stored apart from alerts because a chain names a *path* between entities across
    telemetry layers, not a score on one entity; flattening it into the alert table would discard
    the only thing that makes it interesting.
    """
    chains = find_attack_chains(
        list(result.dataset.events), result.signals, cfg.correlation.cross_layer
    )
    rows = [
        AttackChainRecord(
            run_id=run.id,
            source_ip=chain.external_ip,
            internal_entity_type="api_key",
            internal_entity_id=chain.api_key,
            confidence=chain.score,
            steps=[
                {
                    "stage": step.stage,
                    "entity_type": step.entity_type,
                    "entity_id": step.entity_id,
                    "at": step.at.isoformat(),
                    "detail": step.detail,
                }
                for step in chain.steps
            ],
            narrative=chain.explain(),
        )
        for chain in chains
    ]
    session.add_all(rows)
    session.flush()
    return rows


#: Resolution of the stored timeline. One hour over a two-week dataset is a few hundred buckets —
#: small enough to send whole, fine enough to show a diurnal rhythm and an off-hours burst, which
#: are the shapes an analyst is looking for. A coarser bucket hides exactly those.
TIMELINE_BUCKET_SECONDS = 3600


def build_timeline(result: RunResult) -> list[dict[str, Any]]:
    """Aggregate the event stream into fixed-width buckets for the dashboard timeline.

    Counts are broken down by telemetry layer and by error/refusal outcome, because a flat total
    would hide the two things worth seeing: which layer an event burst happened in, and whether a
    volume rise was accompanied by a failure rise (a stolen key probing) or not (a busy afternoon).
    """
    events = list(result.dataset.events)
    if not events:
        return []

    start = min(event.timestamp for event in events)
    buckets: dict[int, dict[str, Any]] = {}

    for event in events:
        index = int((event.timestamp - start).total_seconds()) // TIMELINE_BUCKET_SECONDS
        bucket = buckets.get(index)
        if bucket is None:
            at = start + timedelta(seconds=index * TIMELINE_BUCKET_SECONDS)
            bucket = buckets[index] = {
                "at": at.isoformat(),
                "total": 0,
                "by_layer": {},
                "errors": 0,
                "refusals": 0,
            }
        bucket["total"] += 1
        layer = str(event.layer)
        bucket["by_layer"][layer] = bucket["by_layer"].get(layer, 0) + 1
        outcome = str(event.outcome)
        if outcome == "error":
            bucket["errors"] += 1
        elif outcome in {"refuse", "block"}:
            bucket["refusals"] += 1

    return [buckets[index] for index in sorted(buckets)]


#: Features surfaced in the network map's node inspector, with the label an analyst reads.
#:
#: A subset of the fifteen the builder computes, chosen for interpretability without a manual:
#: how much the entity did, how widely it reached, how much of that failed, and how much it
#: departed from its own past. The remainder stay available in the alert explanation, where the
#: peer comparison gives them meaning.
INSPECTOR_FEATURES: tuple[tuple[str, str], ...] = (
    ("events", "Events in window"),
    ("distinct_neighbours", "Distinct counterparties"),
    ("distinct_sessions", "Distinct sessions"),
    ("error_ratio", "Error rate"),
    ("refusal_ratio", "Refusal rate"),
    ("night_ratio", "Out-of-hours share"),
    ("weekend_ratio", "Weekend share"),
    ("burstiness", "Burstiness"),
    ("neighbour_novelty", "New counterparties"),
    ("volume_ratio", "Volume vs own baseline"),
)


def build_network_graph(
    cfg: GraphGuardConfig, result: RunResult, alerts: list[Alert]
) -> dict[str, Any]:
    """Build the entity graph the dashboard's network map renders.

    Uses the **last** snapshot, which is the current state of the estate: the map answers "what
    does the network look like now", while an alert's own evidence subgraph answers "what did it
    look like when this fired". Superimposing every window would draw entities that never
    coexisted as though they were neighbours.

    Each node carries the risk of the worst alert touching it, so the map can be coloured by
    danger without the client having to join two datasets.
    """
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(result.dataset.events))
    if not snapshots:
        return {"nodes": [], "links": []}
    snapshot = snapshots[-1]

    worst: dict[tuple[str, str], tuple[float, int]] = {}
    for alert in alerts:
        key = (alert.entity_type, alert.entity_id)
        score, count = worst.get(key, (0.0, 0))
        worst[key] = (max(score, alert.normalised_score), count + 1)

    nodes: list[dict[str, Any]] = []
    for node_type in snapshot.data.node_types:
        for entity_id in snapshot.node_ids.get(node_type, ()):
            risk, alert_count = worst.get((node_type, entity_id), (0.0, 0))
            attributes: dict[str, Any] = {}
            for feature, label in INSPECTOR_FEATURES:
                value = snapshot.raw_feature(node_type, entity_id, feature)
                attributes[label] = round(value, 3)
            nodes.append(
                {
                    "id": f"{node_type}:{entity_id}",
                    "type": node_type,
                    "label": entity_id,
                    "risk": round(risk, 4),
                    "alerts": alert_count,
                    "attributes": attributes,
                }
            )

    index = {node["id"] for node in nodes}
    facts = _edge_facts(snapshot, result.dataset.events)

    links: list[dict[str, Any]] = []
    for relation in snapshot.data.edge_types:
        source_type, name, target_type = relation
        edge_index = snapshot.data[relation].edge_index
        source_ids = snapshot.node_ids.get(source_type, ())
        target_ids = snapshot.node_ids.get(target_type, ())
        seen: set[tuple[str, str]] = set()
        for column in range(edge_index.size(1)):
            source_row = int(edge_index[0, column])
            target_row = int(edge_index[1, column])
            if source_row >= len(source_ids) or target_row >= len(target_ids):
                continue
            source = f"{source_type}:{source_ids[source_row]}"
            target = f"{target_type}:{target_ids[target_row]}"
            if source not in index or target not in index or (source, target) in seen:
                continue
            seen.add((source, target))
            fact = facts.get((source, target, name))
            links.append(
                {
                    "source": source,
                    "target": target,
                    "relation": name,
                    # The number of events on this pair, not the number of edge_index columns.
                    # Counting columns gave every edge a weight of exactly 1 — the builder has
                    # already collapsed parallel edges by the time it assembles the tensor — so
                    # the map's "heavier relation, thicker line" rule was encoding a constant.
                    "weight": fact["events"] if fact else 1,
                    "failures": fact["failures"] if fact else 0,
                    "first_seen": fact["first"].isoformat() if fact else None,
                    "last_seen": fact["last"].isoformat() if fact else None,
                }
            )

    return {"nodes": nodes, "links": links}


#: Outcomes that mean the interaction did not go through. Kept separate from the alert score:
#: a blocked request is a *fact about the event*, whereas an alert is a detector's opinion about
#: an entity, and the map is careful not to present one as the other.
_FAILED_OUTCOMES = frozenset({Outcome.BLOCK, Outcome.REFUSE, Outcome.ERROR})


def _edge_facts(
    snapshot: Any, events: Sequence[TraceEvent]
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Count what actually happened on each edge of the snapshot's window.

    The graph tensor carries structure and a weight; it does not carry outcomes or timings,
    because no detector needs them. An analyst inspecting an edge does: "these two entities are
    connected" is far less useful than "they interacted 41 times, 38 of which were blocked,
    between 02:10 and 02:40".

    Mirrors the three edge rules in ``graph/builder.py`` — the event's own actor→target relation,
    the derived ``used_from``, and the derived ``owns`` — and must be kept in step with them.
    Deliberately computed here rather than added to the builder: this is presentation metadata,
    and ``DECISIONS.md`` D17 keeps the analytics packages free of anything the platform needs.
    """
    facts: dict[tuple[str, str, str], dict[str, Any]] = {}

    def bump(
        source: tuple[str, str], target: tuple[str, str], name: str, event: TraceEvent
    ) -> None:
        key = (f"{source[0]}:{source[1]}", f"{target[0]}:{target[1]}", name)
        entry = facts.get(key)
        if entry is None:
            facts[key] = {
                "events": 1,
                "failures": 1 if event.outcome in _FAILED_OUTCOMES else 0,
                "first": event.timestamp,
                "last": event.timestamp,
            }
            return
        entry["events"] += 1
        if event.outcome in _FAILED_OUTCOMES:
            entry["failures"] += 1
        if event.timestamp < entry["first"]:
            entry["first"] = event.timestamp
        if event.timestamp > entry["last"]:
            entry["last"] = event.timestamp

    for event in events:
        if not snapshot.window.contains(event.timestamp):
            continue
        actor = event.actor_key
        bump(actor, event.target_key, event.action.value, event)

        # Same guard as the builder: on the gateway layer the actor *is* the address, and a
        # used_from edge there would be an ip → ip self-loop.
        if event.source_ip is not None:
            ip_node = (NodeType.IP.value, event.source_ip)
            if ip_node != actor:
                bump(actor, ip_node, USED_FROM[1], event)

        user_id = event.attributes.get(Attr.USER_ID)
        if isinstance(user_id, str) and user_id:
            bump((NodeType.USER.value, user_id), actor, OWNS[1], event)

    return facts


def run_summary(run: Run) -> dict[str, Any]:
    """Compact representation of a run, for listings and status polling."""
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status.value,
        "event_count": run.event_count,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_seconds": (
            round((run.finished_at - run.started_at).total_seconds(), 2)
            if run.started_at and run.finished_at
            else None
        ),
    }
