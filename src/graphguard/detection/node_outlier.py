"""Node-outlier scoring — "impostor scoring" (docs/DESIGN.md §3.2).

Role
----
Scores how far each entity deviates from comparable peers and from its own past. This is the
detector that catches a **stolen API key**: the credential starts calling models it never used, at
hours it never worked, from an address it never came from, at volumes it never reached.

Inputs / outputs
----------------
Input: a :class:`~graphguard.graph.builder.GraphSnapshot`.
Output: explained :class:`~graphguard.detection.base.Signal` objects.

Two detectors, on purpose
-------------------------
:class:`EgonetOutlierDetector` is a classical, non-learned baseline (OddBall-style egonet
power-law deviation). :class:`DominantOutlierDetector` wraps PyGOD's DOMINANT autoencoder.

Reporting only the learned detector would leave the obvious question unanswered: does the GNN
actually earn its complexity over a structural heuristic? Running both on every experiment makes
that comparison automatic, and if the baseline wins on a scenario, that is a result worth
reporting rather than hiding (BOND's benchmarking methodology, ``docs/RELATED_WORK.md`` Family 2).

A high score is not a verdict
-----------------------------
Scores rank entities for human attention. A legitimate power user can top the ranking, and
GraphGuard never auto-blocks (docs/DESIGN.md §14).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
import torch

from graphguard.config import NodeOutlierConfig
from graphguard.detection.base import Detector
from graphguard.graph.builder import GraphSnapshot, NodeKey


class EgonetOutlierDetector(Detector):
    """Classical structural baseline: deviation from the egonet power law.

    For each node, let ``N`` be the size of its ego-network (itself plus neighbours) and ``E`` the
    number of edges inside it. Across a population these obey a power law ``E ~ N^alpha``
    (Akoglu et al., *OddBall*). A node whose ``E`` departs sharply from the fitted value is
    structurally unusual — either far denser (a clique-like cluster) or far sparser (a hub
    touching many otherwise-unconnected entities), and the latter is what a key fanning out to
    many new models looks like.

    Learns nothing and has no hyper-parameters, which is exactly why it is a fair yardstick.
    """

    name = "node_outlier:egonet"

    def _raw_scores(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        adjacency = _undirected_adjacency(snapshot)
        keys = snapshot.all_node_keys()
        position = {key: i for i, key in enumerate(keys)}

        rows = [position[(node_type, entity_id)] for entity_id in snapshot.node_ids[node_type]]

        sizes = np.zeros(len(rows), dtype=np.float64)
        edges = np.zeros(len(rows), dtype=np.float64)
        for i, node in enumerate(rows):
            ego = adjacency[node] | {node}
            sizes[i] = len(ego)
            edges[i] = sum(len(adjacency[member] & ego) for member in ego) / 2.0

        return _oddball_scores(sizes, edges)


class DominantOutlierDetector(Detector):
    """PyGOD's DOMINANT: a graph autoencoder scoring attribute + structure reconstruction error.

    Nodes the model reconstructs poorly — whose features do not fit their neighbourhood, or whose
    neighbourhood does not fit their features — score high. Wrapped rather than reimplemented, per
    docs/DESIGN.md §13.

    The model is fitted **per snapshot**, so "unusual" always means unusual relative to the same
    window's population, never relative to a stale baseline.
    """

    name = "node_outlier:dominant"

    def __init__(self, cfg: NodeOutlierConfig, seed: int = 1337) -> None:
        """Configure the wrapped PyGOD detector from validated config."""
        super().__init__(score_node_types=cfg.score_node_types)
        self.cfg = cfg
        self.seed = seed
        self._cache: tuple[int, dict[NodeKey, float]] | None = None

    def _raw_scores(self, snapshot: GraphSnapshot, node_type: str) -> np.ndarray:
        scores = self._fit_snapshot(snapshot)
        return np.array(
            [scores[(node_type, entity_id)] for entity_id in snapshot.node_ids[node_type]],
            dtype=np.float64,
        )

    def _fit_snapshot(self, snapshot: GraphSnapshot) -> dict[NodeKey, float]:
        """Fit DOMINANT once per snapshot; scoring several node types reuses the cached fit."""
        if self._cache is not None and self._cache[0] == id(snapshot):
            return self._cache[1]

        from pygod.detector import DOMINANT

        torch.manual_seed(self.seed)
        data, keys = snapshot.to_homogeneous()

        params = self.cfg.pygod
        detector = DOMINANT(
            hid_dim=params.hid_dim,
            num_layers=params.num_layers,
            dropout=params.dropout,
            weight_decay=params.weight_decay,
            lr=params.lr,
            epoch=params.epochs,
            contamination=self.cfg.contamination,
            verbose=0,
        )
        with _full_batch_loading():
            detector.fit(data)
        raw = detector.decision_score_
        values = raw.detach().cpu().numpy() if isinstance(raw, torch.Tensor) else np.asarray(raw)

        scores = dict(zip(keys, (float(v) for v in values), strict=True))
        self._cache = (id(snapshot), scores)
        return scores


def build_node_outlier_detector(cfg: NodeOutlierConfig, seed: int = 1337) -> Detector:
    """Construct the node-outlier detector named by ``cfg.method``.

    Raises
    ------
    ValueError
        On an unknown method name, listing what is available — a typo in a config file should not
        silently fall back to a different algorithm than the operator asked for.

    """
    method = cfg.method.strip().lower()
    if method == "baseline:egonet":
        return EgonetOutlierDetector(score_node_types=cfg.score_node_types)
    if method == "pygod:dominant":
        return DominantOutlierDetector(cfg, seed=seed)
    raise ValueError(
        f"unknown node-outlier method {cfg.method!r}; "
        f"available: 'baseline:egonet', 'pygod:dominant'"
    )


# --------------------------------------------------------------------------- PyGOD plumbing


class _FullBatchLoader:
    """Stand-in for PyG's ``NeighborLoader`` that yields the entire graph as one batch.

    PyGOD's training loop always constructs a ``NeighborLoader``, whose sampler requires the
    native ``pyg-lib`` or ``torch-sparse`` extensions. Neither publishes a wheel for this platform
    (arm64 macOS on torch 2.13), so DOMINANT cannot run through its default data path here.

    Neighbour sampling exists purely for **scalability** — it approximates full-batch training on
    graphs too large to fit in memory. GraphGuard's snapshots hold a few hundred nodes, so
    full-batch is not merely an acceptable substitute, it is the exact computation sampling was
    approximating, and it is faster.

    Only the data-loading strategy is replaced. PyGOD's model, loss function and scoring are used
    unmodified, which keeps docs/DESIGN.md §13's "wrap PyGOD, never reimplement" intact.
    """

    def __init__(self, data: Any, num_neigh: Any = None, batch_size: int | None = None) -> None:
        """Accept ``NeighborLoader``'s signature; sampling arguments are deliberately ignored."""
        self.data = data

    def __iter__(self) -> Iterator[Any]:
        """Yield the whole graph once, annotated the way the training loop expects."""
        batch = copy.copy(self.data)
        num_nodes = int(self.data.x.shape[0])
        batch.batch_size = num_nodes
        batch.n_id = torch.arange(num_nodes)
        yield batch

    def __len__(self) -> int:
        return 1


