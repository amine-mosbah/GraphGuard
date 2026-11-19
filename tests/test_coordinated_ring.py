"""Tests for the coordinated-ring scenario and the abnormal-subgraph detector.

The central claim under test is docs/DESIGN.md §3.2's: abnormal-subgraph detection catches what
node-outlier scoring structurally cannot, because no ring member is individually unusual.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from graphguard.config import (
    AbnormalSubgraphConfig,
    BenignConfig,
    CoordinatedRingConfig,
    CredentialStuffingConfig,
    ExternalToInternalConfig,
    GraphGuardConfig,
    HTTPTrafficConfig,
    NodeOutlierConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.detection import build_abnormal_subgraph_detector, build_node_outlier_detector
from graphguard.detection.abnormal_subgraph import _k_core
from graphguard.graph import GraphBuilder
from graphguard.simulator.dataset import generate_dataset
from graphguard.simulator.scenarios.coordinated_ring import RING_PREFIX, SCENARIO_NAME

START = datetime(2026, 3, 2, tzinfo=UTC)
DAYS = 8
N_ACCOUNTS = 12


def make_config(**ring_overrides: object) -> GraphGuardConfig:
    ring: dict[str, object] = {
        "enabled": True,
        "n_accounts": N_ACCOUNTS,
        "onset_fraction": 0.5,
        "calls_per_day": 60.0,
        "sync_jitter_s": 90.0,
        "bursts_per_day": 6,
    }
    ring.update(ring_overrides)
    cfg = GraphGuardConfig(
        run=RunConfig(seed=11),
        simulator=SimulatorConfig(
            start=START,
            days=DAYS,
            benign=BenignConfig(
                n_users=25,
                persona_mix={
                    "researcher": 0.3,
                    "app_developer": 0.35,
                    "batch_job": 0.2,
                    "casual": 0.15,
                },
            ),
            # AI-layer isolation: benign gateway traffic would add ip/endpoint
            # vertices and change the graph these assertions characterise.
            http=HTTPTrafficConfig(enabled=False),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(enabled=False),
                # Drift off: a migrating cohort converges on identical behaviour and would
                # legitimately register as unison, which is a different finding under test.
                slow_group_drift=SlowGroupDriftConfig(enabled=False),
                credential_stuffing=CredentialStuffingConfig(enabled=False),
                external_to_internal=ExternalToInternalConfig(enabled=False),
                coordinated_ring=CoordinatedRingConfig(**ring),  # type: ignore[arg-type]
            ),
        ),
    )
    cfg.detection.abnormal_subgraph = AbnormalSubgraphConfig(
        similarity_threshold=0.8, min_cluster_size=5
    )
    return cfg


@pytest.fixture(scope="module")
def scenario():
    cfg = make_config()
    dataset = generate_dataset(cfg)
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))
    ring_ids = {
        label.entity_id for label in dataset.ground_truth.labels if label.scenario == SCENARIO_NAME
    }
    onset = START + timedelta(days=int(DAYS * 0.5))
    return cfg, dataset, snapshots, ring_ids, onset


# --------------------------------------------------------------------------- scenario


def test_disabled_ring_injects_nothing() -> None:
    dataset = generate_dataset(make_config(enabled=False))
    assert not any(e.label == SCENARIO_NAME for e in dataset.events)


def test_ring_has_the_configured_membership(scenario) -> None:
    _, dataset, _, ring_ids, _ = scenario
    assert len(ring_ids) == N_ACCOUNTS


def test_ring_members_all_target_one_model(scenario) -> None:
    _, dataset, _, _, _ = scenario
    events = [e for e in dataset.events if e.label == SCENARIO_NAME]
    assert len({e.target_id for e in events}) == 1


def test_each_member_uses_its_own_address(scenario) -> None:
    """A shared IP would collapse the ring into one hub and make detection a degree check."""
    _, dataset, _, ring_ids, _ = scenario
    events = [e for e in dataset.events if e.label == SCENARIO_NAME]
    ips = {e.source_ip for e in events}
    assert len(ips) == len(ring_ids)
    assert all(str(ip).startswith(RING_PREFIX) for ip in ips)


def test_ring_traffic_starts_at_onset(scenario) -> None:
    _, dataset, _, _, onset = scenario
    events = [e for e in dataset.events if e.label == SCENARIO_NAME]
    assert min(e.timestamp for e in events) >= onset


def test_no_member_is_individually_a_volume_outlier(scenario) -> None:
    """The scenario is worthless if members stand out alone — that is node-outlier's job.

    Every ring member must sit below the busiest legitimate accounts.
    """
    _, dataset, _, ring_ids, _ = scenario
    per_key = collections.Counter(e.actor_id for e in dataset.events)
    ring_max = max(per_key[k] for k in ring_ids)
    benign_max = max(v for k, v in per_key.items() if k not in ring_ids)
    assert ring_max < benign_max


def test_members_fire_in_the_same_bursts(scenario) -> None:
    """Synchronicity is the ring's defining property, so assert it directly."""
    _, dataset, _, ring_ids, _ = scenario
    events = [e for e in dataset.events if e.label == SCENARIO_NAME]

    # Bucket each member's activity into 15-minute bins and require high overlap.
    def bins(member: str) -> set[tuple[int, int]]:
        return {
            (e.timestamp.toordinal(), e.timestamp.hour * 4 + e.timestamp.minute // 15)
            for e in events
            if e.actor_id == member
        }

    members = sorted(ring_ids)
    first, second = bins(members[0]), bins(members[1])
    overlap = len(first & second) / min(len(first), len(second))
    assert overlap > 0.5


# --------------------------------------------------------------------------- k-core


def test_k_core_peels_accidental_pairs() -> None:
    """Two similar nodes are noise; a mutually-similar group is a ring."""
    adjacency = np.zeros((7, 7), dtype=bool)
    # An isolated pair...
    adjacency[0, 1] = adjacency[1, 0] = True
    # ...and a 5-clique.
    for i in range(2, 7):
        for j in range(2, 7):
            if i != j:
                adjacency[i, j] = True

    core = _k_core(adjacency, k=4)
    assert not core[0].any() and not core[1].any()
    assert core[2:, 2:].sum() == 20  # the clique survives intact


def test_k_core_is_a_noop_for_k_zero() -> None:
    adjacency = np.array([[False, True], [True, False]])
    assert (_k_core(adjacency, 0) == adjacency).all()


# --------------------------------------------------------------------------- detection


def test_detector_never_flags_an_innocent_key(scenario) -> None:
    """Zero false positives is the strong claim, and it holds at every ring size tested.

    An unison finding accuses a *group*; a false positive therefore smears several innocent
    accounts at once, which is far more costly to an analyst than a missed member.
    """
    cfg, _, snapshots, ring_ids, _ = scenario
    detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)

    for snapshot in snapshots:
        flagged = {s.node_id for s in detector.score(snapshot) if s.raw_score > 0}
        assert not (flagged - ring_ids), f"window {snapshot.window.index}: {flagged - ring_ids}"


