"""Tests for the evaluation harness and the log-vs-graph comparison."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from graphguard.config import (
    BenignConfig,
    EvaluationConfig,
    GraphGuardConfig,
    HTTPTrafficConfig,
    LogVsGraphConfig,
    PyGODConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.detection.base import Signal
from graphguard.eval import compare, evaluate, run_experiment
from graphguard.eval.metrics import label_signals
from graphguard.graph.snapshots import Window
from graphguard.simulator.labels import GroundTruth, ScenarioLabel

T0 = datetime(2026, 3, 2, tzinfo=UTC)


def window(index: int) -> Window:
    return Window(index=index, start=T0 + timedelta(days=index), end=T0 + timedelta(days=index + 1))


def signal(node_id: str, window_index: int, score: float) -> Signal:
    return Signal(
        detector="test",
        node=("api_key", node_id),
        window=window(window_index),
        raw_score=score,
        normalised_score=score,
        reasons=(),
        evidence=(),
    )


def truth_for(node_id: str, start_day: int, end_day: int) -> GroundTruth:
    return GroundTruth.from_labels(
        [
            ScenarioLabel(
                scenario="stolen_key",
                entity_type="api_key",
                entity_id=node_id,
                start=T0 + timedelta(days=start_day),
                end=T0 + timedelta(days=end_day),
            )
        ]
    )


# --------------------------------------------------------------------------- labelling


def test_labels_join_on_node_and_window() -> None:
    """A compromised key is a positive only in windows where the compromise was active."""
    truth = truth_for("k-bad", start_day=2, end_day=4)
    observations = label_signals(
        [signal("k-bad", 1, 0.9), signal("k-bad", 2, 0.9), signal("k-good", 2, 0.1)],
        truth,
    )
    assert [o.is_malicious for o in observations] == [False, True, False]
    assert observations[1].scenario == "stolen_key"


# --------------------------------------------------------------------------- metrics


def test_perfect_ranking_scores_perfectly() -> None:
    truth = truth_for("k-bad", 0, 2)
    signals = [
        signal("k-bad", 0, 1.0),
        signal("k-bad", 1, 0.9),
        signal("k-a", 0, 0.2),
        signal("k-b", 0, 0.1),
    ]
    result = evaluate("test", signals, truth, k_values=[2, 4])
    assert result.roc_auc == 1.0
    assert result.average_precision == 1.0
    assert result.precision_at_k[2] == 1.0
    assert result.recall_at_k[2] == 1.0
    assert result.first_hit_rank == 1


def test_inverted_ranking_scores_zero_auc() -> None:
    truth = truth_for("k-bad", 0, 2)
    signals = [
        signal("k-bad", 0, 0.0),
        signal("k-a", 0, 0.5),
        signal("k-b", 0, 1.0),
    ]
    result = evaluate("test", signals, truth, k_values=[1])
    assert result.roc_auc == 0.0
    assert result.first_hit_rank == 3


def test_single_class_reports_none_rather_than_a_fabricated_auc() -> None:
    truth = GroundTruth()
    result = evaluate("test", [signal("k-a", 0, 0.5)], truth, k_values=[1])
    assert result.roc_auc is None
    assert result.average_precision is None
    assert result.first_hit_rank is None


def test_k_larger_than_population_is_truncated_not_crashed() -> None:
    truth = truth_for("k-bad", 0, 2)
    result = evaluate("test", [signal("k-bad", 0, 1.0)], truth, k_values=[50])
    assert result.precision_at_k[50] == 1.0


# --------------------------------------------------------------------------- comparison


def test_unknown_baseline_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown baseline"):
        compare(
            [],
            [],
            GroundTruth(),
            {},
            baselines=["clairvoyance"],
            working_hours=(8, 18),
            k_values=[5],
        )


# --------------------------------------------------------------------------- end to end


def small_experiment_config() -> GraphGuardConfig:
    return GraphGuardConfig(
        run=RunConfig(seed=11, output_dir="runs"),
        simulator=SimulatorConfig(
            start=T0,
            days=6,
            benign=BenignConfig(
                n_users=12,
                persona_mix={"researcher": 0.3, "app_developer": 0.4, "batch_job": 0.3},
            ),
            # All three scenarios enabled: this fixture exercises the full detector-vs-scenario
            # matrix end to end. The drift cohort is sized to fit this small population.
            # AI-layer isolation: benign gateway traffic would add ip/endpoint
            # vertices and change the graph these assertions characterise.
            http=HTTPTrafficConfig(enabled=False),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(n_victims=1, onset_fraction=0.5),
                slow_group_drift=SlowGroupDriftConfig(cohort_size=4, onset_fraction=0.2),
            ),
        ),
        evaluation=EvaluationConfig(
            k_values=[5, 10],
            log_vs_graph=LogVsGraphConfig(
                baselines=["volume_threshold", "error_rate_threshold", "off_hours_rule"]
            ),
        ),
    )


@pytest.fixture(scope="module")
def experiment(tmp_path_factory: pytest.TempPathFactory):
    """One full pipeline run shared by the assertions below; DOMINANT is trimmed for speed."""
    cfg = small_experiment_config()
    cfg.run.output_dir = tmp_path_factory.mktemp("runs")
    cfg.detection.node_outlier.pygod = PyGODConfig(epochs=15, hid_dim=16)
    return run_experiment(cfg, run_name="test-run")


def test_experiment_evaluates_all_methods_on_the_same_grid(experiment) -> None:
    results = experiment.comparison.results
    assert set(results) == {
        "baseline:volume_threshold",
        "baseline:error_rate_threshold",
        "baseline:off_hours_rule",
        "node_outlier:egonet",
        "node_outlier:dominant",
        "abnormal_subgraph:similarity",
        "community_drift:codrift",
    }
    grid_sizes = {r.n_observations for r in results.values()}
    assert len(grid_sizes) == 1, "methods were scored on different (node, window) grids"
    assert all(r.n_positives > 0 for r in results.values())


def test_experiment_finds_the_victim(experiment) -> None:
    """Acceptance test: node-outlier ranks the stolen key first, scored against that scenario.

    Scoped to ``stolen_key`` deliberately. A pooled figure would score this detector against the
    coordinated ring too — an attack mode it is not built for and correctly stays quiet on — and
    the resulting number would describe neither scenario (it fell to 0.19 when pooled).
    """
    egonet = experiment.comparison.per_scenario["node_outlier:egonet"]["stolen_key"]
    assert egonet.first_hit_rank == 1
    assert egonet.roc_auc is not None and egonet.roc_auc > 0.8


def test_each_detector_wins_on_the_attack_mode_it_targets(experiment) -> None:
    """The detector-vs-scenario matrix is the honest reporting unit (docs/DESIGN.md §3.2).

    Node-outlier owns the stolen key; abnormal-subgraph owns the coordinated ring. Neither is
    expected to cover the other, and the matrix is what makes that visible instead of hiding it
    inside one averaged score.
    """
    matrix = experiment.comparison.matrix("roc_auc")

    # Stated relatively, not against fixed thresholds: absolute AUC moves with dataset scale
    # (a smaller ring is genuinely harder), but *which* detector owns *which* attack mode is the
    # structural claim and must hold regardless.
    ring_scores = {name: by["coordinated_ring"] for name, by in matrix.items()}
    assert max(ring_scores, key=lambda n: ring_scores[n] or 0.0) == "abnormal_subgraph:similarity"

    # ...and each is unremarkable on the other's territory, which is the point.
    assert matrix["node_outlier:egonet"]["stolen_key"] > 0.8
    assert matrix["node_outlier:egonet"]["coordinated_ring"] < 0.6
    assert (
        matrix["node_outlier:egonet"]["stolen_key"]
        > matrix["abnormal_subgraph:similarity"]["stolen_key"]
    )


def test_run_directory_is_a_self_contained_artifact(experiment) -> None:
    """A saved run must carry config, dataset, metrics and rankings together."""
    run_dir = experiment.run_dir
    assert run_dir is not None
    for filename in ("config.yaml", "dataset.json", "metrics.json", "ranking.json"):
        assert (run_dir / filename).exists(), f"missing {filename}"

    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert "node_outlier:egonet" in metrics["pooled"]
    assert "stolen_key" in metrics["per_scenario"]["node_outlier:egonet"]

    ranking = json.loads((run_dir / "ranking.json").read_text())
    top = ranking["node_outlier:egonet"][0]
    assert top["rank"] == 1
    assert top["explanation"], "top-ranked rows must carry their alert explanation"
