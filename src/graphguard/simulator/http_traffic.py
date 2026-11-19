"""HTTP-layer telemetry: benign gateway traffic and the external attack scenarios (Layer 2).

Role
----
Generates the second telemetry layer. Benign traffic comes first and matters most: without a
realistic population of legitimate requests, an HTTP layer containing nothing but an attack makes
detection trivial and the cross-layer demonstration meaningless.

Inputs / outputs
----------------
Input: the ``Population`` (so gateway traffic originates from the *same* addresses the AI layer
uses), the simulator config, and a seeded RNG.
Output: HTTP-layer ``TraceEvent``s plus ground-truth labels for the attack scenarios.

Why benign HTTP traffic must reuse the population's addresses
--------------------------------------------------------------
The cross-layer join works because an ``ip`` observed at the gateway and an ``ip`` observed as the
source of an API call are the *same graph vertex*. If benign gateway traffic came from unrelated
addresses, that vertex would only ever be shared during an attack — and the correlation would be
detecting an artefact of the generator rather than a property of the data. Legitimate users
therefore browse the API from exactly the addresses their keys call from, so a shared address is
the normal case and the attack has to be found among many innocent joins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from graphguard.config import SimulatorConfig
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent
from graphguard.schema.events import BENIGN_LABEL
from graphguard.simulator.labels import ScenarioLabel
from graphguard.simulator.population import Population

#: Endpoints a legitimate console/SDK user touches.
BENIGN_ENDPOINTS: dict[str, float] = {
    "/v1/models": 0.22,
    "/v1/usage": 0.20,
    "/v1/keys": 0.14,
    "/v1/projects/{id}": 0.16,
    "/v1/auth/login": 0.18,
    "/v1/billing": 0.10,
}

AUTH_ENDPOINT = "/v1/auth/login"

#: Attacker infrastructure for the external scenarios. TEST-NET-3 (RFC 5737), disjoint from the
#: office, remote-worker and ring ranges so an external address is unambiguous in analysis.
EXTERNAL_PREFIX = "203.0.113."

CREDENTIAL_STUFFING = "credential_stuffing"
EXTERNAL_TO_INTERNAL = "external_to_internal"


@dataclass(frozen=True)
class HTTPPlan:
    """A planned gateway request, before it becomes a ``TraceEvent``."""

    timestamp: datetime
    client_ip: str
    endpoint: str
    status: int
    label: str
    user_id: str | None = None


def _event(plan: HTTPPlan) -> TraceEvent:
    """Materialise one planned request as a canonical HTTP-layer event."""
    if plan.status in (401, 403, 429):
        outcome = Outcome.BLOCK
    elif plan.status >= 400:
        outcome = Outcome.ERROR
    else:
        outcome = Outcome.SUCCESS
    action = Action.LOGIN if AUTH_ENDPOINT in plan.endpoint else Action.REQUEST

    attributes: dict[str, object] = {
        Attr.ENDPOINT_PATH: plan.endpoint,
        Attr.STATUS_CODE: plan.status,
        Attr.METHOD: "POST" if action is Action.LOGIN else "GET",
    }
    if plan.user_id is not None:
        attributes[Attr.USER_ID] = plan.user_id

    return TraceEvent(
        timestamp=plan.timestamp,
        layer=Layer.HTTP,
        session_id=None,
        actor_id=plan.client_ip,
        actor_type=ActorType.IP,
        target_id=plan.endpoint,
        target_type=TargetType.ENDPOINT,
        action=action,
        outcome=outcome,
        source_ip=plan.client_ip,
        attributes=attributes,
        label=plan.label,
    )


def generate_benign_http(
    cfg: SimulatorConfig, population: Population, rng: np.random.Generator
) -> list[HTTPPlan]:
    """Plan ordinary gateway traffic from the population's own addresses."""
    http_cfg = cfg.http
    endpoints = list(BENIGN_ENDPOINTS)
    weights = np.array([BENIGN_ENDPOINTS[e] for e in endpoints], dtype=float)
    weights /= weights.sum()

    plans: list[HTTPPlan] = []
    for key in population.keys:
        addresses = list(key.home_ips)
        for day_index in range(cfg.days):
            day = cfg.start + timedelta(days=day_index)
            is_weekend = day.weekday() >= 5
            rate = http_cfg.requests_per_key_per_day * (0.3 if is_weekend else 1.0)
            count = int(rng.poisson(max(rate, 0.0)))

            for _ in range(count):
                hour = int(rng.choice(24, p=key.hour_weights))
                stamp = day + timedelta(
                    hours=hour, minutes=int(rng.integers(0, 60)), seconds=int(rng.integers(0, 60))
                )
                endpoint = str(rng.choice(endpoints, p=weights))
                # Humans mistype passwords: a small share of legitimate logins fail, which is
                # what stops "a failed login" from being an attack signal on its own.
                failed_login = (
                    endpoint == AUTH_ENDPOINT and rng.random() < http_cfg.login_failure_rate
                )
                plans.append(
                    HTTPPlan(
                        timestamp=stamp,
                        client_ip=str(rng.choice(addresses)),
                        endpoint=endpoint,
                        status=401 if failed_login else 200,
                        label=BENIGN_LABEL,
                        user_id=key.user_id,
                    )
                )
    return plans


