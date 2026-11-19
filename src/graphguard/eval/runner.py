"""Experiment runner — one call from config to saved, reproducible results.

Role
----
Orchestrates the full vertical slice: generate (or load) a labelled dataset, build snapshots, run
every enabled detector plus the flat-log baselines, evaluate everything on the same grid, and save
the results **with the config that produced them** into a run directory.

Inputs / outputs
----------------
Input: a validated :class:`~graphguard.config.GraphGuardConfig`.
Output: a :class:`RunResult` in memory and a run directory on disk:

    runs/<name>/
      config.yaml       the exact effective configuration
      dataset.json      dataset summary (counts, period, population)
      metrics.json      every method's EvaluationResult
      ranking.json      top-ranked (node, window) rows per method, for figures

This is the validation/experiment module with saved runs: each run directory is a self-contained
artifact that a write-up can cite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from graphguard.config import GraphGuardConfig
from graphguard.detection import (
    build_abnormal_subgraph_detector,
    build_community_drift_detector,
    build_node_outlier_detector,
)
from graphguard.detection.base import Signal
from graphguard.eval.log_vs_graph import ComparisonResult, compare
from graphguard.eval.metrics import label_signals
from graphguard.graph import GraphBuilder
from graphguard.simulator.dataset import Dataset, generate_dataset
from graphguard.simulator.labels import GroundTruth


@dataclass(frozen=True)
class RunResult:
    """Everything one experiment produced."""

    dataset: Dataset
    comparison: ComparisonResult
    signals: dict[str, list[Signal]]
    run_dir: Path | None


def run_experiment(
    cfg: GraphGuardConfig,
    *,
    save: bool = True,
    run_name: str | None = None,
) -> RunResult:
    """Run the full pipeline and (optionally) persist the results.

    Both node-outlier methods are always run — the learned detector and the classical baseline —
    so every saved run carries the comparison that justifies (or indicts) the model choice.
    """
    dataset = generate_dataset(cfg)
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))
    windows = [s.window for s in snapshots]

    signals: dict[str, list[Signal]] = {}
    outlier_cfg = cfg.detection.node_outlier
    if outlier_cfg.enabled:
        for method in ("baseline:egonet", "pygod:dominant"):
            method_cfg = outlier_cfg.model_copy(update={"method": method})
            detector = build_node_outlier_detector(method_cfg, seed=cfg.run.seed)
            signals[detector.name] = detector.score_all(snapshots)

    if cfg.detection.abnormal_subgraph.enabled:
        subgraph_detector = build_abnormal_subgraph_detector(cfg.detection.abnormal_subgraph)
        signals[subgraph_detector.name] = subgraph_detector.score_all(snapshots)

    if cfg.detection.community_drift.enabled:
        drift_detector = build_community_drift_detector(cfg.detection.community_drift)
        signals[drift_detector.name] = drift_detector.score_all(snapshots)

    comparison = compare(
        dataset.events,
        windows,
        dataset.ground_truth,
        signals,
        baselines=cfg.evaluation.log_vs_graph.baselines,
        working_hours=cfg.graph.working_hours,
        k_values=cfg.evaluation.k_values,
    )

    run_dir = _save_run(cfg, dataset, comparison, signals, run_name) if save else None
    return RunResult(dataset=dataset, comparison=comparison, signals=signals, run_dir=run_dir)


def _save_run(
    cfg: GraphGuardConfig,
    dataset: Dataset,
    comparison: ComparisonResult,
    signals: dict[str, list[Signal]],
    run_name: str | None,
) -> Path:
    """Persist config, dataset summary, metrics and rankings into a run directory."""
    name = run_name or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(cfg.run.output_dir) / name
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg.save(run_dir / "config.yaml")
    (run_dir / "dataset.json").write_text(
        json.dumps(dataset.summary(), indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(comparison.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "ranking.json").write_text(
        json.dumps(_rankings(signals, dataset.ground_truth), indent=2) + "\n", encoding="utf-8"
    )
    return run_dir


def _rankings(
    signals: dict[str, list[Signal]],
    truth: GroundTruth,
    top_n: int = 25,
) -> dict[str, list[dict[str, object]]]:
    """Top-ranked (node, window) rows per method, with ground truth attached.

    This is the table behind the report's ranked-alert figures, and it keeps the explanation text
    for the top rows so a figure can quote a real alert verbatim.
    """
    explanation_by_key = {
        (name, s.node, s.window.index): s for name, sigs in signals.items() for s in sigs
    }

    tables: dict[str, list[dict[str, object]]] = {}
    for name, method_signals in signals.items():
        observations = label_signals(method_signals, truth)
        observations.sort(key=lambda o: o.score, reverse=True)
        rows: list[dict[str, object]] = []
        for rank, obs in enumerate(observations[:top_n], start=1):
            signal = explanation_by_key.get((name, (obs.node_type, obs.node_id), obs.window_index))
            rows.append(
                {
                    "rank": rank,
                    "node": f"{obs.node_type}:{obs.node_id}",
                    "window": obs.window_index,
                    "score": obs.score,
                    "malicious": obs.is_malicious,
                    "scenario": obs.scenario,
                    "explanation": signal.explain() if signal and rank <= 5 else None,
                }
            )
        tables[name] = rows
    return tables
