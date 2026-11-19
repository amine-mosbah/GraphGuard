"""Scenario: coordinated ring of fake accounts (attacker class C — coordinated abuse).

Role
----
Injects a set of **freshly created** accounts, all driven by one actor, hammering the same model
in synchronized bursts with near-identical behaviour. This is the scenario the abnormal-subgraph
detector exists for (docs/DESIGN.md §3.2): a group in suspicious *unison*, distinct from community
drift (a group *changing*).

Inputs / outputs
----------------
Input: the ``Population`` (for the model catalogue and to stay disjoint from real users),
:class:`~graphguard.config.CoordinatedRingConfig`, and a seeded RNG.
Output: ring :class:`~graphguard.simulator.benign.CallPlan`s labelled ``"coordinated_ring"``,
plus :class:`~graphguard.simulator.labels.ScenarioLabel` intervals per ring account.

The signature — and what it deliberately is NOT
-----------------------------------------------
Each ring member on its own is **unremarkable**: ~60 calls/day (below every ``app_developer``),
one model (like every ``batch_job``), working-plus-evening hours, ordinary token sizes, ordinary
error rates. A per-node outlier detector has nothing to bite on, and that is the point — the
scenario exists to demonstrate the detection mode node-outlier scoring structurally lacks.

What gives the ring away is *collective* structure:

1. **Synchronicity** — all members fire in the same bursts, offset by seconds of jitter.
2. **Shape identity** — same target model, same volume, same session rhythm, same token profile.
3. **Shared novelty** — every member appears for the first time in the same window, with the
   same shape (FraudSquad's temporal-synchronicity + similarity signature, RELATED_WORK Family 4).

Why members get individual IPs
------------------------------
A single shared IP would collapse the ring into one hub vertex and make detection a trivial
degree check on the ``ip`` node. Real account farms rotate residential proxies precisely to avoid
that, so each member gets its own address from one provider block — the graph must earn the
detection through behavioural similarity, not through an infrastructure shortcut we handed it.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from graphguard.config import SimulatorConfig
from graphguard.schema import Action, Outcome
from graphguard.simulator.benign import CallPlan
from graphguard.simulator.labels import ScenarioLabel
from graphguard.simulator.population import MODEL_CATALOGUE, ApiKey, Persona, Population

SCENARIO_NAME = "coordinated_ring"

#: Ring infrastructure: TEST-NET-2 (RFC 5737) — disjoint from the office/remote ranges *and*
#: from the stolen-key attacker block (TEST-NET-3), so scenarios stay separable in analysis.
RING_PREFIX = "198.51.100."
RING_IP_OFFSET = 200  # avoid the benign remote-worker addresses in the same block

#: The ring accounts' nominal persona. Only used for token-size parameters; ring timing is
#: dictated by the shared burst schedule, not by persona hour weights.
_RING_PERSONA = Persona(
    name="ring_member",
    calls_per_day=(0.0, 0.0),  # unused: volume comes from the burst schedule
    n_models=(1, 1),
    peak_hours=None,
    hour_spread=3.0,
    baseline_hour_weight=0.2,
    respects_weekend=False,
    tokens_in_mu=5.6,
    tokens_in_sigma=0.4,
)


def _pick_target_model(population: Population, rng: np.random.Generator) -> str:
    """Choose the model the ring hammers — a popular one, so the *model* node looks normal.

    Targeting an obscure model would light up that model's own volume features and let a
    per-node detector catch the ring indirectly. Popular-model abuse is both more realistic
    (rings farm the flagship) and the honest hard case.
    """
    names = list(MODEL_CATALOGUE)
    weights = np.array([MODEL_CATALOGUE[m] for m in names], dtype=float)
    weights /= weights.sum()
    return str(rng.choice(names, p=weights))


def _burst_schedule(
    days: range,
    bursts_per_day: int,
    rng: np.random.Generator,
) -> list[tuple[int, int, float]]:
    """Build the shared schedule: ``(day, burst index, second-of-day)`` per burst.

    One schedule for the whole ring — members apply only per-member jitter to it. Bursts avoid
    the small hours: an actor mimicking organic traffic schedules activity when activity is
    unremarkable (08:00–23:00).
    """
    schedule: list[tuple[int, int, float]] = []
    for day in days:
        for burst_idx in range(bursts_per_day):
            second = float(rng.uniform(8 * 3600, 23 * 3600))
            schedule.append((day, burst_idx, second))
    return schedule


def inject_coordinated_ring(
    cfg: SimulatorConfig,
    population: Population,
    rng: np.random.Generator,
) -> tuple[list[CallPlan], list[ScenarioLabel]]:
    """Generate the ring's traffic and ground-truth intervals.

    Returns plans to be merged with benign traffic, and one label per ring account covering
    ``[onset, period_end)`` — a fake account is malicious from its first event onward, unlike a
    stolen key which has a benign past.
    """
    ring_cfg = cfg.scenarios.coordinated_ring
    if not ring_cfg.enabled:
        return [], []

    onset_day = int(cfg.days * ring_cfg.onset_fraction)
    onset = cfg.start + timedelta(days=onset_day)
    period_end = cfg.start + timedelta(days=cfg.days)
    if onset >= period_end:
        raise ValueError(
            f"coordinated_ring onset ({onset}) falls outside the observation period ending "
            f"{period_end}; lower onset_fraction or raise simulator.days"
        )

    target_model = _pick_target_model(population, rng)
    calls_per_burst = max(ring_cfg.calls_per_day / ring_cfg.bursts_per_day, 1.0)
    schedule = _burst_schedule(range(onset_day, cfg.days), ring_cfg.bursts_per_day, rng)

    # Shared shape parameters: the whole ring is one script, so the *distribution* parameters
    # are drawn once, not per member.
    tokens_mu = _RING_PERSONA.tokens_in_mu + rng.normal(0.0, 0.1)
    error_rate = float(rng.uniform(0.015, 0.03))  # ordinary, deliberately

    plans: list[CallPlan] = []
    labels: list[ScenarioLabel] = []

    for member_idx in range(ring_cfg.n_accounts):
        member = ApiKey(
            key_id=f"key-ring-{member_idx:03d}",
            user_id=f"u-ring-{member_idx:03d}",
            persona=_RING_PERSONA,
            model_affinity={target_model: 1.0},
            home_ips={f"{RING_PREFIX}{RING_IP_OFFSET + member_idx}": 1.0},
            rate=ring_cfg.calls_per_day,
            hour_weights=np.full(24, 1.0 / 24),
        )
        member_ip = next(iter(member.home_ips))

        for day, burst_idx, burst_second in schedule:
            day_start = cfg.start + timedelta(days=day)
            # Per-member jitter on the shared schedule: seconds, not hours.
            member_offset = rng.normal(0.0, ring_cfg.sync_jitter_s)
            n_calls = int(rng.poisson(calls_per_burst))

            for call_idx in range(n_calls):
                # Calls within a burst are spaced by a few seconds, like a driven script.
                second = burst_second + member_offset + call_idx * float(rng.uniform(2.0, 8.0))
                second = min(max(second, 0.0), 24 * 3600 - 1)
                stamp = day_start + timedelta(seconds=second)

                roll = rng.random()
                if roll < error_rate:
                    action, outcome = Action.ERROR, Outcome.ERROR
                    error_type: str | None = "rate_limit"
                else:
                    action, outcome, error_type = Action.CALL, Outcome.SUCCESS, None

                tokens_in = int(rng.lognormal(tokens_mu, _RING_PERSONA.tokens_in_sigma))
                tokens_out = (
                    int(tokens_in * rng.uniform(0.3, 3.0)) if outcome is Outcome.SUCCESS else 0
                )

                plans.append(
                    CallPlan(
                        timestamp=stamp,
                        key=member,
                        model=target_model,
                        ip=member_ip,
                        session_id=f"sess-ring-{member.key_id}-d{day}-b{burst_idx}",
                        action=action,
                        outcome=outcome,
                        tokens_in=max(tokens_in, 1),
                        tokens_out=tokens_out,
                        latency_ms=int(120 + tokens_out * rng.uniform(1.5, 4.0)),
                        error_type=error_type,
                        label=SCENARIO_NAME,
                    )
                )

        labels.append(
            ScenarioLabel(
                scenario=SCENARIO_NAME,
                entity_type="api_key",
                entity_id=member.key_id,
                start=onset,
                end=period_end,
            )
        )

    plans.sort(key=lambda p: p.timestamp)
    return plans, labels
