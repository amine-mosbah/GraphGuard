"""Cross-layer attack-chain correlation — the finding that is a chain, not an event.

Role
----
Finds the attack docs/DESIGN.md §7 calls the killer feature: an external adversary who brute-forces
the gateway, breaches one credential, and then abuses it from inside. Each stage is individually
unremarkable; the evidence is the *ordered conjunction* connecting them.

Inputs / outputs
----------------
Input: the canonical event stream (both layers), the detector signals, and
:class:`~graphguard.config.CrossLayerConfig`.
Output: :class:`AttackChain` objects — ordered, explained, each naming the entities and windows
involved.

How the search relates to the graph
-----------------------------------
The graph contributes two things, and the event stream contributes one, and being precise about
which is which matters at a defence.

From the graph design comes the **shared identity**: ``source_ip`` is validated and canonicalised
(D7) precisely so that an address in a gateway log and an address on an API call resolve to the
same entity — the ``ip`` vertex both layers share. That identity is what makes the join between
the layers well-defined at all. From the graph detectors comes **corroboration**: the final stage
of a chain is the credential's own anomaly score after the pivot, produced by the detectors that
run on the snapshots.

The search itself, however, runs over the **event stream at event resolution**, not by traversing
snapshot edges — a deliberate choice, not a shortcut. The discriminating evidence sits below the
snapshot's one-day resolution: a burst is forty blocked logins within *one hour*, and a pivot is a
first use *hours* after that burst. Snapshot edges are typed by action but carry neither outcomes
nor sub-window timing, so a traversal over them could not distinguish a blocked login from a
successful one, nor order the stages. The chain is a path in the report's sense — an ordered walk
external address → breach → credential → abuse — reconstructed at the resolution the evidence
requires.

Guarding against the obvious false positive
-------------------------------------------
The discriminator is **novelty of the association, not its existence**. Legitimate credentials use
their office address constantly, so "a key used this address" links almost every key to almost
every address and would flag the whole platform. What matters is a credential appearing at an
address *for the first time*, shortly after that address was seen brute-forcing. The
`credential_stuffing` scenario is the control that keeps this honest: it brute-forces and never
breaches, so no credential ever pivots onto its address, and it must therefore produce a
high-confidence external alert but **no chain**.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from graphguard.config import CrossLayerConfig
from graphguard.detection.base import Signal
from graphguard.schema import Action, ActorType, Layer, Outcome, TraceEvent


@dataclass(frozen=True, slots=True)
class ChainStep:
    """One stage of an attack chain, in the order it happened."""

    stage: str
    entity_type: str
    entity_id: str
    at: datetime
    detail: str

    def text(self) -> str:
        """Render the step as a line an analyst reads."""
        return f"[{self.at.isoformat(timespec='seconds')}] {self.stage}: {self.detail}"


@dataclass(frozen=True)
class AttackChain:
    """An ordered path linking an external event to an internal identity."""

    external_ip: str
    api_key: str
    steps: tuple[ChainStep, ...]
    #: Confidence in [0, 1]. Deliberately built from the evidence present, not from a model.
    score: float
    #: True when an AI-layer detector independently flagged the credential after the pivot.
    corroborated: bool
    tags: tuple[str, ...] = field(default=("MITRE_ATLAS:Initial_Access", "OWASP_LLM:LLM10"))

    def explain(self) -> str:
        """Render the plain-language account of the chain, in order."""
        header = (
            f"Attack chain: external address {self.external_ip} to internal credential "
            f"{self.api_key} (confidence {self.score:.2f}"
            + (
                ", corroborated by an internal detector"
                if self.corroborated
                else ", uncorroborated"
            )
            + ")"
        )
        return header + "\n" + "\n".join(f"  {step.text()}" for step in self.steps)

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable form, for the API and saved runs."""
        return {
            "external_ip": self.external_ip,
            "api_key": self.api_key,
            "score": round(self.score, 4),
            "corroborated": self.corroborated,
            "tags": list(self.tags),
            "steps": [
                {
                    "stage": s.stage,
                    "entity_type": s.entity_type,
                    "entity_id": s.entity_id,
                    "at": s.at.isoformat(),
                    "detail": s.detail,
                }
                for s in self.steps
            ],
            "explanation": self.explain(),
        }