def inject_credential_stuffing(
    cfg: SimulatorConfig, rng: np.random.Generator
) -> tuple[list[HTTPPlan], list[ScenarioLabel]]:
    """Plan a pure external attack: one address, many failed logins, no success.

    This scenario never breaches. It exists as the control for the chain scenario below: an
    external attacker who fails is a *different* finding from one who succeeds and pivots inward,
    and a correlation that cannot tell them apart would raise an attack-chain alert for every
    brute-force attempt on the internet.
    """
    scenario_cfg = cfg.scenarios.credential_stuffing
    if not scenario_cfg.enabled:
        return [], []

    onset_day = int(cfg.days * scenario_cfg.onset_fraction)
    onset = cfg.start + timedelta(days=onset_day)
    attacker_ip = f"{EXTERNAL_PREFIX}{scenario_cfg.ip_offset}"

    plans: list[HTTPPlan] = []
    for burst in range(scenario_cfg.duration_hours):
        hour_start = onset + timedelta(hours=burst)
        attempts = int(rng.poisson(scenario_cfg.attempts_per_hour))
        for _ in range(attempts):
            plans.append(
                HTTPPlan(
                    timestamp=hour_start + timedelta(seconds=float(rng.uniform(0, 3600))),
                    client_ip=attacker_ip,
                    endpoint=AUTH_ENDPOINT,
                    status=401,
                    label=CREDENTIAL_STUFFING,
                )
            )

    labels = [
        ScenarioLabel(
            scenario=CREDENTIAL_STUFFING,
            entity_type="ip",
            entity_id=attacker_ip,
            start=onset,
            end=onset + timedelta(hours=scenario_cfg.duration_hours),
        )
    ]
    return plans, labels


