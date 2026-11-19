"""Scenario: stolen API key (attacker class B — compromised identity).

Role
----
Injects attacker traffic on top of an existing, legitimate key. The key's owner keeps working
normally throughout, because in reality they do not know their credential has leaked. The
observable result is one credential exhibiting **two superimposed behaviours**.

Inputs / outputs
----------------
Input: the ``Population``, :class:`~graphguard.config.StolenKeyConfig`, and a seeded RNG.
Output: attacker :class:`~graphguard.simulator.benign.CallPlan`s labelled ``"stolen_key"``, plus
:class:`~graphguard.simulator.labels.ScenarioLabel` intervals for evaluation.

The signature
-------------
From the onset moment onward, the compromised key shows four simultaneous deviations *from its own
history* — which is exactly what a node-outlier detector is meant to catch (docs/DESIGN.md §3.2):

1. **Volume** — total traffic rises to roughly ``volume_multiplier`` times normal.
2. **Timing** — most attacker calls land outside working hours.
3. **Model novelty** — a large share of calls go to models this key has never used.
4. **Origin** — traffic arrives from an external IP outside the key's home set.

Plus a raised error rate: an attacker driving someone else's integration fumbles parameters and
trips rate limits more often than the owner does.

Why the owner keeps working
---------------------------
Silencing the legitimate owner during the attack window would turn detection into "spot the key
whose behaviour was replaced", which is far easier than the real problem and would inflate every
metric. Superimposing the two behaviours keeps the benign signal present as noise, which is the
honest version of the task.

Why no single deviation is sufficient on its own
------------------------------------------------
Each individual signal is matched somewhere in the benign population by design
(``simulator.population``): ``batch_job`` keys also run at 03:00, ``app_developer`` keys also have
high volume, and ``researcher`` keys also touch many models. Only the *conjunction*, relative to
this particular key's own baseline, is anomalous — which is the point the log-vs-graph comparison
has to demonstrate.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from graphguard.config import SimulatorConfig
from graphguard.schema import Action, Outcome
from graphguard.simulator.benign import SESSION_GAP_MINUTES, CallPlan
from graphguard.simulator.labels import ScenarioLabel
from graphguard.simulator.population import ApiKey, Population

SCENARIO_NAME = "stolen_key"

#: Attacker infrastructure. TEST-NET-3 (RFC 5737), disjoint from the office and remote ranges the
#: population uses, so an attacker IP is unambiguously external.
ATTACKER_PREFIX = "203.0.113."

#: Errors characteristic of someone driving an integration they do not understand.
ATTACK_ERROR_TYPES = (
    "rate_limit",
    "invalid_request",
    "permission_denied",
    "model_not_found",
    "context_length_exceeded",
)


def _off_hours(working_hours: tuple[int, int]) -> list[int]:
    """Hours of the day outside the configured working window."""
    start, end = working_hours
    return [h for h in range(24) if h < start or h >= end]


def _attack_hour_weights(working_hours: tuple[int, int], off_hours_bias: float) -> np.ndarray:
    """Hourly distribution for attacker traffic, biased toward off-hours."""
    off = set(_off_hours(working_hours))
    weights = np.zeros(24, dtype=float)

    n_off = len(off)
    n_on = 24 - n_off
    for hour in range(24):
        if hour in off:
            weights[hour] = off_hours_bias / n_off if n_off else 0.0
        else:
            weights[hour] = (1.0 - off_hours_bias) / n_on if n_on else 0.0

    total = weights.sum()
    if total <= 0:  # pragma: no cover — only reachable with a degenerate working_hours config
        weights[:] = 1.0 / 24
        total = 1.0
    return weights / total


def _choose_victims(
    population: Population, n_victims: int, rng: np.random.Generator
) -> list[ApiKey]:
    """Pick which keys get compromised.

    Victims are drawn uniformly rather than biased toward convenient personas. That deliberately
    admits hard cases — compromising a ``batch_job`` key hides the off-hours signal, because that
    key already ran at 03:00 — and those cases are the ones worth reporting honestly.
    """
    if n_victims > len(population.keys):
        raise ValueError(
            f"cannot compromise {n_victims} keys: population has only {len(population.keys)}"
        )
    indices = rng.choice(len(population.keys), size=n_victims, replace=False)
    return [population.keys[int(i)] for i in indices]


def inject_stolen_key(
    cfg: SimulatorConfig,
    population: Population,
    rng: np.random.Generator,
) -> tuple[list[CallPlan], list[ScenarioLabel]]:
    """Generate attacker traffic for the stolen-key scenario.

    Returns the attacker's call plans (to be merged with benign traffic and re-sorted) and the
    ground-truth intervals for the compromised keys.
    """
    scenario_cfg = cfg.scenarios.stolen_key
    if not scenario_cfg.enabled:
        return [], []

    onset_day = int(cfg.days * scenario_cfg.onset_fraction)
    onset = cfg.start + timedelta(days=onset_day)
    period_end = cfg.start + timedelta(days=cfg.days)
    if onset >= period_end:
        raise ValueError(
            f"stolen_key onset ({onset}) falls outside the observation period ending {period_end}; "
            f"lower onset_fraction or raise simulator.days"
        )

    hour_weights = _attack_hour_weights(cfg.benign.working_hours, scenario_cfg.off_hours_bias)
    victims = _choose_victims(population, scenario_cfg.n_victims, rng)

    plans: list[CallPlan] = []
    labels: list[ScenarioLabel] = []

    for victim_idx, victim in enumerate(victims):
        attacker_ip = f"{ATTACKER_PREFIX}{10 + victim_idx}"
        unseen_models = [m for m in population.models if m not in victim.known_models]
        familiar_models = list(victim.model_affinity)

        # The attacker adds traffic on top of the owner's, so the *observed* total approaches
        # volume_multiplier x the key's normal rate.
        extra_rate = max(victim.rate * (scenario_cfg.volume_multiplier - 1.0), 1.0)

        for day_index in range(onset_day, cfg.days):
            day = cfg.start + timedelta(days=day_index)
            # No weekend damping: the attacker is not on the company's schedule.
            n_calls = int(rng.poisson(extra_rate))
            if n_calls <= 0:
                continue

            hours = rng.choice(24, size=n_calls, p=hour_weights)
            minutes = rng.integers(0, 60, size=n_calls)
            seconds = rng.integers(0, 60, size=n_calls)
            stamps = sorted(
                day + timedelta(hours=int(h), minutes=int(m), seconds=int(s))
                for h, m, s in zip(hours, minutes, seconds, strict=True)
            )

            session_idx = 0
            previous = None
            gap = timedelta(minutes=SESSION_GAP_MINUTES)

            for stamp in stamps:
                if previous is not None and stamp - previous > gap:
                    session_idx += 1
                previous = stamp

                use_new_model = unseen_models and rng.random() < scenario_cfg.new_model_ratio
                pool = unseen_models if use_new_model else familiar_models
                model = str(rng.choice(pool))

                roll = rng.random()
                if roll < scenario_cfg.error_rate:
                    action, outcome = Action.ERROR, Outcome.ERROR
                    error_type: str | None = str(rng.choice(ATTACK_ERROR_TYPES))
                elif roll < scenario_cfg.error_rate + cfg.benign.refusal_rate:
                    action, outcome, error_type = Action.REFUSAL, Outcome.REFUSE, None
                else:
                    action, outcome, error_type = Action.CALL, Outcome.SUCCESS, None

                tokens_in = int(
                    rng.lognormal(victim.persona.tokens_in_mu, victim.persona.tokens_in_sigma)
                )
                tokens_out = (
                    int(tokens_in * rng.uniform(0.3, 3.0)) if outcome is Outcome.SUCCESS else 0
                )

                plans.append(
                    CallPlan(
                        timestamp=stamp,
                        key=victim,
                        model=model,
                        ip=attacker_ip,
                        session_id=f"sess-atk-{victim.key_id}-d{day_index}-{session_idx}",
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
                entity_id=victim.key_id,
                start=onset,
                end=period_end,
            )
        )

    plans.sort(key=lambda p: p.timestamp)
    return plans, labels
