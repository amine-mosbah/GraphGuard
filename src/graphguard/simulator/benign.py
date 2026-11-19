"""Synthetic benign telemetry — the background traffic everything else is developed against.

Role
----
Turns a :class:`~graphguard.simulator.population.Population` into a realistic stream of
``TraceEvent``s: normal API usage over a multi-day observation period, with diurnal rhythm,
weekend damping, per-key model preferences, stable source IPs, and ordinary error and refusal
rates.

Inputs / outputs
----------------
Input: :class:`~graphguard.config.SimulatorConfig`, a ``Population``, and a seeded RNG.
Output: ``TraceEvent``s labelled ``"benign"``, in chronological order.

Design notes
------------
*Volume is Poisson around a per-key mean* rather than constant, so day-to-day variation exists and
a single busy day is not automatically anomalous.

*Sessions are derived, not imposed.* Calls that fall within ``SESSION_GAP_MINUTES`` of each other
are grouped into one session, which is how a real platform would reconstruct them from timestamps.

*Events are emitted in global chronological order.* Timestamps are planned first as cheap tuples,
sorted, and only then materialised into ``TraceEvent`` objects — a telemetry stream arrives ordered,
and building the heavyweight objects lazily keeps memory flat for long observation periods.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from graphguard.config import SimulatorConfig
from graphguard.schema import (
    Action,
    ActorType,
    Attr,
    Layer,
    Outcome,
    TargetType,
    TraceEvent,
)
from graphguard.schema.events import BENIGN_LABEL
from graphguard.simulator.population import ApiKey, Population

#: Calls closer together than this belong to the same session.
SESSION_GAP_MINUTES = 30

#: Error classes a benign caller realistically hits.
BENIGN_ERROR_TYPES = ("rate_limit", "invalid_request", "context_length_exceeded", "timeout")


@dataclass(frozen=True, slots=True)
class CallPlan:
    """A planned call, before it is materialised into a ``TraceEvent``.

    Deliberately lightweight: millions of these may be sorted before any pydantic object exists.
    """

    timestamp: datetime
    key: ApiKey
    model: str
    ip: str
    session_id: str
    action: Action
    outcome: Outcome
    tokens_in: int
    tokens_out: int
    latency_ms: int
    error_type: str | None
    label: str


def _daily_call_count(
    key: ApiKey, day: datetime, rng: np.random.Generator, weekend_factor: float
) -> int:
    """Sample how many calls a key makes on a given day."""
    rate = key.rate
    is_weekend = day.weekday() >= 5
    if is_weekend and key.persona.respects_weekend:
        rate *= weekend_factor
    return int(rng.poisson(max(rate, 0.0)))


def _sample_timestamps(
    key: ApiKey, day: datetime, n_calls: int, rng: np.random.Generator
) -> list[datetime]:
    """Place ``n_calls`` timestamps within ``day`` according to the key's hourly rhythm."""
    if n_calls <= 0:
        return []
    hours = rng.choice(24, size=n_calls, p=key.hour_weights)
    minutes = rng.integers(0, 60, size=n_calls)
    seconds = rng.integers(0, 60, size=n_calls)
    stamps = [
        day + timedelta(hours=int(h), minutes=int(m), seconds=int(s))
        for h, m, s in zip(hours, minutes, seconds, strict=True)
    ]
    stamps.sort()
    return stamps


def _assign_sessions(key_id: str, day_index: int, stamps: list[datetime]) -> list[str]:
    """Group chronologically-sorted timestamps into sessions by inter-arrival gap."""
    session_ids: list[str] = []
    session_idx = 0
    previous: datetime | None = None
    gap = timedelta(minutes=SESSION_GAP_MINUTES)

    for stamp in stamps:
        if previous is not None and stamp - previous > gap:
            session_idx += 1
        session_ids.append(f"sess-{key_id}-d{day_index}-{session_idx}")
        previous = stamp
    return session_ids


