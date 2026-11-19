"""Tests for the slow-group-drift scenario and the community-drift detector.

The claim under test is the third of docs/DESIGN.md §3.2: a group can migrate without any individual
member ever looking unusual in any single window, and only a detector comparing groups *across*
snapshots can see it.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from graphguard.config import (
    AbnormalSubgraphConfig,
    BenignConfig,
    CommunityDriftConfig,
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
from graphguard.detection import build_community_drift_detector
from graphguard.detection.community_drift import _distribution_delta
from graphguard.graph import GraphBuilder
from graphguard.simulator.dataset import generate_dataset
from graphguard.simulator.scenarios.slow_group_drift import SCENARIO_NAME

START = datetime(2026, 3, 2, tzinfo=UTC)
DAYS = 14
COHORT = 10


def make_config(**drift_overrides: object) -> GraphGuardConfig:
    drift: dict[str, object] = {
        "enabled": True,
        "cohort_size": COHORT,
        "onset_fraction": 0.25,
        "final_model_shift": 0.8,
        "final_hour_shift": 5.0,
    }
    drift.update(drift_overrides)
    cfg = GraphGuardConfig(
        run=RunConfig(seed=5),
        simulator=SimulatorConfig(
            start=START,
            days=DAYS,
            benign=BenignConfig(
                n_users=40,
                persona_mix={"researcher": 0.25, "app_developer": 0.5, "casual": 0.25},
            ),
            # Other scenarios off: these tests characterise drift in isolation.
            # AI-layer isolation: benign gateway traffic would add ip/endpoint
            # vertices and change the graph these assertions characterise.
            http=HTTPTrafficConfig(enabled=False),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(enabled=False),
                coordinated_ring=CoordinatedRingConfig(enabled=False),
                slow_group_drift=SlowGroupDriftConfig(**drift),  # type: ignore[arg-type]
                credential_stuffing=CredentialStuffingConfig(enabled=False),
                external_to_internal=ExternalToInternalConfig(enabled=False),
            ),
        ),
    )
    cfg.detection.abnormal_subgraph = AbnormalSubgraphConfig(enabled=False)
    cfg.detection.community_drift = CommunityDriftConfig()
    return cfg


@pytest.fixture(scope="module")
def scenario():
    cfg = make_config()
    dataset = generate_dataset(cfg)
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))
    cohort = {
        label.entity_id for label in dataset.ground_truth.labels if label.scenario == SCENARIO_NAME
    }
    onset = START + timedelta(days=int(DAYS * 0.25))
    return cfg, dataset, snapshots, cohort, onset


# --------------------------------------------------------------------------- helpers


def test_displacement_magnitude_reads_as_mass_moved() -> None:
    """Half the L1 norm of a distribution delta is the share of mass that moved."""
    delta = _distribution_delta({"a": 0.4, "b": 0.6}, {"a": 0.7, "b": 0.3})
    assert 0.5 * float(np.abs(delta).sum()) == pytest.approx(0.3)

    unchanged = _distribution_delta({"a": 1.0}, {"a": 1.0})
    assert 0.5 * float(np.abs(unchanged).sum()) == pytest.approx(0.0)


def test_distribution_delta_aligns_dimensions_across_entities() -> None:
    """Misaligned dimensions would make the cosine compare unrelated services."""
    left = _distribution_delta({"b": 1.0}, {"a": 1.0})
    right = _distribution_delta({"a": 1.0}, {"b": 1.0})
    assert left.tolist() == [-1.0, 1.0]
    assert right.tolist() == [1.0, -1.0]
    assert np.dot(left, right) < 0  # opposite directions, as they should be


# --------------------------------------------------------------------------- scenario


def test_disabled_drift_changes_nothing() -> None:
    dataset = generate_dataset(make_config(enabled=False))
    assert not any(e.label == SCENARIO_NAME for e in dataset.events)


def test_cohort_is_labelled_from_onset(scenario) -> None:
    _, dataset, _, cohort, onset = scenario
    assert len(cohort) == COHORT
    drift_events = [e for e in dataset.events if e.label == SCENARIO_NAME]
    assert drift_events
    assert min(e.timestamp for e in drift_events) >= onset


def test_drift_does_not_change_cohort_volume(scenario) -> None:
    """Drift rewrites behaviour, it does not add traffic.

    Extra events would raise volume — which is precisely what node-outlier scoring already
    catches — and the scenario would then be solvable by the wrong detector.
    """
    cfg, dataset, _, cohort, onset = scenario
    per_day: dict[int, int] = collections.Counter()
    for event in dataset.events:
        if event.actor_id in cohort:
            per_day[(event.timestamp - START).days] += 1

    weekdays = [
        count for day, count in per_day.items() if (START + timedelta(days=day)).weekday() < 5
    ]
    before = [c for d, c in enumerate(weekdays) if d < 3]
    after = [c for d, c in enumerate(weekdays) if d >= len(weekdays) - 3]
    assert sum(after) / len(after) == pytest.approx(sum(before) / len(before), rel=0.35)


def test_cohort_target_mix_migrates_gradually(scenario) -> None:
    """The ramp must be smooth: a step change would be visible in a single window."""
    _, dataset, _, cohort, _ = scenario
    shares: list[float] = []
    for day in range(DAYS):
        events = [
            e for e in dataset.events if e.actor_id in cohort and (e.timestamp - START).days == day
        ]
        if not events:
            continue
        top = collections.Counter(e.target_id for e in events).most_common(1)[0][1]
        shares.append(top / len(events))

    # Later windows are more concentrated on the migration target than early ones...
    assert sum(shares[-3:]) / 3 > sum(shares[:3]) / 3
    # ...and no single day jumps by a large amount.
    steps = [abs(b - a) for a, b in zip(shares, shares[1:], strict=False)]
    assert max(steps) < 0.35


# --------------------------------------------------------------------------- detection


def test_detector_is_silent_until_it_has_a_baseline(scenario) -> None:
    """With no past, nothing can have drifted; claiming otherwise would flag the whole platform."""
    cfg, _, snapshots, _, _ = scenario
    detector = build_community_drift_detector(cfg.detection.community_drift)

    signals = detector.score_all(snapshots)
    early = [
        s
        for s in signals
        if s.window.index < cfg.detection.community_drift.baseline_windows and s.raw_score > 0
    ]
    assert not early


def test_detector_finds_the_drifting_cohort(scenario) -> None:
    cfg, _, snapshots, cohort, onset = scenario
    detector = build_community_drift_detector(cfg.detection.community_drift)
    signals = detector.score_all(snapshots)

    by_window: dict[int, list] = collections.defaultdict(list)
    for signal in signals:
        by_window[signal.window.index].append(signal)

    recalls = []
    for window_index, window_signals in sorted(by_window.items()):
        window = window_signals[0].window
        if window.start < onset + timedelta(days=2):
            continue  # the ramp needs to build before there is anything to see
        flagged = {s.node_id for s in window_signals if s.raw_score > 0}
        recalls.append(len(flagged & cohort) / len(cohort))
        del window_index

    assert recalls
    assert sum(recalls) / len(recalls) >= 0.7


def test_flagged_members_name_their_co_drifting_group(scenario) -> None:
    """The group is the finding, so it must reach the explanation."""
    cfg, _, snapshots, cohort, _ = scenario
    detector = build_community_drift_detector(cfg.detection.community_drift)
    signals = detector.score_all(snapshots)

    flagged = [s for s in signals if s.raw_score > 0 and s.node_id in cohort]
    assert flagged
    top = max(flagged, key=lambda s: s.raw_score)
    assert top.context is not None
    assert "drifting together" in top.context
    assert "drifting together" in top.explain()
    assert len(top.evidence) >= cfg.detection.community_drift.min_community_size


def test_no_single_member_is_an_outlier_in_any_window(scenario) -> None:
    """The premise of the scenario, asserted directly.

    If a cohort member ever became an individual outlier, the scenario would be solvable by
    node-outlier scoring and would no longer test group drift at all.
    """
    cfg, _, snapshots, cohort, _ = scenario
    from graphguard.config import NodeOutlierConfig
    from graphguard.detection import build_node_outlier_detector

    detector = build_node_outlier_detector(
        NodeOutlierConfig(method="baseline:egonet", score_node_types=["api_key"])
    )
    for snapshot in snapshots:
        ranked = [s for s in detector.score(snapshot) if s.node_type == "api_key"]
        if len(ranked) < 10:
            continue
        top_three = {s.node_id for s in ranked[:3]}
        assert not (top_three & cohort), f"cohort member topped window {snapshot.window.index}"


def test_detector_state_resets_between_runs(scenario) -> None:
    cfg, _, snapshots, _, _ = scenario
    detector = build_community_drift_detector(cfg.detection.community_drift)

    first = [s.raw_score for s in detector.score_all(snapshots)]
    second = [s.raw_score for s in detector.score_all(snapshots)]
    assert first == pytest.approx(second)
