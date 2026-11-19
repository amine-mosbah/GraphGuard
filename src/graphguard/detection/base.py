"""Detector interface — scores are never returned bare.

Role
----
Defines what every GraphGuard detector must produce: a :class:`Signal` carrying a score, the
evidence behind it, and a plain-language reason a security analyst can act on. Explainability is a
mandatory output, not a post-hoc add-on (docs/DESIGN.md §8.3, §15).

Inputs / outputs
----------------
Input: a :class:`~graphguard.graph.builder.GraphSnapshot`.
Output: :class:`Signal` objects, consumed by ``graphguard.correlation`` and
``graphguard.eval``.

On what a score means
---------------------
A score is **relative to the population in one window**. ``normalised_score`` is min-max scaled
within the scored nodes, so the top-ranked node always scores 1.0 — including in a window with no
attack in it at all. It is a ranking aid, not a probability of malice, and GraphGuard never
auto-blocks on it (docs/DESIGN.md §14). A legitimate power user genuinely can top the ranking; the
dashboard exists so a human resolves that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from graphguard.graph.builder import GraphSnapshot, NodeKey
from graphguard.graph.model import FEATURE_DESCRIPTIONS
from graphguard.graph.snapshots import Window


@dataclass(frozen=True, slots=True)
class Reason:
    """One contributing factor behind a score, in units a human reads.

    ``deviation`` is a robust z-score: how many units of peer spread this node's value sits from
    the peer median. The spread is the median absolute deviation where that is non-zero, falling
    back to the standard deviation otherwise (see :func:`_robust_scale`). Robust statistics are
    used deliberately — with a handful of compromised keys in the population, a mean-and-standard-
    deviation baseline is dragged toward the very outliers it is supposed to measure.
    """

    feature: str
    value: float
    peer_median: float
    deviation: float

    @property
    def direction(self) -> str:
        """Whether the node is above or below its peers on this feature."""
        return "higher" if self.deviation >= 0 else "lower"

    def text(self) -> str:
        """Render this factor as a sentence."""
        description = FEATURE_DESCRIPTIONS.get(self.feature, self.feature)
        return (
            f"{description} was {self.value:,.2f} versus a peer median of {self.peer_median:,.2f} "
            f"({abs(self.deviation):.1f}x the peer spread, {self.direction})"
        )


@dataclass(frozen=True, slots=True)
class Signal:
    """A scored, explained observation about one node in one window."""

    detector: str
    node: NodeKey
    window: Window
    raw_score: float
    normalised_score: float
    reasons: tuple[Reason, ...]
    #: Neighbouring nodes that make up this node's context, for the alert subgraph.
    evidence: tuple[NodeKey, ...] = ()
    #: Detector-specific finding that per-feature reasons cannot express — e.g. the
    #: abnormal-subgraph detector names the cluster here. Included verbatim in explain().
    context: str | None = None

    @property
    def node_type(self) -> str:
        """Type component of the node identity."""
        return self.node[0]

    @property
    def node_id(self) -> str:
        """Id component of the node identity."""
        return self.node[1]

    def explain(self) -> str:
        """Render the plain-language explanation of why this node was flagged.

        This is the string the analyst reads in the alert (docs/DESIGN.md §3.3). It names the
        entity, the window, and the top contributing factors in human units.
        """
        header = (
            f"{self.node_type} '{self.node_id}' scored {self.normalised_score:.2f} "
            f"in window {self.window.start.isoformat()} .. {self.window.end.isoformat()}"
        )
        parts: list[str] = []
        if self.context:
            parts.append(f"{header}: {self.context}")
        elif not self.reasons:
            return f"{header}; no single feature stood out (the combination was unusual)."
        else:
            parts.append(f"{header}, because:")
        if self.reasons:
            parts.append("\n".join(f"  - {reason.text()}" for reason in self.reasons))
        return "\n".join(parts)

    def __str__(self) -> str:
        """Compact one-line rendering for logs."""
        return (
            f"[{self.detector}] {self.node_type}:{self.node_id} "
            f"score={self.normalised_score:.3f} window={self.window.index}"
        )


class Detector(ABC):
    """Base class for every detector in the suite.

    Subclasses implement :meth:`_raw_scores`; this class handles the parts that must be identical
    across detectors — selecting scorable node types, normalising scores, and attaching
    explanations — so that no detector can accidentally return a bare number.
    """

    #: Stable identifier used in signals, alerts and saved runs.
    name: str = "detector"

    def __init__(self, score_node_types: list[str], top_k_reasons: int = 3) -> None:
        """Configure which node types are scored and how many reasons to attach."""
        self.score_node_types = list(score_node_types)
        self.top_k_reasons = top_k_reasons

    # ------------------------------------------------------------------ subclass API

    @abstractmethod
    def _raw_scores(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        """Return one anomaly score per node of ``node_type``, in feature-row order.

        Higher means more anomalous.
        """

    # ------------------------------------------------------------------ public API

    def score(self, snapshot: GraphSnapshot) -> list[Signal]:
        """Score a snapshot and return explained signals, ranked most anomalous first."""
        signals: list[Signal] = []
        for node_type in self.score_node_types:
            ids = snapshot.node_ids.get(node_type)
            if not ids:
                continue
            raw = np.asarray(self._raw_scores(snapshot, node_type), dtype=np.float64)
            if raw.shape != (len(ids),):
                raise ValueError(
                    f"{self.name}: expected {len(ids)} scores for node type {node_type!r}, "
                    f"got shape {raw.shape}"
                )
            normalised = _min_max(raw)
            features = snapshot.raw_features[node_type]
            peer_medians = np.median(features, axis=0)
            peer_scales, usable = _robust_scale(features)

            for row, entity_id in enumerate(ids):
                signals.append(
                    Signal(
                        detector=self.name,
                        node=(node_type, entity_id),
                        window=snapshot.window,
                        raw_score=float(raw[row]),
                        normalised_score=float(normalised[row]),
                        reasons=self._reasons(features[row], peer_medians, peer_scales, usable),
                        evidence=self._evidence(snapshot, node_type, entity_id),
                    )
                )

        signals.sort(key=lambda s: s.raw_score, reverse=True)
        return signals

    def score_all(self, snapshots: list[GraphSnapshot]) -> list[Signal]:
        """Score every snapshot in a sequence."""
        return [signal for snapshot in snapshots for signal in self.score(snapshot)]

    # ------------------------------------------------------------------ explanation

    def _reasons(
        self,
        features: np.ndarray,
        peer_medians: np.ndarray,
        peer_scales: np.ndarray,
        usable: np.ndarray,
    ) -> tuple[Reason, ...]:
        """Name the features on which this node deviates most from its peers.

        These are *contributing factors*, established independently of how the detector computed
        its score. That distinction is deliberate: for a neural detector the score comes from
        reconstruction error and is not decomposable per feature, so presenting these as a
        breakdown of the model's internals would be a false claim. What they honestly answer is
        "what is unusual about this entity?", which is what the analyst actually needs.
        """
        from graphguard.graph.model import FEATURE_NAMES

        deviations = np.where(usable, (features - peer_medians) / peer_scales, 0.0)
        order = np.argsort(-np.abs(deviations))

        reasons: list[Reason] = []
        for index in order[: self.top_k_reasons]:
            deviation = float(deviations[index])
            if not np.isfinite(deviation) or abs(deviation) < 1.0:
                continue  # nothing worth telling a human about
            reasons.append(
                Reason(
                    feature=FEATURE_NAMES[index],
                    value=float(features[index]),
                    peer_median=float(peer_medians[index]),
                    deviation=deviation,
                )
            )
        return tuple(reasons)

    @staticmethod
    def _evidence(snapshot: GraphSnapshot, node_type: str, entity_id: str) -> tuple[NodeKey, ...]:
        """Collect the node's immediate neighbours, for the alert subgraph."""
        neighbours: set[NodeKey] = set()
        index = snapshot.node_ids[node_type].index(entity_id)

        for relation in snapshot.data.edge_types:
            source_type, _, target_type = relation
            edge_index = snapshot.data[relation].edge_index
            if source_type == node_type:
                mask = edge_index[0] == index
                for target in edge_index[1][mask].tolist():
                    neighbours.add((target_type, snapshot.node_ids[target_type][target]))
            if target_type == node_type:
                mask = edge_index[1] == index
                for source in edge_index[0][mask].tolist():
                    neighbours.add((source_type, snapshot.node_ids[source_type][source]))

        return tuple(sorted(neighbours))