@dataclass(frozen=True)
class _Burst:
    """A window in which one address was brute-forcing."""

    ip: str
    start: datetime
    end: datetime
    failures: int


def find_attack_chains(
    events: Sequence[TraceEvent],
    signals: dict[str, list[Signal]],
    cfg: CrossLayerConfig,
) -> list[AttackChain]:
    """Search for external-to-internal attack chains.

    Returns chains ranked by confidence. An address that brute-forces without any credential
    subsequently pivoting onto it yields no chain — that is an external incident, not a breach,
    and conflating the two would raise a chain alert for every brute-force attempt on the
    internet.
    """
    if not cfg.enabled:
        return []

    bursts = _find_bursts(events, cfg)
    if not bursts:
        return []

    first_seen = _first_credential_use(events)
    successful_logins = _successful_logins(events)
    internal_scores = _internal_scores(signals)

    chains: list[AttackChain] = []
    for burst in bursts:
        pivot_deadline = burst.end + timedelta(hours=cfg.max_pivot_hours)

        for (key_id, ip), first_use in first_seen.items():
            if ip != burst.ip:
                continue
            # The pivot must be *new*: a credential that already used this address before the
            # brute force started was simply always there, which is the benign case.
            if first_use < burst.start or first_use > pivot_deadline:
                continue

            steps = [
                ChainStep(
                    stage="external brute force",
                    entity_type="ip",
                    entity_id=burst.ip,
                    at=burst.start,
                    detail=(
                        f"{burst.failures} blocked authentication attempts from {burst.ip} "
                        f"within one window"
                    ),
                )
            ]

            login_at = _login_after(successful_logins.get(burst.ip, []), burst.start)
            if login_at is not None:
                steps.append(
                    ChainStep(
                        stage="breach",
                        entity_type="ip",
                        entity_id=burst.ip,
                        at=login_at,
                        detail=f"an authentication attempt from {burst.ip} succeeded",
                    )
                )

            steps.append(
                ChainStep(
                    stage="pivot",
                    entity_type="api_key",
                    entity_id=key_id,
                    at=first_use,
                    detail=(
                        f"credential {key_id} was used from {burst.ip} for the first time, "
                        f"{_hours_between(burst.end, first_use):.1f}h after the brute force"
                    ),
                )
            )

            # Corroboration must come from *after* the pivot. A credential flagged for something
            # it did days earlier is evidence of a different problem, and citing it here would
            # attribute pre-existing behaviour to this breach.
            after_pivot = [
                (score, detector, at)
                for score, detector, at in internal_scores.get(key_id, [])
                if at >= first_use
            ]
            best_internal = max(after_pivot, default=None)
            corroborated = best_internal is not None and best_internal[0] >= cfg.min_internal_score
            if corroborated and best_internal is not None:
                score_value, detector, at = best_internal
                steps.append(
                    ChainStep(
                        stage="internal abuse",
                        entity_type="api_key",
                        entity_id=key_id,
                        at=at,
                        detail=(
                            f"{detector} independently flagged {key_id} "
                            f"(score {score_value:.2f}) after the pivot"
                        ),
                    )
                )

            steps.sort(key=lambda step: step.at)

            chains.append(
                AttackChain(
                    external_ip=burst.ip,
                    api_key=key_id,
                    steps=tuple(steps),
                    score=_confidence(burst, login_at is not None, corroborated),
                    corroborated=corroborated,
                )
            )

    chains.sort(key=lambda c: c.score, reverse=True)
    return chains


# --------------------------------------------------------------------------- internals


