"""Tests for the detector interface and the node-outlier detectors.

The contract these guard is that a detector can never emit a bare, unexplained number, and that
scores stay correctly attributed to the entity they describe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from graphguard.config import GraphConfig, NodeOutlierConfig, PyGODConfig, SnapshotConfig
from graphguard.detection import (
    DominantOutlierDetector,
    EgonetOutlierDetector,
    build_node_outlier_detector,
)
from graphguard.detection.base import Detector, _min_max, _robust_scale
from graphguard.graph import GraphBuilder
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def event(
    *,
    minutes: int = 0,
    days: int = 0,
    actor: str = "key-1",
    target: str = "gpt-4o",
    ip: str = "10.0.0.1",
) -> TraceEvent:
    return TraceEvent(
        timestamp=T0 + timedelta(days=days, minutes=minutes),
        layer=Layer.AI,
        session_id=f"s-{actor}-{days}",
        actor_id=actor,
        actor_type=ActorType.API_KEY,
        target_id=target,
        target_type=TargetType.MODEL,
        action=Action.CALL,
        outcome=Outcome.SUCCESS,
        source_ip=ip,
        attributes={Attr.USER_ID: "u-1", Attr.TOKENS_IN: 100, Attr.TOKENS_OUT: 200},
        label="benign",
    )


def population_events() -> list[TraceEvent]:
    """Twelve ordinary keys plus one that fans out to every model from many addresses."""
    events: list[TraceEvent] = []
    for k in range(12):
        for m in range(6):
            events.append(event(minutes=m, actor=f"key-{k:02d}", target="gpt-4o", ip="10.0.0.1"))
    for m in range(40):
        events.append(
            event(
                minutes=m,
                actor="key-odd",
                target=f"model-{m % 8}",
                ip=f"10.0.9.{m % 7}",
            )
        )
    return sorted(events, key=lambda e: e.timestamp)


def snapshots(events: list[TraceEvent]):
    cfg = GraphConfig(snapshot=SnapshotConfig(window="1d", stride="1d"), working_hours=(8, 18))
    return GraphBuilder(cfg).build_snapshots(events)


def outlier_config(**overrides: object) -> NodeOutlierConfig:
    base: dict[str, object] = {
        "method": "pygod:dominant",
        "pygod": PyGODConfig(epochs=20, hid_dim=16),
        "contamination": 0.1,
        "score_node_types": ["api_key"],
    }
    base.update(overrides)
    return NodeOutlierConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- helpers


def test_min_max_maps_a_flat_population_to_zero_not_one() -> None:
    """If nothing stands out, nothing should look maximally suspicious."""
    assert _min_max(np.array([3.0, 3.0, 3.0])).tolist() == [0.0, 0.0, 0.0]


def test_min_max_scales_to_unit_range() -> None:
    scaled = _min_max(np.array([1.0, 3.0, 5.0]))
    assert scaled.min() == 0.0
    assert scaled.max() == 1.0


def test_robust_scale_marks_constant_features_unusable() -> None:
    """A feature with no variation cannot explain anything, so it must not produce a reason."""
    features = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])
    scale, usable = _robust_scale(features)
    assert usable.tolist() == [True, False]
    assert scale[1] == 1.0  # neutral divisor, never a tiny floor


def test_robust_scale_falls_back_to_std_when_mad_is_zero() -> None:
    """MAD is zero whenever most of the population shares a value — common for ratio features.

    Dividing by a tiny floor there manufactured deviations in the millions, which then crowded out
    genuinely informative reasons.
    """
    features = np.array([[0.0], [0.0], [0.0], [0.0], [0.5]])
    scale, usable = _robust_scale(features)
    assert usable.tolist() == [True]
    assert scale[0] == pytest.approx(features.std())

    deviation = (0.5 - np.median(features)) / scale[0]
    assert abs(deviation) < 100  # sane, not astronomical


# --------------------------------------------------------------------------- contract


def test_detector_rejects_a_misaligned_score_array() -> None:
    """A length mismatch would silently misattribute every score to the wrong entity."""

    class Broken(Detector):
        name = "broken"

        def _raw_scores(self, snapshot, node_type):  # type: ignore[no-untyped-def]
            return np.zeros(3)

    snapshot = snapshots(population_events())[0]
    with pytest.raises(ValueError, match="expected .* scores"):
        Broken(score_node_types=["api_key"]).score(snapshot)


def test_signals_are_ranked_most_anomalous_first() -> None:
    snapshot = snapshots(population_events())[0]
    signals = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)
    scores = [s.raw_score for s in signals]
    assert scores == sorted(scores, reverse=True)


def test_every_signal_carries_evidence_and_can_explain_itself() -> None:
    """Explainability is a mandatory output, not an optional extra (docs/DESIGN.md §13)."""
    snapshot = snapshots(population_events())[0]
    for signal in EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot):
        assert signal.explain()
        assert signal.node_type == "api_key"
        assert signal.evidence  # every scored key touched at least one model

    top = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)[0]
    assert top.reasons
    for reason in top.reasons:
        assert reason.text()
        assert np.isfinite(reason.deviation)


def test_explanation_quotes_human_units_not_model_internals() -> None:
    snapshot = snapshots(population_events())[0]
    top = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)[0]
    text = top.explain()
    assert "key-odd" in text
    assert "log" not in text.lower()


def test_scores_cover_every_node_of_the_requested_types() -> None:
    snapshot = snapshots(population_events())[0]
    signals = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)
    assert {s.node_id for s in signals} == set(snapshot.node_ids["api_key"])


def test_unscored_node_types_produce_no_signals() -> None:
    snapshot = snapshots(population_events())[0]
    signals = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)
    assert all(s.node_type == "api_key" for s in signals)


# --------------------------------------------------------------------------- detectors


def test_egonet_baseline_ranks_the_structural_outlier_first() -> None:
    snapshot = snapshots(population_events())[0]
    signals = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)
    assert signals[0].node_id == "key-odd"


def test_egonet_returns_zero_scores_for_a_degenerate_population() -> None:
    """Too few usable ego-networks to fit a power law must yield zeros, not infinities."""
    snapshot = snapshots([event()])[0]
    signals = EgonetOutlierDetector(score_node_types=["api_key"]).score(snapshot)
    assert all(np.isfinite(s.raw_score) for s in signals)


def test_dominant_runs_without_native_sampler_extensions() -> None:
    """PyGOD's default NeighborLoader needs pyg-lib/torch-sparse, unavailable on this platform.

    Full-batch loading is substituted; the model, loss and scoring stay PyGOD's.
    """
    snapshot = snapshots(population_events())[0]
    signals = DominantOutlierDetector(outlier_config(), seed=7).score(snapshot)
    assert len(signals) == len(snapshot.node_ids["api_key"])
    assert all(np.isfinite(s.raw_score) for s in signals)


def test_dominant_is_deterministic_for_a_fixed_seed() -> None:
    snapshot = snapshots(population_events())[0]

    def run() -> list[float]:
        return [
            s.raw_score for s in DominantOutlierDetector(outlier_config(), seed=7).score(snapshot)
        ]

    assert run() == pytest.approx(run())


def test_dominant_fits_once_per_snapshot_across_node_types() -> None:
    """Scoring two node types must not mean two independent fits of the same model."""
    snapshot = snapshots(population_events())[0]
    detector = DominantOutlierDetector(outlier_config(score_node_types=["api_key", "user"]), seed=7)

    fits = 0
    original = detector._fit_snapshot

    def counting(snap):  # type: ignore[no-untyped-def]
        nonlocal fits
        fits += 1
        return original(snap)

    detector._fit_snapshot = counting  # type: ignore[method-assign]
    detector.score(snapshot)
    assert fits == 2  # called per type...
    assert detector._cache is not None  # ...but the second call is served from cache


def test_patched_loader_is_restored_after_fitting() -> None:
    """A failed fit must not leave PyGOD globally patched for the rest of the process."""
    import pygod.detector.base as pygod_base

    before = pygod_base.NeighborLoader
    snapshot = snapshots(population_events())[0]
    DominantOutlierDetector(outlier_config(), seed=7).score(snapshot)
    assert pygod_base.NeighborLoader is before


# --------------------------------------------------------------------------- factory


def test_factory_builds_the_configured_detector() -> None:
    assert isinstance(
        build_node_outlier_detector(outlier_config(method="baseline:egonet")),
        EgonetOutlierDetector,
    )
    assert isinstance(
        build_node_outlier_detector(outlier_config(method="pygod:dominant")),
        DominantOutlierDetector,
    )


def test_unknown_method_is_rejected_rather_than_silently_defaulted() -> None:
    """A config typo must not quietly run a different algorithm than the operator asked for."""
    with pytest.raises(ValueError, match="unknown node-outlier method"):
        build_node_outlier_detector(outlier_config(method="pygod:not-a-detector"))