def _min_max(values: np.ndarray) -> np.ndarray:
    """Scale scores to ``[0, 1]`` within one window.

    A flat population maps to all-zeros rather than all-ones: if nothing stands out, nothing
    should look maximally suspicious.
    """
    if values.size == 0:
        return values
    low, high = float(values.min()), float(values.max())
    if high - low <= 0:
        return np.zeros_like(values)
    return (values - low) / (high - low)


def _robust_scale(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-feature robust scale, plus a mask of features that have any usable spread.

    Returns ``(scale, usable)``.

    Median absolute deviation is the primary scale because it is not dragged toward the outliers
    it is meant to measure. But MAD is **zero whenever more than half the population shares one
    value**, which is common here: most keys have a refusal ratio of exactly 0.00. Dividing by a
    tiny floor in that case manufactures absurd deviations — an observed 0.01 against a zero MAD
    produced "10,207,553x higher", which then outranked genuinely informative reasons.

    So: fall back to the standard deviation when MAD is zero, and mark the feature unusable when
    it is genuinely constant. A feature with no variation cannot explain anything, and saying
    nothing is better than saying something meaningless.
    """
    medians = np.median(features, axis=0)
    mad = np.median(np.abs(features - medians), axis=0) * 1.4826
    scale = np.where(mad > 0, mad, features.std(axis=0))
    usable = scale > 0
    return np.where(usable, scale, 1.0), usable