def _find_bursts(events: Sequence[TraceEvent], cfg: CrossLayerConfig) -> list[_Burst]:
    """Find addresses with enough blocked logins in one hour to count as brute forcing."""
    per_hour: dict[tuple[str, datetime], int] = defaultdict(int)
    for event in events:
        if event.layer is not Layer.HTTP or event.action is not Action.LOGIN:
            continue
        if event.outcome is not Outcome.BLOCK:
            continue
        hour = event.timestamp.replace(minute=0, second=0, microsecond=0)
        per_hour[(event.actor_id, hour)] += 1

    # Merge consecutive qualifying hours for the same address into one burst, so a four-hour
    # attack is reported as one incident rather than four.
    qualifying: dict[str, list[datetime]] = defaultdict(list)
    counts: dict[tuple[str, datetime], int] = {}
    for (ip, hour), failures in per_hour.items():
        if failures >= cfg.min_failed_logins:
            qualifying[ip].append(hour)
            counts[(ip, hour)] = failures

    bursts: list[_Burst] = []
    for ip, hours in qualifying.items():
        hours.sort()
        start = previous = hours[0]
        total = counts[(ip, start)]
        for hour in hours[1:]:
            if hour - previous <= timedelta(hours=1):
                total += counts[(ip, hour)]
            else:
                bursts.append(
                    _Burst(ip=ip, start=start, end=previous + timedelta(hours=1), failures=total)
                )
                start, total = hour, counts[(ip, hour)]
            previous = hour
        bursts.append(_Burst(ip=ip, start=start, end=previous + timedelta(hours=1), failures=total))
    return bursts


def _first_credential_use(events: Sequence[TraceEvent]) -> dict[tuple[str, str], datetime]:
    """Earliest moment each credential was seen at each address, on the AI layer."""
    first: dict[tuple[str, str], datetime] = {}
    for event in events:
        if event.layer is not Layer.AI or event.actor_type is not ActorType.API_KEY:
            continue
        if event.source_ip is None:
            continue
        pair = (event.actor_id, event.source_ip)
        if pair not in first or event.timestamp < first[pair]:
            first[pair] = event.timestamp
    return first


def _successful_logins(events: Sequence[TraceEvent]) -> dict[str, list[datetime]]:
    """Collect the times at which each address authenticated successfully."""
    logins: dict[str, list[datetime]] = defaultdict(list)
    for event in events:
        if (
            event.layer is Layer.HTTP
            and event.action is Action.LOGIN
            and event.outcome is Outcome.SUCCESS
        ):
            logins[event.actor_id].append(event.timestamp)
    for times in logins.values():
        times.sort()
    return logins


def _internal_scores(
    signals: dict[str, list[Signal]],
) -> dict[str, list[tuple[float, str, datetime]]]:
    """Every AI-layer detector score per credential, with its detector and window.

    All of them are kept rather than only the best, because the caller has to restrict
    corroboration to windows after the pivot — and the highest-scoring window overall is often an
    earlier one belonging to a different incident.
    """
    scores: dict[str, list[tuple[float, str, datetime]]] = defaultdict(list)
    for detector, detector_signals in signals.items():
        for signal in detector_signals:
            if signal.node_type != "api_key" or signal.normalised_score <= 0:
                continue
            scores[signal.node_id].append((signal.normalised_score, detector, signal.window.start))
    return scores


def _login_after(times: Sequence[datetime], moment: datetime) -> datetime | None:
    """First successful login at or after ``moment``."""
    return next((t for t in times if t >= moment), None)


def _hours_between(start: datetime, end: datetime) -> float:
    """Hours from ``start`` to ``end``, never negative."""
    return max((end - start).total_seconds() / 3600.0, 0.0)


def _confidence(burst: _Burst, breached: bool, corroborated: bool) -> float:
    """Combine the evidence present into a confidence in [0, 1].

    Deliberately a transparent sum of named contributions rather than a learned score: an analyst
    must be able to see which pieces of evidence produced the number, and a chain is far too
    consequential a claim to justify with an opaque one.
    """
    score = 0.45  # a new credential appearing on a brute-forcing address
    if breached:
        score += 0.25  # ...that also authenticated successfully
    if corroborated:
        score += 0.20  # ...and then behaved anomalously
    # Volume of the brute force, contributing at most 0.10.
    score += min(burst.failures / 1000.0, 0.10)
    return round(min(score, 1.0), 4)