@contextmanager
def _full_batch_loading() -> Iterator[None]:
    """Temporarily route PyGOD's training loop through :class:`_FullBatchLoader`.

    Scoped to the ``fit`` call and restored in a ``finally``, so an exception during training
    cannot leave PyGOD globally patched for the rest of the process.
    """
    import pygod.detector.base as pygod_base

    original = pygod_base.NeighborLoader
    pygod_base.NeighborLoader = _FullBatchLoader
    try:
        yield
    finally:
        pygod_base.NeighborLoader = original


# --------------------------------------------------------------------------- helpers


def _undirected_adjacency(snapshot: GraphSnapshot) -> list[set[int]]:
    """Adjacency sets over the homogeneous projection, ignoring edge direction and type.

    The egonet baseline is a purely structural measure, so relation types are intentionally
    collapsed here; the heterogeneous form remains available to every other component.
    """
    data, keys = snapshot.to_homogeneous()
    adjacency: list[set[int]] = [set() for _ in range(len(keys))]

    edge_index = data.edge_index
    if edge_index is None or edge_index.numel() == 0:
        return adjacency

    for source, target in edge_index.t().tolist():
        if source == target:
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)
    return adjacency


def _oddball_scores(sizes: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Score deviation from the fitted ``E ~ N^alpha`` egonet power law.

    The fit is done in log space by least squares. Nodes with a degenerate ego-network (a single
    node, no neighbours) score zero rather than infinity: an isolated node is uninformative, not
    maximally suspicious.
    """
    scores = np.zeros_like(sizes, dtype=np.float64)
    usable = (sizes > 1) & (edges > 0)
    if usable.sum() < 3:
        return scores  # too few points for a meaningful fit

    log_n = np.log(sizes[usable])
    log_e = np.log(edges[usable])
    slope, intercept = np.polyfit(log_n, log_e, 1)

    expected = np.exp(intercept + slope * log_n)
    actual = edges[usable]

    ratio = np.maximum(actual, expected) / np.maximum(np.minimum(actual, expected), 1e-9)
    scores[usable] = ratio * np.log1p(np.abs(actual - expected))
    return scores
