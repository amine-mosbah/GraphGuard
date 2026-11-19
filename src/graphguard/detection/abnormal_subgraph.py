"""Abnormal-subgraph detection — a group acting in suspicious unison (docs/DESIGN.md §3.2).

Role
----
Detects the attack mode per-node scoring structurally cannot: many accounts, each individually
unremarkable, that are **near-copies of one another** — one actor puppeting a ring of fake
identities. Distinct from community drift (a group *changing over time*); this is a group in
suspicious *coordination within one window*.

Inputs / outputs
----------------
Input: a :class:`~graphguard.graph.builder.GraphSnapshot` (the documented interface — no raw
events).
Output: explained :class:`~graphguard.detection.base.Signal` objects; ring members carry the
cluster as ``evidence`` and a cluster description in ``context``.

Method
------
FraudSquad-style similarity clustering (RELATED_WORK Family 4: temporal synchronicity +
behavioural similarity), kept classical and inspectable. Pairwise similarity is the **geometric
mean of three cosine similarities**, each answering a different question:

1. **Feature similarity** — same volume, outcome mix, session rhythm, token profile
   (z-scored engineered features).
2. **Target similarity** — same services in the same proportions (row-normalised edge-weight
   distribution per relation, minus relations listed in ``ignore_relations``: which *specific
   IP* a member uses is infrastructure identity, not behaviour — farms rotate proxies precisely
   so per-member addresses differ, and counting that against similarity rewards the evasion).
3. **Timing synchronicity** — active in the same 15-minute bins of the window
   (the snapshot's activity profiles). This is the discriminator aggregates cannot provide:
   two batch jobs both "run at night", but only a driven ring fires in the *same minutes*.

A geometric mean is used because unison requires all three at once — a high score on two axes
cannot compensate a low third, which is exactly how legitimate similarity (same persona, same
office hours, but uncorrelated minutes) is kept below the bar.

Clusters are connected components over pairs above ``similarity_threshold``, kept when at least
``min_cluster_size`` members; every member gets the cluster's cohesion-based score, because the
*cluster* is the finding. The size floor exists because near-identical pairs and triples occur
naturally (one team sharing a template; a user's two service keys), while ten do not.

Honest limitation
-----------------
An adversary who adds enough per-member noise (volume jitter, distinct models, staggered
schedules) dilutes cosine similarity below any fixed threshold. That is not a corner case — it is
the adaptive-adversary version of this scenario, it costs the attacker coordination efficiency,
and it belongs in the report's limitations section with the ``sync_jitter_s`` sweep as evidence.
"""

from __future__ import annotations

import numpy as np

from graphguard.config import AbnormalSubgraphConfig
from graphguard.detection.base import Detector, Signal
from graphguard.graph.builder import GraphSnapshot, NodeKey