def _sample_outcome(
    error_rate: float, refusal_rate: float, rng: np.random.Generator
) -> tuple[Action, Outcome, str | None]:
    """Decide how a call ended.

    Refusals are modelled as their own action rather than an error: a guardrail declining a request
    is a different relational fact from the platform failing, and the graph should be able to tell
    them apart.
    """
    roll = rng.random()
    if roll < error_rate:
        error_type = str(rng.choice(BENIGN_ERROR_TYPES))
        return Action.ERROR, Outcome.ERROR, error_type
    if roll < error_rate + refusal_rate:
        return Action.REFUSAL, Outcome.REFUSE, None
    return Action.CALL, Outcome.SUCCESS, None


def plan_benign_calls(
    cfg: SimulatorConfig,
    population: Population,
    rng: np.random.Generator,
) -> list[CallPlan]:
    """Plan every benign call over the observation period, in chronological order.

    Exposed separately from :func:`generate_benign` so attack scenarios can reason about, and
    interleave with, the benign plan without re-deriving it.
    """
    benign = cfg.benign
    plans: list[CallPlan] = []

    for key in population.keys:
        affinity_models = list(key.model_affinity)
        affinity_probs = np.array([key.model_affinity[m] for m in affinity_models], dtype=float)
        affinity_probs /= affinity_probs.sum()

        ip_names = list(key.home_ips)
        ip_probs = np.array([key.home_ips[i] for i in ip_names], dtype=float)
        ip_probs /= ip_probs.sum()

        for day_index in range(cfg.days):
            day = cfg.start + timedelta(days=day_index)
            n_calls = _daily_call_count(key, day, rng, benign.weekend_activity_factor)
            stamps = _sample_timestamps(key, day, n_calls, rng)
            sessions = _assign_sessions(key.key_id, day_index, stamps)

            for stamp, session_id in zip(stamps, sessions, strict=True):
                action, outcome, error_type = _sample_outcome(
                    benign.error_rate, benign.refusal_rate, rng
                )
                tokens_in = int(
                    rng.lognormal(key.persona.tokens_in_mu, key.persona.tokens_in_sigma)
                )
                # A refused or failed call produces little or no completion.
                if outcome is Outcome.SUCCESS:
                    tokens_out = int(tokens_in * rng.uniform(0.3, 3.0))
                else:
                    tokens_out = 0

                plans.append(
                    CallPlan(
                        timestamp=stamp,
                        key=key,
                        model=str(rng.choice(affinity_models, p=affinity_probs)),
                        ip=str(rng.choice(ip_names, p=ip_probs)),
                        session_id=session_id,
                        action=action,
                        outcome=outcome,
                        tokens_in=max(tokens_in, 1),
                        tokens_out=tokens_out,
                        latency_ms=int(120 + tokens_out * rng.uniform(1.5, 4.0)),
                        error_type=error_type,
                        label=BENIGN_LABEL,
                    )
                )

    plans.sort(key=lambda p: p.timestamp)
    return plans


def plan_to_event(plan: CallPlan) -> TraceEvent:
    """Materialise one planned call as a canonical ``TraceEvent``."""
    attributes: dict[str, object] = {
        Attr.USER_ID: plan.key.user_id,
        Attr.MODEL_NAME: plan.model,
        Attr.TOKENS_IN: plan.tokens_in,
        Attr.TOKENS_OUT: plan.tokens_out,
        Attr.LATENCY_MS: plan.latency_ms,
    }
    if plan.error_type is not None:
        attributes[Attr.ERROR_TYPE] = plan.error_type

    return TraceEvent(
        timestamp=plan.timestamp,
        layer=Layer.AI,
        session_id=plan.session_id,
        actor_id=plan.key.key_id,
        actor_type=ActorType.API_KEY,
        target_id=plan.model,
        target_type=TargetType.MODEL,
        action=plan.action,
        outcome=plan.outcome,
        source_ip=plan.ip,
        attributes=attributes,
        label=plan.label,
    )


def generate_benign(
    cfg: SimulatorConfig,
    population: Population,
    rng: np.random.Generator,
) -> Iterator[TraceEvent]:
    """Yield benign ``TraceEvent``s for the whole observation period, chronologically ordered."""
    for plan in plan_benign_calls(cfg, population, rng):
        yield plan_to_event(plan)
