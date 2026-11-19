"""HTTP routes for runs, triage, configuration and export.

Role
----
The analyst- and admin-facing surface the dashboard consumes, on top of persisted state. Every
route here requires authentication; the split between what an analyst and an administrator may do
follows :mod:`graphguard.platform.auth`.

Relationship to the legacy in-memory endpoints
----------------------------------------------
:mod:`graphguard.api.app` still exposes read-only ``/api/*`` endpoints backed by a single
in-memory pipeline run. Those are kept because the evaluation figures and the bare triage page
depend on them, and because they are the surface the report's current chapter describes. The
routes here are the persistent replacement: runs that can be re-executed, alerts that carry triage
state, and a timeline that does not require the raw event stream.

Inputs / outputs
----------------
Input: authenticated HTTP requests.
Output: JSON describing runs, alerts, chains, timelines and the effective configuration.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from graphguard.config import GraphGuardConfig
from graphguard.platform import config_store, investigations
from graphguard.platform.auth import AdminUser, CurrentUser, SessionDep
from graphguard.platform.models import (
    Alert,
    AlertState,
    AttackChainRecord,
    ConfigVersion,
    Investigation,
    InvestigationStatus,
    Run,
    RunStatus,
)
from graphguard.platform.runs import create_run, run_summary
from graphguard.platform.triage import (
    CLOSED_STATES,
    TransitionError,
    add_note,
    alert_detail,
    alert_summary,
    history,
    transition,
)

router = APIRouter(prefix="/api/v1", tags=["platform"])


# --------------------------------------------------------------------------- schemas


class StartRunRequest(BaseModel):
    """A request to execute the pipeline."""

    name: str = Field(default="manual run", max_length=120)


class TransitionRequest(BaseModel):
    """A triage state change."""

    state: AlertState
    rationale: str = Field(default="", max_length=2000)


class NoteRequest(BaseModel):
    """Free-text commentary on an alert."""

    body: str = Field(min_length=1, max_length=4000)


class SaveConfigRequest(BaseModel):
    """A configuration revision to store and activate."""

    config: dict[str, Any]
    note: str = Field(default="", max_length=500)


class CreateInvestigationRequest(BaseModel):
    """A new case."""

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=8000)


class AttachEvidenceRequest(BaseModel):
    """One alert or one chain to attach to a case."""

    alert_id: str | None = None
    chain_id: str | None = None
    rationale: str = Field(default="", max_length=2000)


class InvestigationStatusRequest(BaseModel):
    """A case lifecycle change."""

    status: InvestigationStatus


# --------------------------------------------------------------------------- helpers


def _get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such run: {run_id}")
    return run


def _get_alert(session: Session, alert_id: str) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such alert: {alert_id}")
    return alert


def _get_investigation(session: Session, investigation_id: str) -> Investigation:
    case = session.get(Investigation, investigation_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such case: {investigation_id}")
    return case


# --------------------------------------------------------------------------- runs


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(payload: StartRunRequest, session: SessionDep, user: AdminUser) -> dict[str, Any]:
    """Queue a pipeline run. Administrators only.

    Returns 202 rather than 201: the run has been *accepted*, not completed. Execution happens in
    a worker because a full run measures around seven seconds, which is far too long to hold a
    request open.
    """
    from graphguard.platform.jobs import dispatch

    # The *saved* configuration, not the file: an administrator who changed a threshold
    # through the platform expects the next run to use it.
    cfg = config_store.effective_config(session)
    run = create_run(session, cfg, name=payload.name, created_by_id=user.id)
    session.commit()

    run.task_id = dispatch(run.id)
    session.commit()
    session.refresh(run)
    return run_summary(run)


@router.get("/runs")
def list_runs(session: SessionDep, _user: CurrentUser, limit: int = Query(50, ge=1, le=200)) -> Any:
    """List runs, most recent first."""
    runs = session.scalars(select(Run).order_by(desc(Run.created_at)).limit(limit))
    return {"runs": [run_summary(run) for run in runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Full detail for one run, including its evaluation metrics."""
    run = _get_run(session, run_id)
    alert_count = session.scalar(
        select(func.count()).select_from(Alert).where(Alert.run_id == run_id)
    )
    chain_count = session.scalar(
        select(func.count())
        .select_from(AttackChainRecord)
        .where(AttackChainRecord.run_id == run_id)
    )
    # Aggregated in SQL rather than by shipping the run's alerts to the browser and counting
    # there: a run produces a few hundred, and the run list polls this while a run is in flight.
    by_detector = session.execute(
        select(Alert.detector, func.count(), func.max(Alert.raw_score))
        .where(Alert.run_id == run_id)
        .group_by(Alert.detector)
        .order_by(desc(func.count()))
    ).all()
    by_state = session.execute(
        select(Alert.state, func.count()).where(Alert.run_id == run_id).group_by(Alert.state)
    ).all()
    return {
        **run_summary(run),
        "alert_count": int(alert_count or 0),
        "chain_count": int(chain_count or 0),
        "alerts_by_detector": [
            {"detector": detector, "count": int(count), "top_score": round(float(top or 0.0), 4)}
            for detector, count, top in by_detector
        ],
        "alerts_by_state": {state.value: int(count) for state, count in by_state},
        "window_count": len(run.timeline or []),
        "metrics": run.metrics,
        "config": run.config,
    }


