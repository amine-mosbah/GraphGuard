"""Tests for the synthetic population and benign traffic generator.

These guard the properties the *evaluation* depends on. If benign traffic stops being realistically
heterogeneous, every downstream detection result becomes flattering and meaningless.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime

import numpy as np
import pytest

from graphguard.config import BenignConfig, ScenariosConfig, SimulatorConfig, StolenKeyConfig
from graphguard.schema import Action, ActorType, Layer, Outcome, TargetType
from graphguard.schema.events import BENIGN_LABEL
from graphguard.simulator.benign import generate_benign, plan_benign_calls
from graphguard.simulator.population import PERSONAS, build_population

START = datetime(2026, 3, 2, tzinfo=UTC)  # a Monday


def small_config(**overrides: object) -> SimulatorConfig:
    """A fast configuration for tests: few users, few days."""
    benign_kwargs: dict[str, object] = {
        "n_users": 25,
        "persona_mix": {
            "researcher": 0.25,
            "app_developer": 0.35,
            "batch_job": 0.25,
            "casual": 0.15,
        },
        "working_hours": (8, 18),
        "weekend_activity_factor": 0.25,
        "error_rate": 0.02,
        "refusal_rate": 0.01,
    }
    benign_kwargs.update(overrides)
    return SimulatorConfig(
        start=START,
        days=7,
        benign=BenignConfig(**benign_kwargs),  # type: ignore[arg-type]
        scenarios=ScenariosConfig(stolen_key=StolenKeyConfig(enabled=False)),
    )


def build(seed: int = 1337) -> tuple[SimulatorConfig, object, np.random.Generator]:
    cfg = small_config()
    rng = np.random.default_rng(seed)
    population = build_population(cfg.benign, rng)
    return cfg, population, rng


# --------------------------------------------------------------------------- population


def test_every_user_owns_at_least_one_key() -> None:
    _, population, _ = build()
    summary = population.summary()
    assert summary["total_users"] == 25
    assert summary["total_keys"] >= summary["total_users"]


def test_keys_have_stable_distinct_profiles() -> None:
    """ "This account stopped behaving like itself" is only meaningful if "itself" exists."""
    _, population, _ = build()
    for key in population.keys:
        assert key.model_affinity, "a key with no habitual models has no identity to deviate from"
        assert key.home_ips
        assert key.rate > 0
        assert key.hour_weights.shape == (24,)
        assert pytest.approx(1.0, abs=1e-9) == key.hour_weights.sum()

    # Profiles must not be identical across keys, or per-key deviation is undetectable.
    affinities = {tuple(sorted(k.model_affinity)) for k in population.keys}
    assert len(affinities) > 1


def test_batch_jobs_are_single_model_single_ip_automation() -> None:
    _, population, _ = build()
    batch_keys = [k for k in population.keys if k.persona.name == "batch_job"]
    assert batch_keys, "the persona mix should produce batch jobs"
    for key in batch_keys:
        assert len(key.model_affinity) == 1
        assert len(key.home_ips) == 1


def test_unknown_persona_in_config_is_rejected() -> None:
    cfg = small_config(persona_mix={"astronaut": 1.0})
    with pytest.raises(ValueError, match="unknown personas"):
        build_population(cfg.benign, np.random.default_rng(0))


# --------------------------------------------------------------------------- determinism


def test_generation_is_reproducible_for_a_fixed_seed() -> None:
    """Fixed seeds are mandatory (docs/DESIGN.md §13): results must be re-derivable from config."""

    def run() -> list[str]:
        cfg = small_config()
        rng = np.random.default_rng(4242)
        population = build_population(cfg.benign, rng)
        return [str(e) for e in generate_benign(cfg, population, rng)]

    assert run() == run()


def test_different_seeds_produce_different_traffic() -> None:
    def run(seed: int) -> int:
        cfg = small_config()
        rng = np.random.default_rng(seed)
        population = build_population(cfg.benign, rng)
        return len(list(generate_benign(cfg, population, rng)))

    assert run(1) != run(2)


# --------------------------------------------------------------------------- event stream


def test_events_are_chronologically_ordered() -> None:
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    assert events
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_events_are_well_formed_ai_layer_records() -> None:
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    for event in events[:500]:
        assert event.layer is Layer.AI
        assert event.actor_type is ActorType.API_KEY
        assert event.target_type is TargetType.MODEL
        assert event.label == BENIGN_LABEL
        assert event.source_ip is not None
        assert event.session_id is not None


def test_all_events_fall_inside_the_observation_period() -> None:
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    end = START.replace(hour=23, minute=59, second=59)
    assert events[0].timestamp >= START
    assert events[-1].timestamp <= end.replace(day=START.day + cfg.days - 1)


def test_benign_traffic_stays_within_each_key_profile() -> None:
    """Benign events never touch a model or IP outside the key's profile.

    This is what makes the stolen-key signature meaningful: novelty in the attack window is
    genuinely novel, not just an unlucky draw from benign behaviour.
    """
    cfg, population, rng = build()
    profiles = {k.key_id: k for k in population.keys}  # type: ignore[attr-defined]
    for event in generate_benign(cfg, population, rng):  # type: ignore[arg-type]
        key = profiles[event.actor_id]
        assert event.target_id in key.model_affinity
        assert event.source_ip in key.home_ips


# --------------------------------------------------------------------------- rates


def test_outcome_rates_match_configuration() -> None:
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    counts = collections.Counter(e.action for e in events)
    total = len(events)

    assert counts[Action.ERROR] / total == pytest.approx(cfg.benign.error_rate, abs=0.006)
    assert counts[Action.REFUSAL] / total == pytest.approx(cfg.benign.refusal_rate, abs=0.006)


def test_refusals_are_distinct_from_errors() -> None:
    """A guardrail declining a request is a different relational fact from a platform failure."""
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    refusals = [e for e in events if e.action is Action.REFUSAL]
    errors = [e for e in events if e.action is Action.ERROR]
    assert refusals and errors
    assert all(e.outcome is Outcome.REFUSE for e in refusals)
    assert all(e.outcome is Outcome.ERROR for e in errors)


# --------------------------------------------------------------------------- realism


def test_off_hours_activity_is_legitimately_present() -> None:
    """Automation runs at 03:00, so "off-hours" alone must not be an attack signal.

    The log-vs-graph comparison depends on this: a naive off-hours rule has to be able to produce
    false positives, or the comparison proves nothing.
    """
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    night = [e for e in events if e.timestamp.hour in (2, 3, 4)]
    assert len(night) / len(events) > 0.05

    batch_ids = {k.key_id for k in population.keys if k.persona.name == "batch_job"}  # type: ignore[attr-defined]
    assert {e.actor_id for e in night} & batch_ids


def test_volume_spans_orders_of_magnitude_across_keys() -> None:
    """Legitimate volume must vary widely, or a flat volume threshold would look perfect.

    Two distinct properties matter here, and both work against a naive rule:

    1. The busiest key does far more than the quietest, so no single cut-off is natural.
    2. The busiest key is not a lone spike — several legitimate keys sit near the top — so a
       threshold tuned to catch a boosted stolen key necessarily sweeps up real accounts too.
    """
    cfg, population, rng = build()
    events = list(generate_benign(cfg, population, rng))  # type: ignore[arg-type]
    counts = sorted(collections.Counter(e.actor_id for e in events).values())

    assert counts[-1] > 20 * counts[0]

    near_top = [c for c in counts if c > 0.5 * counts[-1]]
    assert len(near_top) >= 3


def test_weekend_activity_is_damped_for_human_personas() -> None:
    cfg, population, rng = build()
    plans = plan_benign_calls(cfg, population, rng)  # type: ignore[arg-type]
    human = [p for p in plans if p.key.persona.respects_weekend]

    weekday = [p for p in human if p.timestamp.weekday() < 5]
    weekend = [p for p in human if p.timestamp.weekday() >= 5]
    assert weekend, "the observation period should span a weekend"

    per_weekday_day = len(weekday) / 5
    per_weekend_day = len(weekend) / 2
    assert per_weekend_day < per_weekday_day


def test_automation_ignores_weekends() -> None:
    cfg, population, rng = build()
    assert PERSONAS["batch_job"].respects_weekend is False

    plans = plan_benign_calls(cfg, population, rng)  # type: ignore[arg-type]
    batch = [p for p in plans if p.key.persona.name == "batch_job"]
    weekday = [p for p in batch if p.timestamp.weekday() < 5]
    weekend = [p for p in batch if p.timestamp.weekday() >= 5]

    per_weekday_day = len(weekday) / 5
    per_weekend_day = len(weekend) / 2
    assert per_weekend_day == pytest.approx(per_weekday_day, rel=0.25)
