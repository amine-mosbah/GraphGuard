"""The analyst triage workflow — alert states, legal transitions and the audit trail.

Role
----
Implements the analyst triage state machine: an alert moves from ``new`` through
``investigating`` to a verdict, and may then be exported. Every move is recorded with who made it
and why.

Where the rules live, and why
-----------------------------
Legal transitions are enforced here rather than by a database constraint. A constraint could
express "state is one of these five values" — and does, via the enum's check constraint — but not
"new may become investigating and nothing else". Encoding the graph in the service layer means an
illegal move returns a clear, actionable API error naming the states involved, instead of an
integrity violation the analyst cannot interpret.

Why a verdict is not final
--------------------------
``true_positive`` and ``false_positive`` can both be reopened into ``investigating``. Triage
mistakes are ordinary and an analyst who cannot correct one will instead avoid recording a verdict
at all, which is worse: the queue stops reflecting what is actually known. Reopening is not a
deletion — the original verdict stays in the audit trail, so the correction is visible as a
correction.

Inputs / outputs
----------------
Input: an alert, a target state, the acting user and their rationale.
Output: the updated alert plus an appended :class:`~graphguard.platform.models.AlertStateChange`,
written in one transaction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from graphguard.platform import metrics
from graphguard.platform.models import Alert, AlertState, AlertStateChange, AnalystNote, User

#: Which states each state may move to.
#:
#: Notable edges: a verdict can be reopened (see the module docstring), and ``exported`` is
#: reachable only from ``true_positive`` — exporting an incident report for something judged
#: benign, or not yet judged at all, would put unreviewed material into whatever system consumes
#: the export.
LEGAL_TRANSITIONS: dict[AlertState, frozenset[AlertState]] = {
    AlertState.NEW: frozenset({AlertState.INVESTIGATING, AlertState.FALSE_POSITIVE}),
    AlertState.INVESTIGATING: frozenset(
        {AlertState.TRUE_POSITIVE, AlertState.FALSE_POSITIVE, AlertState.NEW}
    ),
    AlertState.TRUE_POSITIVE: frozenset({AlertState.EXPORTED, AlertState.INVESTIGATING}),
    AlertState.FALSE_POSITIVE: frozenset({AlertState.INVESTIGATING}),
    AlertState.EXPORTED: frozenset({AlertState.INVESTIGATING}),
}

#: States that mean "an analyst has finished with this", for queue filtering.
CLOSED_STATES = frozenset(
    {AlertState.TRUE_POSITIVE, AlertState.FALSE_POSITIVE, AlertState.EXPORTED}
)


class TransitionError(Exception):
    """Raised when a requested state change is not permitted from the current state."""


def can_transition(current: AlertState, target: AlertState) -> bool:
    """Return whether ``current`` may move to ``target``."""
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


def transition(
    session: Session,
    alert: Alert,
    target: AlertState,
    *,
    actor: User | None,
    rationale: str = "",
) -> Alert:
    """Move ``alert`` to ``target``, recording the change.

    The alert's ``state`` column and the appended history row are written in the same transaction,
    so the denormalised current state can never disagree with the trail that explains it.

    Raises
    ------
    TransitionError
        If the move is not legal from the alert's current state.

    """
    current = alert.state
    if target == current:
        raise TransitionError(f"alert is already {current.value}")
    if not can_transition(current, target):
        allowed = sorted(s.value for s in LEGAL_TRANSITIONS.get(current, frozenset()))
        raise TransitionError(
            f"cannot move from {current.value} to {target.value}; "
            f"allowed from {current.value}: {', '.join(allowed) or 'nothing'}"
        )

    alert.state = target
    metrics.ALERT_TRANSITIONS.labels(to_state=target.value).inc()
    session.add(
        AlertStateChange(
            alert_id=alert.id,
            from_state=current,
            to_state=target,
            actor_id=actor.id if actor else None,
            rationale=rationale,
        )
    )
    session.flush()
    return alert


def add_note(session: Session, alert: Alert, body: str, *, author: User | None) -> AnalystNote:
    """Attach free-text commentary to an alert."""
    note = AnalystNote(alert_id=alert.id, author_id=author.id if author else None, body=body)
    session.add(note)
    session.flush()
    return note


def history(session: Session, alert: Alert) -> list[AlertStateChange]:
    """Return the alert's transitions, oldest first."""
    return list(
        session.scalars(
            select(AlertStateChange)
            .where(AlertStateChange.alert_id == alert.id)
            .order_by(AlertStateChange.created_at)
        )
    )


def alert_summary(alert: Alert) -> dict[str, Any]:
    """Compact row for the triage queue."""
    return {
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
        "ground_truth": alert.ground_truth,
    }


def alert_detail(alert: Alert, changes: list[AlertStateChange]) -> dict[str, Any]:
    """Full drill-down: the summary, the explanation, the evidence and the audit trail."""
    return {
        **alert_summary(alert),
        "explanation": alert.explanation,
        "reasons": alert.reasons,
        "evidence": alert.evidence,
        "tags": alert.tags,
        "notes": [
            {
                "id": note.id,
                "body": note.body,
                "author_id": note.author_id,
                "created_at": note.created_at.isoformat(),
            }
            for note in alert.notes
        ],
        "history": [
            {
                "from_state": change.from_state.value if change.from_state else None,
                "to_state": change.to_state.value,
                "actor_id": change.actor_id,
                "rationale": change.rationale,
                "at": change.created_at.isoformat(),
            }
            for change in changes
        ],
        "allowed_transitions": sorted(
            s.value for s in LEGAL_TRANSITIONS.get(alert.state, frozenset())
        ),
    }