def test_detector_is_silent_before_the_ring_exists(scenario) -> None:
    cfg, _, snapshots, _, onset = scenario
    detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)

    for snapshot in snapshots:
        if snapshot.window.end <= onset:
            flagged = [s for s in detector.score(snapshot) if s.raw_score > 0]
            assert not flagged, f"fired before onset in window {snapshot.window.index}"


def test_detector_recovers_most_of_the_ring_while_it_is_active(scenario) -> None:
    """Recall is high but not guaranteed complete — see the size-sensitivity test below."""
    cfg, _, snapshots, ring_ids, onset = scenario
    detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)

    # The window straddling the onset holds only a sliver of ring traffic; whether that is
    # enough to establish unison is genuinely ambiguous, so only fully-active windows count.
    recalls = [
        len({s.node_id for s in detector.score(snapshot) if s.raw_score > 0} & ring_ids)
        / len(ring_ids)
        for snapshot in snapshots
        if snapshot.window.start >= onset
    ]
    assert len(recalls) >= 3, "too few fully-active windows to be a meaningful assertion"
    assert sum(recalls) / len(recalls) >= 0.85


def test_detection_power_grows_with_ring_size() -> None:
    """A documented property, asserted so it cannot regress unnoticed.

    Detection rests on *mutual* corroboration, so it strengthens as the ring grows: each extra
    member adds partners that help hold the k-core together. The corollary is the honest
    limitation — a very small ring can fall below the cluster-size floor and vanish entirely,
    while the rings that matter operationally (dozens of accounts) are the easiest to catch.
    """

    def mean_recall(n_accounts: int) -> float:
        cfg = make_config(n_accounts=n_accounts)
        dataset = generate_dataset(cfg)
        snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))
        detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)
        ring_ids = {
            label.entity_id
            for label in dataset.ground_truth.labels
            if label.scenario == SCENARIO_NAME
        }
        onset = START + timedelta(days=int(DAYS * 0.5))
        recalls = [
            len({s.node_id for s in detector.score(snapshot) if s.raw_score > 0} & ring_ids)
            / len(ring_ids)
            for snapshot in snapshots
            if snapshot.window.start >= onset
        ]
        return sum(recalls) / len(recalls)

    assert mean_recall(20) > mean_recall(8)


