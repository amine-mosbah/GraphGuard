"""Tests for cross-layer attack-chain correlation (docs/DESIGN.md §7).

The claim under test is that a finding can be a *path* rather than an event — and, just as
importantly, that the correlation distinguishes an external attacker who breached from one who
merely tried.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graphguard.config import (
    BenignConfig,
    CoordinatedRingConfig,
    CredentialStuffingConfig,
    CrossLayerConfig,
    ExternalToInternalConfig,
    GraphGuardConfig,
    HTTPTrafficConfig,
    PyGODConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.correlation import find_attack_chains
from graphguard.eval.runner import run_experiment
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent

T0 = datetime(2026, 3, 2, tzinfo=UTC)


def http_event(
    *, ip: str, at: datetime, outcome: Outcome, action: Action = Action.LOGIN
) -> TraceEvent:
    return TraceEvent(
        timestamp=at,
        layer=Layer.HTTP,
        actor_id=ip,
        actor_type=ActorType.IP,
        target_id="/v1/auth/login",
        target_type=TargetType.ENDPOINT,
        action=action,
        outcome=outcome,
        source_ip=ip,
        attributes={Attr.STATUS_CODE: 401 if outcome is Outcome.BLOCK else 200},
    )


def ai_event(*, key: str, ip: str, at: datetime) -> TraceEvent:
    return TraceEvent(
        timestamp=at,
        layer=Layer.AI,
        actor_id=key,
        actor_type=ActorType.API_KEY,
        target_id="gpt-4o",
        target_type=TargetType.MODEL,
        action=Action.CALL,
        outcome=Outcome.SUCCESS,
        source_ip=ip,
        attributes={Attr.TOKENS_IN: 10, Attr.TOKENS_OUT: 20},
    )


def brute_force(ip: str, at: datetime, count: int = 60) -> list[TraceEvent]:
    return [
        http_event(ip=ip, at=at + timedelta(seconds=i * 10), outcome=Outcome.BLOCK)
        for i in range(count)
    ]


def cfg() -> CrossLayerConfig:
    return CrossLayerConfig(min_failed_logins=40, max_pivot_hours=72, min_internal_score=0.5)


# --------------------------------------------------------------------------- unit behaviour


def test_a_brute_force_with_no_pivot_yields_no_chain() -> None:
    """The control. Every internet-facing gateway is brute-forced hourly; that is not a breach.

    Reporting a chain here would raise an attack-chain alert for every failed attack on the
    internet, which is the failure mode that would make the feature useless in practice.
    """
    events = brute_force("203.0.113.40", T0)
    assert find_attack_chains(events, {}, cfg()) == []


def test_a_pivot_after_a_brute_force_yields_a_chain() -> None:
    events = brute_force("203.0.113.41", T0)
    events.append(
        http_event(ip="203.0.113.41", at=T0 + timedelta(hours=1), outcome=Outcome.SUCCESS)
    )
    events.append(ai_event(key="key-9", ip="203.0.113.41", at=T0 + timedelta(hours=6)))

    chains = find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg())
    assert len(chains) == 1
    assert chains[0].external_ip == "203.0.113.41"
    assert chains[0].api_key == "key-9"
    assert [s.stage for s in chains[0].steps] == ["external brute force", "breach", "pivot"]


def test_a_credential_already_present_is_not_a_pivot() -> None:
    """Novelty of the association is the discriminator, not its existence.

    Legitimate credentials use their office address constantly. If mere association counted, an
    attacker brute-forcing from a shared corporate address would implicate every colleague.
    """
    events = [ai_event(key="key-resident", ip="10.0.0.7", at=T0 - timedelta(days=2))]
    events += brute_force("10.0.0.7", T0)
    events.append(ai_event(key="key-resident", ip="10.0.0.7", at=T0 + timedelta(hours=2)))

    assert find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg()) == []


def test_a_pivot_long_after_the_burst_is_not_linked() -> None:
    events = brute_force("203.0.113.42", T0)
    events.append(ai_event(key="key-late", ip="203.0.113.42", at=T0 + timedelta(hours=200)))
    assert find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg()) == []


def test_a_burst_below_threshold_is_ignored() -> None:
    events = brute_force("203.0.113.43", T0, count=5)
    events.append(ai_event(key="key-x", ip="203.0.113.43", at=T0 + timedelta(hours=2)))
    assert find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg()) == []


def test_disabled_correlation_returns_nothing() -> None:
    events = brute_force("203.0.113.41", T0)
    events.append(ai_event(key="key-9", ip="203.0.113.41", at=T0 + timedelta(hours=6)))
    disabled = CrossLayerConfig(enabled=False)
    assert find_attack_chains(events, {}, disabled) == []


def test_consecutive_hours_merge_into_one_burst() -> None:
    """A four-hour attack is one incident, not four alerts."""
    events: list[TraceEvent] = []
    for hour in range(4):
        events += brute_force("203.0.113.44", T0 + timedelta(hours=hour))
    events.append(ai_event(key="key-m", ip="203.0.113.44", at=T0 + timedelta(hours=6)))

    chains = find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg())
    assert len(chains) == 1
    assert chains[0].steps[0].detail.startswith("240 blocked")


def test_chain_steps_are_chronological_and_explainable() -> None:
    events = brute_force("203.0.113.41", T0)
    events.append(
        http_event(ip="203.0.113.41", at=T0 + timedelta(hours=1), outcome=Outcome.SUCCESS)
    )
    events.append(ai_event(key="key-9", ip="203.0.113.41", at=T0 + timedelta(hours=6)))

    chain = find_attack_chains(sorted(events, key=lambda e: e.timestamp), {}, cfg())[0]
    times = [s.at for s in chain.steps]
    assert times == sorted(times)

    text = chain.explain()
    assert "203.0.113.41" in text and "key-9" in text
    for step in chain.steps:
        assert step.detail
    assert chain.to_dict()["steps"]


def test_confidence_grows_with_the_evidence_present() -> None:
    """The score must be a transparent sum of named evidence, not an opaque number."""
    base = brute_force("203.0.113.45", T0)
    base.append(ai_event(key="key-p", ip="203.0.113.45", at=T0 + timedelta(hours=3)))
    without_breach = find_attack_chains(sorted(base, key=lambda e: e.timestamp), {}, cfg())[0]

    with_breach_events = [
        *base,
        http_event(ip="203.0.113.45", at=T0 + timedelta(hours=1), outcome=Outcome.SUCCESS),
    ]
    with_breach = find_attack_chains(
        sorted(with_breach_events, key=lambda e: e.timestamp), {}, cfg()
    )[0]

    assert with_breach.score > without_breach.score
    assert 0.0 <= without_breach.score <= 1.0


# --------------------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def experiment():
    cfg_full = GraphGuardConfig(
        run=RunConfig(seed=4),
        simulator=SimulatorConfig(
            start=T0,
            days=8,
            benign=BenignConfig(
                n_users=16,
                persona_mix={"researcher": 0.3, "app_developer": 0.5, "casual": 0.2},
            ),
            http=HTTPTrafficConfig(enabled=True),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(enabled=False),
                coordinated_ring=CoordinatedRingConfig(enabled=False),
                slow_group_drift=SlowGroupDriftConfig(enabled=False),
                credential_stuffing=CredentialStuffingConfig(enabled=True, onset_fraction=0.3),
                external_to_internal=ExternalToInternalConfig(enabled=True, onset_fraction=0.5),
            ),
        ),
    )
    cfg_full.detection.node_outlier.pygod = PyGODConfig(epochs=10, hid_dim=16)
    return cfg_full, run_experiment(cfg_full, save=False)


def test_the_chain_scenario_is_found_end_to_end(experiment) -> None:
    config, result = experiment
    chains = find_attack_chains(
        list(result.dataset.events), result.signals, config.correlation.cross_layer
    )
    truth = {label.entity_id: label.scenario for label in result.dataset.ground_truth.labels}

    assert chains, "the injected external-to-internal chain was not found"
    top = chains[0]
    assert truth.get(top.external_ip) == "external_to_internal"
    assert truth.get(top.api_key) == "external_to_internal"


def test_the_stuffing_only_attacker_produces_no_chain(experiment) -> None:
    """The scenario that brute-forces but never breaches must not yield a chain."""
    config, result = experiment
    chains = find_attack_chains(
        list(result.dataset.events), result.signals, config.correlation.cross_layer
    )
    truth = {label.entity_id: label.scenario for label in result.dataset.ground_truth.labels}
    stuffing_ips = {k for k, v in truth.items() if v == "credential_stuffing"}

    assert stuffing_ips, "the control scenario did not run"
    assert not {c.external_ip for c in chains} & stuffing_ips


def test_no_innocent_address_is_implicated(experiment) -> None:
    config, result = experiment
    chains = find_attack_chains(
        list(result.dataset.events), result.signals, config.correlation.cross_layer
    )
    truth = {label.entity_id: label.scenario for label in result.dataset.ground_truth.labels}
    assert all(truth.get(c.external_ip) == "external_to_internal" for c in chains)