def inject_external_to_internal(
    cfg: SimulatorConfig,
    population: Population,
    rng: np.random.Generator,
    *,
    exclude: set[str] | None = None,
) -> tuple[list[HTTPPlan], list[TraceEvent], list[ScenarioLabel]]:
    """Plan the hybrid chain: external breach, then internal abuse by the breached credential.

    Three stages, each innocuous on its own:

    1. An external address brute-forces the login endpoint — one of thousands of such attempts
       any internet-facing gateway sees daily.
    2. One attempt *succeeds*. A successful login is the most ordinary event a platform has.
    3. The compromised key then calls models **from that same address**, reaching services it has
       never used.

    No single stage justifies an alert. The finding is the *path* connecting them through the
    shared address vertex, which is exactly what a flat log cannot express and
    :mod:`graphguard.correlation.crosslayer` searches for.
    """
    scenario_cfg = cfg.scenarios.external_to_internal
    if not scenario_cfg.enabled:
        return [], [], []

    candidates = [k for k in population.keys if k.key_id not in (exclude or set())]
    if not candidates:
        raise ValueError("no key available to compromise for external_to_internal")
    victim = candidates[int(rng.integers(0, len(candidates)))]

    onset_day = int(cfg.days * scenario_cfg.onset_fraction)
    onset = cfg.start + timedelta(days=onset_day)
    attacker_ip = f"{EXTERNAL_PREFIX}{scenario_cfg.ip_offset}"
    period_end = cfg.start + timedelta(days=cfg.days)

    http_plans: list[HTTPPlan] = []

    # Stage 1 — brute force.
    for burst in range(scenario_cfg.stuffing_hours):
        hour_start = onset + timedelta(hours=burst)
        for _ in range(int(rng.poisson(scenario_cfg.attempts_per_hour))):
            http_plans.append(
                HTTPPlan(
                    timestamp=hour_start + timedelta(seconds=float(rng.uniform(0, 3600))),
                    client_ip=attacker_ip,
                    endpoint=AUTH_ENDPOINT,
                    status=401,
                    label=EXTERNAL_TO_INTERNAL,
                )
            )

    # Stage 2 — the breach: one successful login, from the same address.
    breach_at = onset + timedelta(hours=scenario_cfg.stuffing_hours)
    http_plans.append(
        HTTPPlan(
            timestamp=breach_at,
            client_ip=attacker_ip,
            endpoint=AUTH_ENDPOINT,
            status=200,
            label=EXTERNAL_TO_INTERNAL,
            user_id=victim.user_id,
        )
    )

    # Stage 3 — internal abuse on the AI layer, from that address.
    unseen = [m for m in population.models if m not in victim.known_models]
    ai_events: list[TraceEvent] = []
    pivot_start = breach_at + timedelta(hours=scenario_cfg.pivot_delay_hours)
    day = pivot_start

    while day < period_end:
        for _ in range(int(rng.poisson(scenario_cfg.calls_per_day))):
            stamp = day + timedelta(seconds=float(rng.uniform(0, 86400)))
            if stamp >= period_end:
                continue
            model = (
                str(rng.choice(unseen)) if unseen else str(rng.choice(list(victim.model_affinity)))
            )
            tokens_in = int(
                rng.lognormal(victim.persona.tokens_in_mu, victim.persona.tokens_in_sigma)
            )
            ai_events.append(
                TraceEvent(
                    timestamp=stamp,
                    layer=Layer.AI,
                    session_id=f"sess-e2i-{victim.key_id}",
                    actor_id=victim.key_id,
                    actor_type=ActorType.API_KEY,
                    target_id=model,
                    target_type=TargetType.MODEL,
                    action=Action.CALL,
                    outcome=Outcome.SUCCESS,
                    # The load-bearing detail: the same address as the brute force.
                    source_ip=attacker_ip,
                    attributes={
                        Attr.USER_ID: victim.user_id,
                        Attr.MODEL_NAME: model,
                        Attr.TOKENS_IN: max(tokens_in, 1),
                        Attr.TOKENS_OUT: int(tokens_in * rng.uniform(0.3, 2.0)),
                    },
                    label=EXTERNAL_TO_INTERNAL,
                )
            )
        day += timedelta(days=1)

    labels = [
        ScenarioLabel(
            scenario=EXTERNAL_TO_INTERNAL,
            entity_type="ip",
            entity_id=attacker_ip,
            start=onset,
            end=period_end,
        ),
        ScenarioLabel(
            scenario=EXTERNAL_TO_INTERNAL,
            entity_type="api_key",
            entity_id=victim.key_id,
            start=breach_at,
            end=period_end,
        ),
    ]
    return http_plans, ai_events, labels


def plans_to_events(plans: list[HTTPPlan]) -> list[TraceEvent]:
    """Materialise planned gateway requests as canonical events."""
    return [_event(plan) for plan in plans]