def test_flagged_members_name_their_cluster(scenario) -> None:
    """The cluster is the finding, so it must appear in the explanation, not just the score."""
    cfg, _, snapshots, ring_ids, onset = scenario
    detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)

    active = next(s for s in snapshots if s.window.start >= onset)
    flagged = [s for s in detector.score(active) if s.raw_score > 0]
    assert flagged
    top = flagged[0]
    assert top.context is not None
    assert "near-unison" in top.context
    assert "near-unison" in top.explain()
    # Evidence is the cluster itself, not the node's graph neighbours.
    cluster = {node_id for _, node_id in top.evidence}
    assert cluster <= ring_ids
    assert top.node_id in cluster
    assert len(cluster) >= cfg.detection.abnormal_subgraph.min_cluster_size


def test_node_outlier_scoring_does_not_surface_the_ring(scenario) -> None:
    """The point of this detector: per-node scoring is blind here, by construction.

    Ring members are deliberately unremarkable individually, so they should rank in the bulk of
    the population rather than at the top. If this test ever fails, the scenario has drifted into
    making members individually anomalous and no longer tests coordination.
    """
    cfg, _, snapshots, ring_ids, _ = scenario
    outlier_cfg = NodeOutlierConfig(method="baseline:egonet", score_node_types=["api_key"])
    detector = build_node_outlier_detector(outlier_cfg)

    ranked = [s for s in detector.score(snapshots[-1]) if s.node_type == "api_key"]
    ranks = [i for i, s in enumerate(ranked, start=1) if s.node_id in ring_ids]
    assert min(ranks) > len(ranked) // 4, "ring members should not dominate the outlier ranking"


def test_loose_coordination_is_harder_to_detect() -> None:
    """Honest limitation: enough per-member jitter dilutes the unison signature.

    Documented rather than hidden — the report must state that an adversary willing to give up
    coordination tightness can evade this detector.
    """
    cfg = make_config(sync_jitter_s=6 * 3600.0)  # hours of jitter: barely coordinated
    dataset = generate_dataset(cfg)
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))
    detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)

    ring_ids = {
        label.entity_id for label in dataset.ground_truth.labels if label.scenario == SCENARIO_NAME
    }
    detected = {
        s.node_id
        for snapshot in snapshots
        for s in detector.score(snapshot)
        if s.raw_score > 0 and s.node_id in ring_ids
    }
    assert len(detected) < len(ring_ids)
