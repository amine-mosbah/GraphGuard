"""Community-drift detection — a group whose behaviour is moving together (docs/DESIGN.md §3.2).

Role
----
Detects the third attack mode: a cohort of entities whose collective behaviour migrates over
time. Each of the three detectors asks a structurally different question —

- **node-outlier**: is this entity unlike its peers, or unlike its own past?
- **abnormal-subgraph**: are these entities suspiciously alike *right now*?
- **community drift** (here): are these entities *moving the same way over time*?

Inputs / outputs
----------------
Input: a **chronological sequence** of :class:`~graphguard.graph.builder.GraphSnapshot`.
Output: explained :class:`~graphguard.detection.base.Signal` objects.

Stateful across snapshots — it remembers where each entity used to sit — so :meth:`score_all` is
the intended entry point. Scoring a lone snapshot is valid but yields nothing, because there is no
past to have moved away from.

Method
------
1. **Per-entity displacement.** Each entity's behaviour is summarised as two distributions: its
   mix of target services and its 15-minute activity profile. Its *displacement* is the
   difference between the current values and its own baseline, averaged over the previous
   ``baseline_windows`` snapshots. Entities that barely moved (below ``min_displacement``) are
   dropped before anything else happens.
2. **Group by direction of travel.** Displacements are compared by cosine similarity and
   clustered with a k-core, keeping groups of at least ``min_community_size`` entities that are
   all moving *the same way*.
3. **Score** each member by the group's coherence (how tightly aligned the movement is) times its
   magnitude (how far it went).

Why direction of travel, and not community tracking
---------------------------------------------------
The textbook design — cluster entities into communities, then track those communities across
windows — was implemented first and measured. It fails here twice over, and both failures are
instructive enough to record:

*Communities split exactly when it matters.* A drifting cohort separates from its parent community
as it migrates: on this dataset the ten drifting keys sit inside two larger communities early on
and form their own 13-member community by the end. Membership overlap between those is far below
any sensible continuation threshold, so a community-tracking detector loses the group precisely
when it has drifted furthest.

*Community assignment is unstable for everyone else.* Benign entities hop between clusters as the
partition shifts window to window, and that hopping produces large spurious "drift". Measured, it
swamped the real signal completely: drifting cohort mean 0.16 versus benign maximum 0.82.

Displacement direction has neither problem, because it never asks which group an entity belongs
to — only which way it is going. Measured on the same data, cohort members' displacements align at
a median cosine of **+0.52 to +0.60** across every window of the drift, while benign entities'
displacements are uncorrelated at a median of **+0.00**. Ordinary behavioural churn is real but
*directionless*; a group being pushed by one cause moves as one.

This is a deliberate reinterpretation of the classical formulation, not an oversight: it operates
on the same phenomenon Ranshous et al. catalogue (splitting, merging and flickering communities)
while sidestepping the identity-tracking problem that makes those cases hard.

Why displacement is measured on bounded distributions
-----------------------------------------------------
Drift is a comparison *across* windows, so every quantity involved must mean the same thing in
every window. Per-window standardisation would defeat this outright: it re-centres each snapshot
on itself, so a group that moves steadily keeps landing at the same relative coordinates and its
drift silently reads as zero. Distributions over services and over time-of-day are naturally
normalised, comparable window to window, and convert directly into a sentence an analyst can act
on. Raw magnitudes such as event counts are excluded on purpose: a team that simply gets busier
has not drifted, it has scaled.

Honest limitation
-----------------
Drift and legitimate change are the same measurement. A team genuinely migrating to a new model, a
product launch, or a daylight-saving shift all produce coherent group movement, and this detector
will and should flag them. It surfaces *"this group is not where it was, and it moved together"*
with the evidence; deciding whether that is an incident is the analyst's call, which is exactly
the division of labour docs/DESIGN.md §14 prescribes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from graphguard.config import CommunityDriftConfig
from graphguard.detection.abnormal_subgraph import (
    _connected_components,
    _cosine_matrix,
    _k_core,
)
from graphguard.detection.base import Detector, Signal
from graphguard.graph.builder import GraphSnapshot, NodeKey


@dataclass
class _Behaviour:
    """An entity's behaviour in one window, as two comparable-across-windows distributions."""

    target_mix: dict[str, float]
    activity: np.ndarray


@dataclass(frozen=True, slots=True)
class _DriftDetail:
    """What was found about one entity's co-drifting group, for its explanation."""

    coherence: float
    target_move: float
    activity_move: float
    members: tuple[NodeKey, ...]