class SimilarityClusterDetector(Detector):
    """Unison detection via near-duplicate behaviour clustering."""

    name = "abnormal_subgraph:similarity"

    def __init__(self, cfg: AbnormalSubgraphConfig) -> None:
        """Configure thresholds from validated config."""
        super().__init__(score_node_types=cfg.score_node_types)
        self.cfg = cfg
        #: Clusters found for the snapshot scored last, keyed by node — read by
        #: :meth:`score` to attach ring context to each member's signal.
        self._clusters: dict[NodeKey, tuple[NodeKey, ...]] = {}
        self._cohesion: dict[NodeKey, float] = {}

    # ------------------------------------------------------------------ Detector API

    def _raw_scores(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        ids = snapshot.node_ids[node_type]
        similarity = self._similarity_matrix(snapshot, node_type)

        adjacency = similarity >= self.cfg.similarity_threshold
        np.fill_diagonal(adjacency, False)

        # Peel to the k-core *before* taking components. Thresholding pairs alone is not
        # enough: legitimate keys do form the occasional similar pair by chance, and plain
        # connected components would chain those accidents into one sprawling blob. Requiring
        # every surviving member to stay similar to at least `min_cluster_size - 1` others
        # encodes what actually distinguishes a ring — mutual, group-wide resemblance. In
        # practice ring members hold 15+ such partners while no innocent key exceeds 2.
        core = _k_core(adjacency, self.cfg.min_cluster_size - 1)
        components = _connected_components(core)

        scores = np.zeros(len(ids), dtype=np.float64)
        for component in components:
            if len(component) < self.cfg.min_cluster_size:
                continue
            rows = np.array(sorted(component))
            pair_sims = similarity[np.ix_(rows, rows)]
            upper = pair_sims[np.triu_indices(len(rows), k=1)]
            cohesion = float(upper.mean())

            # Rescale cohesion from [threshold, 1] to (0, 1] so scores reflect *how far past*
            # the unison bar the cluster is, and grow modestly with size: a 20-member clone
            # set is stronger evidence than a 5-member one at equal cohesion.
            margin = (cohesion - self.cfg.similarity_threshold) / max(
                1.0 - self.cfg.similarity_threshold, 1e-9
            )
            size_factor = np.log1p(len(rows)) / np.log1p(len(ids)) if len(ids) > 1 else 1.0
            score = max(margin, 0.0) * (0.5 + 0.5 * size_factor)

            member_keys = tuple((node_type, ids[int(r)]) for r in rows)
            for row in rows:
                scores[int(row)] = score
                node = (node_type, ids[int(row)])
                self._clusters[node] = member_keys
                self._cohesion[node] = cohesion

        return scores

    def score(self, snapshot: GraphSnapshot) -> list[Signal]:
        """Score a snapshot, attaching cluster membership to each ring member's signal."""
        self._clusters = {}
        self._cohesion = {}
        signals = super().score(snapshot)

        enriched: list[Signal] = []
        for signal in signals:
            cluster = self._clusters.get(signal.node)
            if cluster is None:
                enriched.append(signal)
                continue
            others = tuple(node for node in cluster if node != signal.node)
            context = (
                f"behaves in near-unison with {len(others)} other "
                f"{signal.node_type} entities (mean pairwise similarity "
                f"{self._cohesion[signal.node]:.4f}): "
                + ", ".join(node_id for _, node_id in others[:8])
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
                    evidence=cluster,
                    context=context,
                )
            )
        return enriched

    # ------------------------------------------------------------------ similarity

    def _similarity_matrix(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        """Geometric mean of the three per-axis cosine similarity matrices.

        Negative cosines (possible on the z-scored feature axis) are clamped to zero before the
        geometric mean: "anticorrelated" and "unrelated" are equally far from unison.
        """
        feature_sim = _cosine_matrix(self._feature_block(snapshot, node_type))
        target_sim = _cosine_matrix(self._target_block(snapshot, node_type))
        timing_sim = _cosine_matrix(self._timing_block(snapshot, node_type))

        stacked = np.stack([feature_sim, target_sim, timing_sim])
        return np.power(np.clip(stacked, 0.0, None).prod(axis=0), 1.0 / 3.0)

    @staticmethod
    def _timing_block(snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        """Activity-profile *residuals*: each node's 15-minute rhythm minus the population's.

        Raw profiles will not do: every office-hours key follows the same diurnal curve, so two
        heavy legitimate keys correlate at ~0.9 on raw profiles and synchronicity would mean
        nothing. What is suspicious is being active in the same bins *beyond what the shared
        rhythm explains* — so profiles are row-normalised (volume already lives on the feature
        axis) and the population's typical rhythm is subtracted. Residuals of independent keys
        are uncorrelated noise; residuals of a driven ring share the same burst spikes.

        The baseline rhythm is the **median**, not the mean. A ring large enough to matter is
        also large enough to drag the mean toward its own burst pattern, partially subtracting
        the very signal being measured — the detector would get *worse* as the attack got
        bigger. The median is unmoved until the ring is over half the population.
        """
        profiles = snapshot.activity_profiles[node_type]
        row_sums = profiles.sum(axis=1, keepdims=True)
        rhythm = np.divide(profiles, row_sums, out=np.zeros_like(profiles), where=row_sums > 0)
        return rhythm - np.median(rhythm, axis=0)

    @staticmethod
    def _feature_block(snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        """Engineered features, z-scored per window so no dimension dominates the angle."""
        features = snapshot.raw_features[node_type]
        centred = features - features.mean(axis=0)
        spread = features.std(axis=0)
        return centred / np.where(spread > 0, spread, 1.0)

    def _target_block(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        """Row-normalised edge-weight distribution over targets, per non-ignored relation.

        Captures *what* the node connects to and in what proportions; row-normalisation keeps
        absolute volume out (it already lives on the feature axis). Relations named in
        ``ignore_relations`` are skipped — see the module docstring for why ``used_from`` is.
        """
        ids = snapshot.node_ids[node_type]
        ignored = set(self.cfg.ignore_relations)

        blocks: list[np.ndarray] = []
        for relation in snapshot.data.edge_types:
            source_type, relation_name, target_type = relation
            if source_type != node_type or relation_name in ignored:
                continue
            block = np.zeros((len(ids), len(snapshot.node_ids[target_type])), dtype=np.float64)
            edge_index = snapshot.data[relation].edge_index
            weights = snapshot.data[relation].edge_weight
            for (source, target), weight in zip(
                edge_index.t().tolist(), weights.tolist(), strict=True
            ):
                block[source, target] += float(weight)
            row_sums = block.sum(axis=1, keepdims=True)
            blocks.append(np.divide(block, row_sums, out=np.zeros_like(block), where=row_sums > 0))

        if not blocks:
            # No usable relations: this axis is uninformative; a neutral all-ones block keeps
            # the geometric mean defined without favouring or penalising anyone.
            return np.ones((len(ids), 1), dtype=np.float64)
        return np.hstack(blocks)


def build_abnormal_subgraph_detector(cfg: AbnormalSubgraphConfig) -> Detector:
    """Construct the configured abnormal-subgraph detector."""
    return SimilarityClusterDetector(cfg)


# --------------------------------------------------------------------------- helpers


def _cosine_matrix(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity, safe for zero rows."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)
    return unit @ unit.T


def _k_core(adjacency: np.ndarray, k: int) -> np.ndarray:
    """Return ``adjacency`` restricted to its k-core: every node keeps degree >= ``k``.

    Iterative peeling — removing a low-degree node can drop its neighbours below the bar, so
    the process repeats until stable.
    """
    if k <= 0:
        return adjacency
    core = adjacency.copy()
    while True:
        degrees = core.sum(axis=1)
        doomed = (degrees > 0) & (degrees < k)
        if not doomed.any():
            return core
        core[doomed, :] = False
        core[:, doomed] = False


def _connected_components(adjacency: np.ndarray) -> list[set[int]]:
    """Find connected components of a boolean adjacency matrix, iteratively."""
    n = adjacency.shape[0]
    seen = np.zeros(n, dtype=bool)
    components: list[set[int]] = []

    for start in range(n):
        if seen[start]:
            continue
        component = {start}
        seen[start] = True
        frontier = [start]
        while frontier:
            node = frontier.pop()
            neighbours = np.flatnonzero(adjacency[node])
            for neighbour in neighbours:
                if not seen[neighbour]:
                    seen[neighbour] = True
                    component.add(int(neighbour))
                    frontier.append(int(neighbour))
        if len(component) > 1:
            components.append(component)
    return components