@router.get("/runs/{run_id}/timeline")
def get_timeline(run_id: str, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Pre-aggregated event counts per time bucket.

    Served from storage rather than computed per request: the raw event stream is not persisted,
    and a default run holds roughly 220,000 events, which must never be sent to a browser.
    """
    run = _get_run(session, run_id)
    return {"run_id": run.id, "buckets": run.timeline}


@router.get("/runs/{run_id}/graph")
def get_network_graph(run_id: str, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Return the entity graph: typed nodes with risk, and the relations between them.

    Served from storage for the same reason as the timeline — the graph is a pure function of an
    event stream that is not persisted, so computing it per request would re-run the simulator and
    the builder on every page load.
    """
    run = _get_run(session, run_id)
    graph = run.graph or {"nodes": [], "links": []}
    return {"run_id": run.id, **graph}


@router.get("/runs/{run_id}/chains")
def get_chains(run_id: str, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Cross-layer attack chains found in this run (docs/DESIGN.md §7).

    A distinct finding type from an alert, and served separately for that reason: an alert names
    an entity, a chain names a path linking an external event to an internal identity.
    """
    _get_run(session, run_id)
    chains = session.scalars(
        select(AttackChainRecord)
        .where(AttackChainRecord.run_id == run_id)
        .order_by(desc(AttackChainRecord.confidence))
    )
    return {
        "run_id": run_id,
        "chains": [
            {
                "id": chain.id,
                "source_ip": chain.source_ip,
                "internal_entity_type": chain.internal_entity_type,
                "internal_entity_id": chain.internal_entity_id,
                "confidence": round(chain.confidence, 4),
                "steps": chain.steps,
                "narrative": chain.narrative,
            }
            for chain in chains
        ],
    }


# --------------------------------------------------------------------------- alerts


@router.get("/alerts")
def list_alerts(
    session: SessionDep,
    _user: CurrentUser,
    run_id: Annotated[str | None, Query()] = None,
    detector: Annotated[str | None, Query()] = None,
    state: Annotated[AlertState | None, Query()] = None,
    open_only: Annotated[bool, Query(description="Exclude alerts an analyst has closed.")] = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return the triage queue: ranked alerts, worst first, with optional filters."""
    statement = select(Alert)
    if run_id:
        statement = statement.where(Alert.run_id == run_id)
    if detector:
        statement = statement.where(Alert.detector == detector)
    if state:
        statement = statement.where(Alert.state == state)
    if open_only:
        statement = statement.where(Alert.state.not_in(tuple(CLOSED_STATES)))

    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    alerts = session.scalars(statement.order_by(desc(Alert.raw_score)).limit(limit).offset(offset))
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "alerts": [alert_summary(alert) for alert in alerts],
    }


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Full drill-down: explanation, contributing factors, evidence subgraph and audit trail."""
    alert = _get_alert(session, alert_id)
    return alert_detail(alert, history(session, alert))


@router.post("/alerts/{alert_id}/transition")
def transition_alert(
    alert_id: str, payload: TransitionRequest, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """Move an alert through the triage workflow."""
    alert = _get_alert(session, alert_id)
    try:
        transition(session, alert, payload.state, actor=user, rationale=payload.rationale)
    except TransitionError as exc:
        # 409, not 400: the request is well-formed, it simply conflicts with the
        # alert's current state — which may have been changed by another analyst.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return alert_detail(alert, history(session, alert))


@router.post("/alerts/{alert_id}/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    alert_id: str, payload: NoteRequest, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """Attach commentary to an alert."""
    alert = _get_alert(session, alert_id)
    note = add_note(session, alert, payload.body, author=user)
    session.commit()
    return {
        "id": note.id,
        "body": note.body,
        "author_id": note.author_id,
        "created_at": note.created_at.isoformat(),
    }


@router.post("/alerts/{alert_id}/export")
def export_alert(alert_id: str, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    """Produce an incident report for a confirmed alert.

    Only a ``true_positive`` may be exported, and exporting moves it to ``exported`` so the queue
    records that the incident left the system. Exporting an unreviewed or benign alert would push
    unvetted material into whatever consumes the report.
    """
    alert = _get_alert(session, alert_id)
    if alert.state is not AlertState.TRUE_POSITIVE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"only a confirmed alert can be exported; this one is {alert.state.value}",
        )

    run = _get_run(session, alert.run_id)
    document = {
        "incident": {
            "alert_id": alert.id,
            "detector": alert.detector,
            "entity": {"type": alert.entity_type, "id": alert.entity_id},
            "window": {
                "index": alert.window_index,
                "start": alert.window_start.isoformat(),
                "end": alert.window_end.isoformat(),
            },
            "score": round(alert.raw_score, 4),
            "explanation": alert.explanation,
            "contributing_factors": alert.reasons,
            "evidence": alert.evidence,
            "tags": alert.tags,
        },
        "provenance": {
            "run_id": run.id,
            "run_name": run.name,
            "config": run.config,
            "exported_by": user.email,
        },
        "audit_trail": [
            {
                "from_state": change.from_state.value if change.from_state else None,
                "to_state": change.to_state.value,
                "actor_id": change.actor_id,
                "rationale": change.rationale,
                "at": change.created_at.isoformat(),
            }
            for change in history(session, alert)
        ],
    }

    transition(session, alert, AlertState.EXPORTED, actor=user, rationale="incident exported")
    session.commit()
    return document


# --------------------------------------------------------------------------- configuration


@router.get("/config")
def read_config(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Return the configuration new runs will use, and which saved revision it came from.

    Readable by any analyst: knowing the thresholds an alert was produced under is part of
    interpreting it. Only an administrator may change them.
    """
    state = config_store.effective_state(session)
    return {
        "config": state.config.model_dump(mode="json"),
        "active_version": (config_store.version_summary(state.version) if state.version else None),
        "source": state.source,
        # Present only when the revision marked active can no longer be parsed. The page needs
        # both halves: which revision is broken, and the exact keys to fix.
        "stale_version": (
            config_store.version_summary(state.stale_version) if state.stale_version else None
        ),
        "stale_reason": state.stale_reason,
    }


@router.get("/config/schema")
def read_config_schema(_user: CurrentUser) -> dict[str, Any]:
    """Return the JSON Schema of :class:`~graphguard.config.GraphGuardConfig`.

    The settings form is generated from this rather than hand-written. A hand-maintained form
    falls behind the model the moment a detector gains a parameter, and the failure is silent —
    the parameter exists, the administrator cannot reach it, and nothing says so. Deriving the
    form from the schema the server validates against makes that drift impossible.
    """
    return GraphGuardConfig.model_json_schema()


@router.put("/config/validate")
def validate_config(payload: dict[str, Any], _admin: AdminUser) -> dict[str, Any]:
    """Validate a proposed configuration without applying it.

    Separating validation from application matters here: a configuration that fails to parse
    halfway through would otherwise leave detection running on a half-applied set of thresholds,
    and a silently wrong threshold is indistinguishable from an absence of attacks.
    """
    try:
        cfg = GraphGuardConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return {"valid": True, "config": cfg.model_dump(mode="json")}


@router.post("/config", status_code=status.HTTP_201_CREATED)
def save_config(
    payload: SaveConfigRequest, session: SessionDep, admin: AdminUser
) -> dict[str, Any]:
    """Save a configuration revision and make it active. Administrators only.

    Validation happens before anything is written, so a rejected configuration leaves the previous
    revision in force rather than half-applying.
    """
    try:
        cfg = GraphGuardConfig.model_validate(payload.config)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    version = config_store.save_version(session, cfg, author=admin, note=payload.note)
    session.commit()
    return config_store.version_summary(version)


@router.get("/config/versions")
def list_config_versions(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Every saved revision, newest first."""
    return {"versions": [config_store.version_summary(v) for v in config_store.history(session)]}


@router.post("/config/versions/{version_id}/activate")
def activate_config_version(
    version_id: str, session: SessionDep, _admin: AdminUser
) -> dict[str, Any]:
    """Roll back to an earlier revision. Administrators only."""
    version = session.get(ConfigVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such revision: {version_id}")
    if not config_store.parses(version):
        # Activating it would "succeed" and then silently put the platform back on the file,
        # which reads from the queue as a rollback that took effect. Refuse instead.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"revision {version.revision} no longer validates against the current schema; "
            "edit it in the form and save it as a new revision instead",
        )
    config_store.activate_version(session, version)
    session.commit()
    return config_store.version_summary(version)


# --------------------------------------------------------------------------- investigations


@router.get("/investigations")
def list_investigations(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Open and closed cases, most recently touched first."""
    cases = session.scalars(select(Investigation).order_by(desc(Investigation.updated_at)))
    return {"investigations": [investigations.summarise(case) for case in cases]}


@router.post("/investigations", status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: CreateInvestigationRequest, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """Open a case."""
    try:
        case = investigations.create(
            session, title=payload.title, summary=payload.summary, owner=user
        )
    except investigations.InvestigationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    session.commit()
    return investigations.detail(case)


@router.get("/investigations/{investigation_id}")
def get_investigation(
    investigation_id: str, session: SessionDep, _user: CurrentUser
) -> dict[str, Any]:
    """One case with its evidence resolved."""
    return investigations.detail(_get_investigation(session, investigation_id))


@router.post("/investigations/{investigation_id}/members", status_code=status.HTTP_201_CREATED)
def attach_evidence(
    investigation_id: str,
    payload: AttachEvidenceRequest,
    session: SessionDep,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Attach one alert or one chain to a case."""
    case = _get_investigation(session, investigation_id)
    try:
        investigations.attach(
            session,
            case,
            alert_id=payload.alert_id,
            chain_id=payload.chain_id,
            rationale=payload.rationale,
        )
    except investigations.InvestigationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return investigations.detail(case)


@router.delete("/investigations/{investigation_id}/members/{member_id}")
def detach_evidence(
    investigation_id: str, member_id: str, session: SessionDep, _user: CurrentUser
) -> dict[str, Any]:
    """Remove one piece of evidence from a case."""
    case = _get_investigation(session, investigation_id)
    try:
        investigations.detach(session, case, member_id)
    except investigations.InvestigationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    session.commit()
    return investigations.detail(case)


@router.post("/investigations/{investigation_id}/status")
def set_investigation_status(
    investigation_id: str,
    payload: InvestigationStatusRequest,
    session: SessionDep,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Move a case through its lifecycle."""
    case = _get_investigation(session, investigation_id)
    investigations.set_status(session, case, payload.status)
    session.commit()
    return investigations.detail(case)


@router.get("/investigations/{investigation_id}/export")
def export_investigation(
    investigation_id: str, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """Produce an incident document for a whole case.

    The per-alert export (``/alerts/{id}/export``) answers "what did this one detection say".
    A case is the unit an incident is actually written about — one compromised credential is a
    dozen alerts across windows and detectors plus, sometimes, a chain — so the document has to
    be assembled at that level or the reader has to re-do the correlation the analyst already did.

    Unlike the alert export this does not change any state. A case is a working artifact that may
    be exported repeatedly as it develops; the alert transition to ``exported`` exists to stop an
    unreviewed detection leaving the system, and that gate is already applied per alert.
    """
    case = _get_investigation(session, investigation_id)
    document = investigations.detail(case)
    entities = sorted(
        {
            (member["alert"]["entity_type"], member["alert"]["entity_id"])
            for member in document["members"]
            if member["kind"] == "alert"
        }
        | {
            (member["chain"]["internal_entity_type"], member["chain"]["internal_entity_id"])
            for member in document["members"]
            if member["kind"] == "chain"
        }
    )
    tags = sorted(
        {
            tag
            for member in document["members"]
            if member["kind"] == "alert"
            for tag in member["alert"]["tags"]
        }
    )
    return {
        "case": document,
        "entities": [{"type": kind, "id": identifier} for kind, identifier in entities],
        "framework_tags": tags,
        "provenance": {
            "exported_by": user.email,
            "run_ids": sorted(
                {
                    member["alert"]["run_id"]
                    for member in document["members"]
                    if member["kind"] == "alert"
                }
                | {
                    member["chain"]["run_id"]
                    for member in document["members"]
                    if member["kind"] == "chain"
                }
            ),
        },
    }


# --------------------------------------------------------------------------- system


@router.get("/system")
def system_info(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Describe what this deployment is running, for the settings page.

    Deliberately does not expose a single secret or connection string — only the *kind* of each
    dependency. An analyst debugging "why does my run never finish" needs to know whether a broker
    is configured; nobody needs the password in it.
    """
    from graphguard import __version__
    from graphguard.platform.settings import get_settings

    settings = get_settings()
    return {
        "version": __version__,
        "environment": settings.environment,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "assistant_enabled": settings.assistant_enabled,
        "assistant_provider": settings.assistant_provider if settings.assistant_enabled else None,
        "assistant_model": settings.assistant_model if settings.assistant_enabled else None,
        "access_token_ttl_minutes": settings.access_token_ttl_minutes,
        "refresh_token_ttl_days": settings.refresh_token_ttl_days,
        "detectors": sorted(
            {row for (row,) in session.execute(select(Alert.detector).distinct()).all() if row}
        ),
    }


@router.get("/stats")
def platform_stats(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Headline counts for the dashboard's summary strip."""
    by_state: dict[AlertState, int] = {
        state: int(count)
        for state, count in session.execute(
            select(Alert.state, func.count()).group_by(Alert.state)
        ).all()
    }
    return {
        "runs": int(session.scalar(select(func.count()).select_from(Run)) or 0),
        "alerts": int(session.scalar(select(func.count()).select_from(Alert)) or 0),
        "alerts_by_state": {state.value: int(by_state.get(state, 0)) for state in AlertState},
        "open_alerts": sum(
            count for state, count in by_state.items() if state not in CLOSED_STATES
        ),
        "chains": int(session.scalar(select(func.count()).select_from(AttackChainRecord)) or 0),
        "last_run": next(
            (
                run_summary(run)
                for run in session.scalars(
                    select(Run)
                    .where(Run.status == RunStatus.SUCCEEDED)
                    .order_by(desc(Run.finished_at))
                    .limit(1)
                )
            ),
            None,
        ),
    }


__all__ = ["router", "Depends"]