@dataclass
class _Track:
    """An entity's recent behaviour history."""

    history: list[_Behaviour] = field(default_factory=list)

    def baseline(self) -> _Behaviour:
        """Average of the remembered windows."""
        return _Behaviour(
            target_mix=_mean_distribution([b.target_mix for b in self.history]),
            activity=np.mean(np.stack([b.activity for b in self.history]), axis=0),
        )


class CommunityDriftDetector(Detector):
    """Score entities by how coherently their group's behaviour is moving."""

    name = "community_drift:codrift"

    def __init__(self, cfg: CommunityDriftConfig) -> None:
        """Configure displacement, coherence and baseline depth from validated config."""
        super().__init__(score_node_types=cfg.score_node_types)
        self.cfg = cfg
        self._tracks: dict[NodeKey, _Track] = {}
        #: Per-node drift detail for the window being scored, consumed by :meth:`score`.
        self._detail: dict[NodeKey, _DriftDetail] = {}

    # ------------------------------------------------------------------ Detector API

    def _raw_scores(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        ids = snapshot.node_ids[node_type]
        scores = np.zeros(len(ids), dtype=np.float64)

        current = _behaviours(snapshot, node_type)

        # 1. Displacement of every entity that has enough history to have a baseline.
        candidate_rows: list[int] = []
        raw_displacements: list[np.ndarray] = []
        target_widths: list[int] = []

        for row, entity_id in enumerate(ids):
            track = self._tracks.get((node_type, entity_id))
            if track is None or len(track.history) < self.cfg.baseline_windows:
                continue
            baseline = track.baseline()
            behaviour = current[row]

            target_delta = _distribution_delta(behaviour.target_mix, baseline.target_mix)
            activity_delta = behaviour.activity - baseline.activity
            candidate_rows.append(row)
            raw_displacements.append(np.concatenate([target_delta, activity_delta]))
            target_widths.append(len(target_delta))

        # 2. Remove the movement the whole population shares.
        #
        # Everyone's activity profile shifts together on a Saturday, and a platform-wide model
        # deprecation moves every team's target mix at once. Those are coherent by construction
        # and uninteresting: measured, the weekend alone produced ten spurious co-drift
        # detections in one window. Subtracting the element-wise *median* displacement leaves
        # only movement specific to a subset of entities, which is the finding. The median, not
        # the mean, so a large drifting cohort cannot drag the reference toward itself and
        # subtract away its own signal.
        rows: list[int] = []
        displacements: list[np.ndarray] = []
        detail_parts: dict[int, tuple[float, float]] = {}

        if candidate_rows:
            stacked = np.stack(raw_displacements)
            common = np.median(stacked, axis=0)
            width = target_widths[0]

            for i, row in enumerate(candidate_rows):
                residual = stacked[i] - common
                target_move = 0.5 * float(np.abs(residual[:width]).sum())
                activity_move = 0.5 * float(np.abs(residual[width:]).sum())
                if 0.5 * (target_move + activity_move) < self.cfg.min_displacement:
                    continue
                rows.append(row)
                displacements.append(residual)
                detail_parts[row] = (target_move, activity_move)

        # 3. Group the movers by direction of travel.
        if len(rows) >= self.cfg.min_community_size:
            matrix = np.stack(displacements)
            similarity = _cosine_matrix(matrix)
            adjacency = similarity >= self.cfg.coherence_threshold
            np.fill_diagonal(adjacency, False)
            core = _k_core(adjacency, self.cfg.min_community_size - 1)

            for component in _connected_components(core):
                if len(component) < self.cfg.min_community_size:
                    continue
                local = sorted(component)
                pairs = similarity[np.ix_(local, local)][np.triu_indices(len(local), k=1)]
                coherence = float(pairs.mean())
                magnitudes = [0.5 * sum(detail_parts[rows[i]]) for i in local]
                group_magnitude = float(np.mean(magnitudes))
                score = coherence * group_magnitude

                members = tuple((node_type, ids[rows[i]]) for i in local)
                for i in local:
                    row = rows[i]
                    target_move, activity_move = detail_parts[row]
                    scores[row] = score
                    self._detail[(node_type, ids[row])] = _DriftDetail(
                        coherence=coherence,
                        target_move=target_move,
                        activity_move=activity_move,
                        members=members,
                    )

        # 4. Remember this window, after scoring, so an entity never has itself in its baseline.
        for row, entity_id in enumerate(ids):
            track = self._tracks.setdefault((node_type, entity_id), _Track())
            track.history.append(current[row])
            del track.history[: -self.cfg.baseline_windows]

        return scores

    def score(self, snapshot: GraphSnapshot) -> list[Signal]:
        """Score a snapshot, attaching each co-drifting group's detail to its members' signals."""
        self._detail = {}
        signals = super().score(snapshot)

        enriched: list[Signal] = []
        for signal in signals:
            detail = self._detail.get(signal.node)
            if detail is None:
                enriched.append(signal)
                continue
            others = [node_id for _, node_id in detail.members if node_id != signal.node_id]
            context = (
                f"is drifting together with {len(others)} other {signal.node_type} entities "
                f"(direction agreement {detail.coherence:.2f}): the group moved "
                f"{detail.target_move:.0%} of its traffic to different services and "
                f"{detail.activity_move:.0%} of its activity to different times of day, "
                f"relative to its own recent baseline. Group: "
                + ", ".join(others[:8])
                + (", ..." if len(others) > 8 else "")
            )
            enriched.append(
                Signal(
                    detector=signal.detector,
                    node=signal.node,
                    window=signal.window,
                    raw_score=signal.raw_score,
                    normalised_score=signal.normalised_score,
                    reasons=signal.reasons,
                    evidence=detail.members,
                    context=context,
                )
            )
        return enriched

    def reset(self) -> None:
        """Forget all history, so a rerun is independent of the previous one."""
        self._tracks = {}
        self._detail = {}

    def score_all(self, snapshots: list[GraphSnapshot]) -> list[Signal]:
        """Score a chronological sequence, carrying behaviour history across it."""
        self.reset()
        return super().score_all(snapshots)


def build_community_drift_detector(cfg: CommunityDriftConfig) -> Detector:
    """Construct the configured community-drift detector."""
    return CommunityDriftDetector(cfg)


# --------------------------------------------------------------------------- helpers


def _behaviours(snapshot: GraphSnapshot, node_type: str) -> list[_Behaviour]:
    """Summarise every entity of ``node_type`` as its target mix and activity profile."""
    ids = snapshot.node_ids[node_type]
    targets = _target_distribution_block(snapshot, node_type)
    activity = _normalised_activity(snapshot, node_type)
    models = snapshot.node_ids.get("model", ())

    return [
        _Behaviour(
            target_mix={name: float(targets[row, i]) for i, name in enumerate(models)},
            activity=activity[row],
        )
        for row in range(len(ids))
    ]


def _target_distribution_block(snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
    """Row-normalised distribution over ``model`` targets, aligned with ``node_ids[node_type]``.

    Restricted to the ``model`` relation on purpose: the services an entity consumes are what
    define its working behaviour, whereas source addresses are infrastructure and would register
    a team moving office as a behavioural drift.
    """
    ids = snapshot.node_ids[node_type]
    models = snapshot.node_ids.get("model", ())
    block = np.zeros((len(ids), len(models)), dtype=np.float64)
    if not models:
        return block

    for relation in snapshot.data.edge_types:
        source_type, _, target_type = relation
        if source_type != node_type or target_type != "model":
            continue
        edge_index = snapshot.data[relation].edge_index
        weights = snapshot.data[relation].edge_weight
        for (source, target), weight in zip(edge_index.t().tolist(), weights.tolist(), strict=True):
            block[source, target] += float(weight)

    row_sums = block.sum(axis=1, keepdims=True)
    return np.divide(block, row_sums, out=np.zeros_like(block), where=row_sums > 0)


def _normalised_activity(snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
    """15-minute activity profiles as per-node distributions summing to one."""
    profiles = snapshot.activity_profiles[node_type]
    row_sums = profiles.sum(axis=1, keepdims=True)
    return np.divide(profiles, row_sums, out=np.zeros_like(profiles), where=row_sums > 0)


def _mean_distribution(distributions: list[dict[str, float]]) -> dict[str, float]:
    """Average a list of sparse distributions over the union of their keys."""
    if not distributions:
        return {}
    keys = {key for distribution in distributions for key in distribution}
    return {
        key: sum(distribution.get(key, 0.0) for distribution in distributions) / len(distributions)
        for key in keys
    }


def _distribution_delta(current: dict[str, float], baseline: dict[str, float]) -> np.ndarray:
    """Signed difference between two distributions, over their union of keys in sorted order.

    Sorted keys matter: the vector must line up dimension-for-dimension across entities, or the
    cosine between two displacements would compare unrelated services.
    """
    keys = sorted(set(current) | set(baseline))
    return np.array([current.get(key, 0.0) - baseline.get(key, 0.0) for key in keys])
