"""Case management — gathering alerts and chains into one investigation.

Role
----
An investigation is the unit an analyst actually reasons about. A single compromised credential
produces a dozen alerts across windows and detectors, and triaging each in isolation loses the
fact that they are one incident.

That matters more here than in most tools. This project's whole argument is that treating related
events as independent hides the attack; an interface that forces alert-by-alert triage would
reproduce exactly that blindness one layer up.

Inputs / outputs
----------------
Input: analyst actions — create a case, attach evidence, record findings, close it.
Output: ``investigations`` rows with their members, and an export document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from graphguard.platform.models import (
    Alert,
    AttackChainRecord,
    Investigation,
    InvestigationMember,
    InvestigationStatus,
    User,
)


class InvestigationError(Exception):
    """Raised when an operation on a case is not valid."""


def create(
    session: Session, *, title: str, summary: str = "", owner: User | None = None
) -> Investigation:
    """Open a new case."""
    if not title.strip():
        raise InvestigationError("a case needs a title")
    investigation = Investigation(
        title=title.strip(), summary=summary, owner_id=owner.id if owner else None
    )
    session.add(investigation)
    session.flush()
    return investigation


def attach(
    session: Session,
    investigation: Investigation,
    *,
    alert_id: str | None = None,
    chain_id: str | None = None,
    rationale: str = "",
) -> InvestigationMember:
    """Attach one alert or one chain to a case.

    Raises
    ------
    InvestigationError
        If neither or both identifiers are given, if the referenced record does not exist, or if
        it is already attached.

    """
    if (alert_id is None) == (chain_id is None):
        raise InvestigationError("attach exactly one of alert_id or chain_id")

    if alert_id is not None and session.get(Alert, alert_id) is None:
        raise InvestigationError(f"no such alert: {alert_id}")
    if chain_id is not None and session.get(AttackChainRecord, chain_id) is None:
        raise InvestigationError(f"no such chain: {chain_id}")

    existing = session.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.alert_id == alert_id,
            InvestigationMember.chain_id == chain_id,
        )
    )
    if existing is not None:
        raise InvestigationError("that evidence is already attached to this case")

    member = InvestigationMember(
        investigation_id=investigation.id,
        alert_id=alert_id,
        chain_id=chain_id,
        rationale=rationale,
    )
    session.add(member)
    # Evidence changes are what "recently touched" means for a case, so they bump the
    # timestamp the listing orders by; without this, only renames surfaced a case.
    investigation.updated_at = datetime.now(UTC)
    session.flush()
    return member


def detach(session: Session, investigation: Investigation, member_id: str) -> None:
    """Remove one piece of evidence from a case."""
    member = session.get(InvestigationMember, member_id)
    if member is None or member.investigation_id != investigation.id:
        raise InvestigationError("that evidence is not attached to this case")
    session.delete(member)
    investigation.updated_at = datetime.now(UTC)
    session.flush()


def set_status(
    session: Session, investigation: Investigation, status: InvestigationStatus
) -> Investigation:
    """Move a case through its lifecycle.

    Unlike alert triage there is no transition graph here: a case legitimately reopens, and
    contained work legitimately returns to open when new evidence arrives. Constraining that would
    describe a workflow no incident actually follows.
    """
    investigation.status = status
    session.flush()
    return investigation


def summarise(investigation: Investigation) -> dict[str, Any]:
    """Compact representation for the case list."""
    alerts = sum(1 for member in investigation.members if member.alert_id)
    chains = sum(1 for member in investigation.members if member.chain_id)
    return {
        "id": investigation.id,
        "title": investigation.title,
        "summary": investigation.summary,
        "status": investigation.status.value,
        "owner_id": investigation.owner_id,
        "alert_count": alerts,
        "chain_count": chains,
        "created_at": investigation.created_at.isoformat(),
        "updated_at": investigation.updated_at.isoformat(),
    }


def detail(investigation: Investigation) -> dict[str, Any]:
    """Full case with its evidence resolved."""
    members: list[dict[str, Any]] = []
    for member in investigation.members:
        entry: dict[str, Any] = {
            "id": member.id,
            "rationale": member.rationale,
            "added_at": member.added_at.isoformat(),
        }
        if member.alert is not None:
            alert = member.alert
            entry["kind"] = "alert"
            # The window bounds and the tags are here so a case can be drawn as a timeline
            # and read as a set of techniques, rather than as an undated list of entity names.
            entry["alert"] = {
                "id": alert.id,
                "run_id": alert.run_id,
                "detector": alert.detector,
                "entity_type": alert.entity_type,
                "entity_id": alert.entity_id,
                "window_index": alert.window_index,
                "window_start": alert.window_start.isoformat(),
                "window_end": alert.window_end.isoformat(),
                "score": round(alert.raw_score, 4),
                "window_score": round(alert.normalised_score, 4),
                "state": alert.state.value,
                "explanation": alert.explanation,
                "tags": alert.tags,
            }
        elif member.chain is not None:
            chain = member.chain
            entry["kind"] = "chain"
            entry["chain"] = {
                "id": chain.id,
                "run_id": chain.run_id,
                "source_ip": chain.source_ip,
                "internal_entity_type": chain.internal_entity_type,
                "internal_entity_id": chain.internal_entity_id,
                "confidence": round(chain.confidence, 4),
                "steps": chain.steps,
                "narrative": chain.narrative,
            }
        else:
            # The referenced record was deleted with its run. Keep the slot visible rather
            # than dropping it, so the case does not silently shrink.
            entry["kind"] = "missing"
        members.append(entry)

    return {**summarise(investigation), "members": members}
