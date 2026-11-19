"""Scenario: slow group drift (attacker classes B/D — gradual compromise or misconfiguration).

Role
----
Migrates a whole cohort's behaviour **gradually** across the observation period: the team's calls
move to a new shared model and its working hours slide later, a little more each day. This is the
scenario the community-drift detector exists for (docs/DESIGN.md §3.2): a group *changing shape over
time*, as distinct from a group in *unison* (abnormal-subgraph) or an individual deviating
(node-outlier).

Inputs / outputs
----------------
Input: the benign :class:`~graphguard.simulator.benign.CallPlan` list, the ``Population``,
:class:`~graphguard.config.SlowGroupDriftConfig`, and a seeded RNG.
Output: a *modified* plan list plus :class:`~graphguard.simulator.labels.ScenarioLabel` intervals
for the cohort.

Why this scenario rewrites plans instead of injecting new ones
--------------------------------------------------------------
Drift is a change in how existing accounts behave, not extra traffic on top. Injecting additional
events would raise the cohort's volume, and volume is exactly the signal a node-outlier detector
already catches — the scenario would then be solvable by the wrong detector and prove nothing
about drift. Rewriting keeps per-key volume constant so that *only* the shape moves.

The signature — and the property that makes it hard
---------------------------------------------------
Two coordinated shifts, both ramped linearly from zero at onset to their configured maximum at the
end of the period:

1. **Target migration** — a growing share of the cohort's calls go to one new shared model.
2. **Schedule slide** — the cohort's activity moves progressively later in the day.

Because the ramp is gradual, the change between any two consecutive windows is small: no member
crosses an outlier threshold in any single snapshot, and each member individually just looks like
someone whose habits are evolving. What is anomalous is that *the whole cohort moves together, and
keeps moving*. That is only visible by comparing a group's centre of mass across snapshots, which
is precisely what the community-drift detector measures and what per-node and per-window methods
structurally cannot see.

Benign or malicious?
--------------------
Deliberately ambiguous, and that is realistic: the same trajectory fits a slow credential
compromise spreading through a team, an insider gradually widening their reach, and a
misconfigured deployment rolling a new model out. GraphGuard's job is to surface it with evidence
and let the analyst decide (docs/DESIGN.md §14: scores inform a human, they never auto-block).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import numpy as np

from graphguard.config import SimulatorConfig
from graphguard.simulator.benign import CallPlan
from graphguard.simulator.labels import ScenarioLabel
from graphguard.simulator.population import MODEL_CATALOGUE, Population

SCENARIO_NAME = "slow_group_drift"


def _pick_cohort(
    population: Population,
    size: int,
    exclude: set[str],
    rng: np.random.Generator,
) -> list[str]:
    """Choose a cohort of same-persona keys — a plausible "team".

    Drawing from one persona matters: a community-drift detector first has to *find* a community,
    and a random grab-bag of unrelated keys would not form one in any clustering. Real teams share
    a working pattern, which is what makes them a community in the first place.
    """
    by_persona: dict[str, list[str]] = {}
    for key in population.keys:
        if key.key_id in exclude:
            continue
        by_persona.setdefault(key.persona.name, []).append(key.key_id)

    candidates: list[list[str]] = [ids for ids in by_persona.values() if len(ids) >= size]
    if not candidates:
        largest = max((len(ids) for ids in by_persona.values()), default=0)
        raise ValueError(
            f"no persona has {size} available keys for a drift cohort (largest has {largest}); "
            f"lower slow_group_drift.cohort_size or raise simulator.benign.n_users"
        )

    candidates.sort(key=len, reverse=True)
    pool = sorted(candidates[0])
    chosen = rng.choice(len(pool), size=size, replace=False)
    return [pool[int(i)] for i in chosen]


def apply_slow_group_drift(
    cfg: SimulatorConfig,
    population: Population,
    plans: list[CallPlan],
    rng: np.random.Generator,
    *,
    exclude: set[str] | None = None,
) -> tuple[list[CallPlan], list[ScenarioLabel]]:
    """Rewrite a cohort's plans so its behaviour migrates gradually.

    ``exclude`` names keys already claimed by another scenario, so a key is never both a drift
    cohort member and, say, a stolen-key victim — overlapping labels would make the per-scenario
    evaluation ambiguous about which attack a detection belongs to.
    """
    drift_cfg = cfg.scenarios.slow_group_drift
    if not drift_cfg.enabled:
        return plans, []

    onset_day = int(cfg.days * drift_cfg.onset_fraction)
    onset = cfg.start + timedelta(days=onset_day)
    period_end = cfg.start + timedelta(days=cfg.days)
    ramp_days = cfg.days - onset_day
    if ramp_days <= 1:
        raise ValueError(
            f"slow_group_drift needs at least two days of ramp, got {ramp_days}; "
            f"lower onset_fraction or raise simulator.days"
        )

    cohort = _pick_cohort(population, drift_cfg.cohort_size, exclude or set(), rng)
    cohort_set = set(cohort)

    # The cohort migrates toward one shared model none of them habitually use — a shared
    # destination is what makes this a *group* trajectory rather than independent wandering.
    incumbent = {
        model for key in population.keys if key.key_id in cohort_set for model in key.known_models
    }
    candidates = [m for m in MODEL_CATALOGUE if m not in incumbent]
    target_model = (
        str(rng.choice(candidates)) if candidates else str(rng.choice(list(MODEL_CATALOGUE)))
    )

    rewritten: list[CallPlan] = []
    for plan in plans:
        if plan.key.key_id not in cohort_set or plan.timestamp < onset:
            rewritten.append(plan)
            continue

        # Linear ramp: 0.0 at onset, 1.0 at the end of the observation period.
        progress = (plan.timestamp - onset).total_seconds() / (period_end - onset).total_seconds()
        progress = min(max(progress, 0.0), 1.0)

        model = plan.model
        if rng.random() < drift_cfg.final_model_shift * progress:
            model = target_model

        # Slide the schedule later, keeping the event inside its own day so the drift shows up
        # as a changed rhythm rather than as events leaking into neighbouring windows.
        shift_hours = drift_cfg.final_hour_shift * progress
        shifted = plan.timestamp + timedelta(hours=shift_hours)
        if shifted.date() != plan.timestamp.date():
            shifted = plan.timestamp.replace(hour=23, minute=plan.timestamp.minute)

        rewritten.append(replace(plan, model=model, timestamp=shifted, label=SCENARIO_NAME))

    rewritten.sort(key=lambda p: p.timestamp)

    labels = [
        ScenarioLabel(
            scenario=SCENARIO_NAME,
            entity_type="api_key",
            entity_id=key_id,
            start=onset,
            end=period_end,
        )
        for key_id in cohort
    ]
    return rewritten, labels
