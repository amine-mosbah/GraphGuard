"""Tests for the stolen-key scenario, ground-truth labelling and dataset assembly.

These assert the *attack signature* itself. If the injected behaviour stops matching what
``docs/THREAT_MODEL.md`` §3 claims, every detection result reported against it is measuring
something other than what the report says it is.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from graphguard.config import (
    BenignConfig,
    CoordinatedRingConfig,
    CredentialStuffingConfig,
    ExternalToInternalConfig,
    GraphGuardConfig,
    HTTPTrafficConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.simulator.dataset import generate_dataset, read_labels, write_dataset
from graphguard.simulator.labels import GroundTruth, ScenarioLabel
from graphguard.simulator.population import build_population
from graphguard.simulator.scenarios.stolen_key import ATTACKER_PREFIX, inject_stolen_key

START = datetime(2026, 3, 2, tzinfo=UTC)
DAYS = 10


def make_config(**stolen_overrides: object) -> GraphGuardConfig:
    stolen: dict[str, object] = {
        "enabled": True,
        "n_victims": 2,
        "onset_fraction": 0.6,
        "volume_multiplier": 8.0,
        "off_hours_bias": 0.85,
        "new_model_ratio": 0.7,
        "error_rate": 0.12,
    }
    stolen.update(stolen_overrides)
    return GraphGuardConfig(
        run=RunConfig(seed=99),
        simulator=SimulatorConfig(
            start=START,
            days=DAYS,
            benign=BenignConfig(
                n_users=20,
                persona_mix={"researcher": 0.4, "app_developer": 0.4, "casual": 0.2},
                working_hours=(8, 18),
            ),
            # The ring is disabled here on purpose: these tests characterise the stolen-key
            # signature in isolation, and a second scenario in the same dataset would both
            # shift the RNG stream and add unrelated attack events to every assertion.
            # AI-layer isolation: benign gateway traffic would add ip/endpoint
            # vertices and change the graph these assertions characterise.
            http=HTTPTrafficConfig(enabled=False),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(**stolen),  # type: ignore[arg-type]
                coordinated_ring=CoordinatedRingConfig(enabled=False),
                credential_stuffing=CredentialStuffingConfig(enabled=False),
                external_to_internal=ExternalToInternalConfig(enabled=False),
                slow_group_drift=SlowGroupDriftConfig(enabled=False),
            ),
        ),
    )


# --------------------------------------------------------------------------- labels


def test_label_interval_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ScenarioLabel("s", "api_key", "k", START, START)


def test_label_covers_overlapping_windows_only() -> None:
    label = ScenarioLabel("s", "api_key", "k", START + timedelta(days=2), START + timedelta(days=5))

    assert label.covers(START + timedelta(days=3), START + timedelta(days=4)) is True
    # Partial overlap counts: that snapshot still contains attack edges.
    assert label.covers(START + timedelta(days=1), START + timedelta(days=3)) is True
    assert label.covers(START, START + timedelta(days=2)) is False
    assert label.covers(START + timedelta(days=5), START + timedelta(days=6)) is False


def test_binary_vector_aligns_with_caller_node_order() -> None:
    truth = GroundTruth.from_labels(
        [ScenarioLabel("s", "api_key", "k-1", START, START + timedelta(days=3))]
    )
    nodes = [("api_key", "k-0"), ("api_key", "k-1"), ("user", "u-0")]
    vector = truth.binary_vector(nodes, START, START + timedelta(days=1))
    assert vector.tolist() == [0, 1, 0]

    later = truth.binary_vector(nodes, START + timedelta(days=5), START + timedelta(days=6))
    assert later.tolist() == [0, 0, 0]


def test_scenario_lookup_reports_which_attack_applies() -> None:
    truth = GroundTruth.from_labels(
        [ScenarioLabel("stolen_key", "api_key", "k-1", START, START + timedelta(days=3))]
    )
    assert truth.scenario_of(("api_key", "k-1"), START, START + timedelta(days=1)) == "stolen_key"
    assert truth.scenario_of(("api_key", "k-9"), START, START + timedelta(days=1)) is None


# --------------------------------------------------------------------------- scenario


def test_disabled_scenario_injects_nothing() -> None:
    cfg = make_config(enabled=False)
    rng = np.random.default_rng(0)
    population = build_population(cfg.simulator.benign, rng)
    plans, labels = inject_stolen_key(cfg.simulator, population, rng)
    assert plans == []
    assert labels == []


def test_requesting_more_victims_than_keys_is_rejected() -> None:
    cfg = make_config()
    rng = np.random.default_rng(0)
    population = build_population(cfg.simulator.benign, rng)
    cfg.simulator.scenarios.stolen_key.n_victims = len(population.keys) + 1
    with pytest.raises(ValueError, match="population has only"):
        inject_stolen_key(cfg.simulator, population, rng)


def test_attack_starts_at_the_configured_onset_and_never_before() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    onset = START + timedelta(days=int(DAYS * 0.6))

    attack_events = [e for e in dataset.events if e.is_attack]
    assert attack_events
    assert min(e.timestamp for e in attack_events) >= onset

    for label in dataset.ground_truth.labels:
        assert label.start == onset
        assert label.end == START + timedelta(days=DAYS)


def test_victim_owner_keeps_working_during_the_compromise() -> None:
    """Superimposed behaviour, not replaced behaviour.

    Silencing the owner would turn detection into "spot the key whose behaviour was replaced",
    which is a much easier problem than the real one.
    """
    cfg = make_config()
    dataset = generate_dataset(cfg)
    onset = START + timedelta(days=int(DAYS * 0.6))

    for label in dataset.ground_truth.labels:
        after = [
            e for e in dataset.events if e.actor_id == label.entity_id and e.timestamp >= onset
        ]
        benign_after = [e for e in after if not e.is_attack]
        assert benign_after, f"{label.entity_id}: owner traffic vanished during the attack"


# --------------------------------------------------------------------------- signature


def test_attack_raises_volume_substantially() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    onset = START + timedelta(days=int(DAYS * 0.6))
    pre_days = (onset - START).days
    post_days = DAYS - pre_days

    for label in dataset.ground_truth.labels:
        events = [e for e in dataset.events if e.actor_id == label.entity_id]
        pre_rate = sum(1 for e in events if e.timestamp < onset) / pre_days
        post_rate = sum(1 for e in events if e.timestamp >= onset) / post_days
        assert post_rate > 3 * pre_rate


def test_attack_traffic_is_biased_to_off_hours() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    attack = [e for e in dataset.events if e.is_attack]
    off_hours = sum(1 for e in attack if e.timestamp.hour < 8 or e.timestamp.hour >= 18)
    assert off_hours / len(attack) == pytest.approx(0.85, abs=0.05)


def test_attack_reaches_models_the_victim_never_used() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)

    for label in dataset.ground_truth.labels:
        key = dataset.population.key_by_id(label.entity_id)
        attack_models = {
            e.target_id for e in dataset.events if e.actor_id == label.entity_id and e.is_attack
        }
        assert attack_models - key.known_models, "attacker stayed inside the victim's usual models"


def test_attack_originates_from_an_external_ip_outside_the_victim_home_set() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)

    for label in dataset.ground_truth.labels:
        key = dataset.population.key_by_id(label.entity_id)
        attack_ips = {
            e.source_ip for e in dataset.events if e.actor_id == label.entity_id and e.is_attack
        }
        assert attack_ips
        assert not (attack_ips & set(key.home_ips))
        assert all(str(ip).startswith(ATTACKER_PREFIX) for ip in attack_ips)


def test_attack_error_rate_exceeds_the_benign_rate() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    attack = [e for e in dataset.events if e.is_attack]
    errors = sum(1 for e in attack if e.action.value == "error")
    assert errors / len(attack) == pytest.approx(0.12, abs=0.03)
    assert errors / len(attack) > cfg.simulator.benign.error_rate


def test_no_single_signal_is_unique_to_the_attack() -> None:
    """Every individual deviation is matched somewhere in benign traffic.

    Only the *conjunction*, relative to the key's own baseline, is anomalous. If any one signal
    were unique to attacks, a one-line grep would solve the problem and the graph would be
    unnecessary.
    """
    cfg = make_config()
    dataset = generate_dataset(cfg)
    benign = [e for e in dataset.events if not e.is_attack]

    # Benign off-hours traffic exists.
    assert any(e.timestamp.hour < 8 or e.timestamp.hour >= 18 for e in benign)
    # Benign errors exist.
    assert any(e.action.value == "error" for e in benign)
    # Benign keys reach high volume too.
    per_key = collections.Counter(e.actor_id for e in benign)
    victims = {label.entity_id for label in dataset.ground_truth.labels}
    innocent_volumes = [v for k, v in per_key.items() if k not in victims]
    assert max(innocent_volumes) > 0.25 * max(per_key.values())


# --------------------------------------------------------------------------- dataset


def test_dataset_events_are_chronological_after_merging_attack_traffic() -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    timestamps = [e.timestamp for e in dataset.events]
    assert timestamps == sorted(timestamps)


def test_dataset_generation_is_reproducible() -> None:
    assert [str(e) for e in generate_dataset(make_config()).events] == [
        str(e) for e in generate_dataset(make_config()).events
    ]


def test_write_dataset_persists_events_labels_and_config(tmp_path) -> None:
    cfg = make_config()
    dataset = generate_dataset(cfg)
    out = write_dataset(dataset, cfg, tmp_path / "run")

    assert (out / "events.jsonl").exists()
    assert (out / "config.yaml").exists()

    restored = read_labels(out / "labels.json")
    assert restored.summary() == dataset.ground_truth.summary()
    assert {label.entity_id for label in restored.labels} == {
        label.entity_id for label in dataset.ground_truth.labels
    }
