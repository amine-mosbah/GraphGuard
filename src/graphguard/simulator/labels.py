"""Ground truth for evaluation.

Role
----
Attack scenarios emit *event-level* labels (``TraceEvent.label``), but detectors score **nodes
within a time window**. This module carries the bridge: a scenario declares which entity was
compromised over which interval, and :class:`GroundTruth` answers "was this node malicious in this
snapshot?" — the binary vector every metric in :mod:`graphguard.eval` needs.

Inputs / outputs
----------------
Input: :class:`ScenarioLabel` records produced by ``simulator.scenarios.*``.
Output: :class:`GroundTruth`, consumed by the evaluation harness.

Why an interval and not a flag
------------------------------
A compromised key is not malicious for the whole observation period — it is malicious *from the
moment of compromise onward*. Labelling the key as malicious in windows before the onset would
punish a detector for correctly staying quiet, and labelling it benign after would reward missing
the attack. The interval is the only honest representation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

#: A node's graph identity: ``(type, id)``.
NodeKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ScenarioLabel:
    """One entity's malicious interval under one scenario.

    The interval is half-open, ``[start, end)``.
    """

    scenario: str
    entity_type: str
    entity_id: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Reject inverted intervals, which would silently label nothing."""
        if self.end <= self.start:
            raise ValueError(
                f"{self.scenario}: label interval must be non-empty, got {self.start} -> {self.end}"
            )

    @property
    def node_key(self) -> NodeKey:
        """Graph identity of the labelled entity."""
        return (self.entity_type, self.entity_id)

    def covers(self, start: datetime, end: datetime) -> bool:
        """Whether this label overlaps the window ``[start, end)``.

        Overlap, not containment: a snapshot in which the attack was active for even part of the
        window contains attack edges, so the node is malicious in that snapshot.
        """
        return self.start < end and start < self.end


@dataclass(frozen=True)
class GroundTruth:
    """All scenario labels for one generated dataset."""

    labels: tuple[ScenarioLabel, ...] = ()

    @classmethod
    def from_labels(cls, labels: Iterable[ScenarioLabel]) -> GroundTruth:
        """Build ground truth from any iterable of labels."""
        return cls(labels=tuple(labels))

    def merge(self, other: GroundTruth) -> GroundTruth:
        """Combine ground truth from several scenarios."""
        return GroundTruth(labels=self.labels + other.labels)

    @property
    def scenarios(self) -> frozenset[str]:
        """Names of every scenario represented here."""
        return frozenset(label.scenario for label in self.labels)

    def malicious_nodes(self, start: datetime, end: datetime) -> set[NodeKey]:
        """Nodes that are malicious in the window ``[start, end)``."""
        return {label.node_key for label in self.labels if label.covers(start, end)}

    def scenario_of(self, node: NodeKey, start: datetime, end: datetime) -> str | None:
        """Which scenario made ``node`` malicious in this window, if any."""
        for label in self.labels:
            if label.node_key == node and label.covers(start, end):
                return label.scenario
        return None

    def binary_vector(self, nodes: Sequence[NodeKey], start: datetime, end: datetime) -> np.ndarray:
        """Return a 0/1 array aligned to ``nodes`` for the window ``[start, end)``.

        This is the ``y_true`` every evaluation metric consumes; the ordering must match the
        detector's score array exactly, so callers pass the detector's own node ordering.
        """
        malicious = self.malicious_nodes(start, end)
        return np.fromiter((node in malicious for node in nodes), dtype=np.int8, count=len(nodes))

    def summary(self) -> dict[str, int]:
        """Count labelled entities per scenario, for run logs and the report."""
        counts: dict[str, int] = {}
        for label in self.labels:
            counts[label.scenario] = counts.get(label.scenario, 0) + 1
        return counts
