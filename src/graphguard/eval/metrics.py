"""Evaluation metrics — BOND-style reporting for node-level detection.

Role
----
Scores any detector's output against ground truth. The unit of evaluation is the
**(node, window) pair**: a detector is right when it ranks a compromised key highly *in a window
where the compromise was active*, not merely somewhere, sometime.

Inputs / outputs
----------------
Input: :class:`~graphguard.detection.base.Signal` lists and a
:class:`~graphguard.simulator.labels.GroundTruth`.
Output: a :class:`EvaluationResult` (JSON-serialisable), consumed by the CLI, saved runs and the
report's result tables.

Methodology notes
-----------------
Following BOND (NeurIPS 2022): ROC-AUC and average precision on the continuous scores, plus
precision/recall@k — because an analyst triages a *ranked queue*, the quality of the top of the
ranking matters more than the tail. Windows before the attack onset contain no positive labels by
construction; they are still included, because false positives raised there are real costs an
analyst would pay.

Scores are pooled across windows using the **raw** score, not the per-window normalised one:
min-max normalisation forces some node to 1.0 in every window, including quiet ones, which would
manufacture false positives at the top of the pooled ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from graphguard.detection.base import Signal
from graphguard.simulator.labels import GroundTruth


@dataclass(frozen=True)
class LabelledScore:
    """One (node, window) observation: the detector's score and the ground truth."""

    node_type: str
    node_id: str
    window_index: int
    score: float
    is_malicious: bool
    scenario: str | None


@dataclass(frozen=True)
class EvaluationResult:
    """Metrics for one detector run over one dataset."""

    detector: str
    n_observations: int
    n_positives: int
    roc_auc: float | None
    average_precision: float | None
    #: Attack scenario this result is scoped to; ``None`` means pooled over every scenario.
    scenario: str | None = None
    precision_at_k: dict[int, float] = field(default_factory=dict)
    recall_at_k: dict[int, float] = field(default_factory=dict)
    #: Highest-ranked truly-malicious observation (1-based); None if there are no positives.
    first_hit_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for saved runs."""
        return {
            "detector": self.detector,
            "scenario": self.scenario,
            "n_observations": self.n_observations,
            "n_positives": self.n_positives,
            "roc_auc": self.roc_auc,
            "average_precision": self.average_precision,
            "precision_at_k": {str(k): v for k, v in self.precision_at_k.items()},
            "recall_at_k": {str(k): v for k, v in self.recall_at_k.items()},
            "first_hit_rank": self.first_hit_rank,
        }


def label_signals(signals: list[Signal], truth: GroundTruth) -> list[LabelledScore]:
    """Join detector signals with ground truth on (node, window)."""
    observations: list[LabelledScore] = []
    for signal in signals:
        scenario = truth.scenario_of(signal.node, signal.window.start, signal.window.end)
        observations.append(
            LabelledScore(
                node_type=signal.node_type,
                node_id=signal.node_id,
                window_index=signal.window.index,
                score=signal.raw_score,
                is_malicious=scenario is not None,
                scenario=scenario,
            )
        )
    return observations


def evaluate(
    detector_name: str,
    signals: list[Signal],
    truth: GroundTruth,
    k_values: list[int],
    *,
    scenario: str | None = None,
) -> EvaluationResult:
    """Compute BOND-style metrics over all (node, window) observations.

    Parameters
    ----------
    detector_name:
        Display name recorded on the result.
    signals:
        The detector's output.
    truth:
        Ground-truth intervals for the dataset.
    k_values:
        Cut-offs for precision@k / recall@k.
    scenario:
        When given, measure this detector against **one** attack scenario: only that scenario's
        entities count as positives, and observations belonging to *other* scenarios are dropped
        from the grid entirely.

        Dropping them is the point. A node compromised by a different attack is neither a
        positive (it is not the scenario under test) nor an honest negative (it is not benign),
        so scoring it either way misstates the result. Keeping them as negatives would punish a
        detector for correctly flagging a real attack.

    Why per-scenario evaluation exists
    ----------------------------------
    Each detector targets a different attack *mode* (docs/DESIGN.md §3.2). Pooling every scenario
    into one number scores each detector against attacks it was never meant to catch and yields a
    figure that describes none of them: on a dataset containing both a stolen key and a
    coordinated ring, the node-outlier detector's pooled ROC-AUC fell to 0.19 purely because it
    (correctly) does not rank ring members highly. The detector-vs-scenario matrix is the honest
    reporting unit.

    """
    observations = label_signals(signals, truth)
    if scenario is not None:
        observations = [o for o in observations if o.scenario in (None, scenario)]

    y_true = np.array(
        [o.scenario == scenario if scenario is not None else o.is_malicious for o in observations],
        dtype=np.int8,
    )
    y_score = np.array([o.score for o in observations], dtype=np.float64)

    n_positives = int(y_true.sum())
    if n_positives == 0 or n_positives == len(y_true):
        # AUC is undefined with a single class; report None rather than a fabricated number.
        roc_auc, avg_precision = None, None
    else:
        roc_auc = float(roc_auc_score(y_true, y_score))
        avg_precision = float(average_precision_score(y_true, y_score))

    order = np.argsort(-y_score)
    ranked = y_true[order]

    precision_at_k: dict[int, float] = {}
    recall_at_k: dict[int, float] = {}
    for k in k_values:
        k_eff = min(k, len(ranked))
        if k_eff == 0:
            continue
        hits = int(ranked[:k_eff].sum())
        precision_at_k[k] = hits / k_eff
        recall_at_k[k] = hits / n_positives if n_positives else 0.0

    hit_positions = np.flatnonzero(ranked)
    first_hit_rank = int(hit_positions[0]) + 1 if hit_positions.size else None

    return EvaluationResult(
        detector=detector_name,
        scenario=scenario,
        n_observations=len(observations),
        n_positives=n_positives,
        roc_auc=roc_auc,
        average_precision=avg_precision,
        precision_at_k=precision_at_k,
        recall_at_k=recall_at_k,
        first_hit_rank=first_hit_rank,
    )
