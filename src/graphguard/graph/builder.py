"""Graph construction — the only component that knows graph internals.

Role
----
Turns a ``TraceEvent`` stream into a sequence of heterogeneous temporal graph snapshots, each a
PyG :class:`~torch_geometric.data.HeteroData` with engineered node features. Detectors consume
this documented interface and never touch raw events (docs/DESIGN.md §8.3).

Inputs / outputs
----------------
Input: chronologically sorted ``TraceEvent``s and :class:`~graphguard.config.GraphConfig`.
Output: a list of :class:`GraphSnapshot`, consumed by ``graphguard.detection``.

What becomes a node and an edge
-------------------------------
Per ``docs/SCHEMA.md`` §2: actor and target become nodes, the event becomes a timestamped edge of
type ``action``, and two further relations are derived from context — ``user -[owns]-> api_key``
from ``attributes["user_id"]``, and ``actor -[used_from]-> ip`` from ``source_ip``. The ``ip``
node is what lets telemetry layers share vertices and makes cross-layer correlation possible.

Two feature matrices, deliberately
----------------------------------
Each snapshot carries **raw** features and **model-ready** features:

- ``raw_features`` are the values an analyst reads ("9.4x its usual volume", "83% at night").
  Explanations quote these, so they must stay in human units.
- ``data[node_type].x`` is the same information with heavy-tailed counts ``log1p``-compressed,
  because a detector fed a feature ranging over five orders of magnitude will model that one
  dimension and ignore the rest.

Collapsing the two would force a choice between a detector that trains badly and an explanation
that says "log-volume 8.7", which is not something a security analyst can act on.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import torch
from torch_geometric.data import Data, HeteroData

from graphguard.config import GraphConfig
from graphguard.graph.model import (
    FEATURE_INDEX,
    FEATURE_NAMES,
    OWNS,
    USED_FROM,
    NodeType,
    Relation,
)
from graphguard.graph.snapshots import Window, split_into_windows
from graphguard.schema import Action, Attr, Outcome, TraceEvent

#: Node identity: ``(type, id)``.
NodeKey = tuple[str, str]

#: Features that are heavy-tailed counts and get ``log1p`` compression for the model matrix.
_LOG_SCALED: frozenset[str] = frozenset(
    {
        "events",
        "out_degree",
        "in_degree",
        "distinct_neighbours",
        "distinct_sessions",
        "tokens_in_mean",
        "tokens_out_mean",
        "volume_ratio",
    }
)


@dataclass
class _NodeStats:
    """Per-node accumulator for one window.

    Inter-arrival statistics are accumulated in streaming form (running sums, not a timestamp
    list): a popular ``model`` node can appear in tens of thousands of events per window, and
    retaining every timestamp would make memory scale with traffic rather than with the graph.
    """

    events: int = 0
    out_neighbours: set[NodeKey] = field(default_factory=set)
    in_neighbours: set[NodeKey] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    errors: int = 0
    refusals: int = 0
    night: int = 0
    weekend: int = 0
    hour_counts: np.ndarray = field(default_factory=lambda: np.zeros(24, dtype=np.int64))
    #: 15-minute activity profile (96 bins). Aggregate features (entropy, night ratio) cannot
    #: express *when exactly* a node acts; this can, and cross-node comparison of these
    #: profiles is what makes burst synchronicity — the coordinated-ring signature — visible.
    quarter_hour_counts: np.ndarray = field(default_factory=lambda: np.zeros(96, dtype=np.int64))
    tokens_in: float = 0.0
    tokens_out: float = 0.0
    token_samples: int = 0
    last_seen: datetime | None = None
    gap_sum: float = 0.0
    gap_sq_sum: float = 0.0
    gap_count: int = 0

    def observe(self, event: TraceEvent, night_hours: frozenset[int]) -> None:
        """Fold one incident event into this node's statistics."""
        self.events += 1
        if event.session_id is not None:
            self.sessions.add(event.session_id)
        if event.outcome is Outcome.ERROR or event.action is Action.ERROR:
            self.errors += 1
        if event.outcome is Outcome.REFUSE or event.action is Action.REFUSAL:
            self.refusals += 1

        hour = event.timestamp.hour
        self.hour_counts[hour] += 1
        self.quarter_hour_counts[hour * 4 + event.timestamp.minute // 15] += 1
        if hour in night_hours:
            self.night += 1
        if event.timestamp.weekday() >= 5:
            self.weekend += 1

        tokens_in = event.attributes.get(Attr.TOKENS_IN)
        tokens_out = event.attributes.get(Attr.TOKENS_OUT)
        if isinstance(tokens_in, int | float):
            self.tokens_in += float(tokens_in)
            self.tokens_out += float(tokens_out) if isinstance(tokens_out, int | float) else 0.0
            self.token_samples += 1

        if self.last_seen is not None:
            gap = (event.timestamp - self.last_seen).total_seconds()
            if gap >= 0:
                self.gap_sum += gap
                self.gap_sq_sum += gap * gap
                self.gap_count += 1
        self.last_seen = event.timestamp

    @property
    def neighbours(self) -> set[NodeKey]:
        """All distinct neighbours, in either direction."""
        return self.out_neighbours | self.in_neighbours

    def hour_entropy(self) -> float:
        """Shannon entropy of the hour-of-day distribution, in bits.

        Low entropy means a rigid schedule (a cron job); high entropy means activity smeared across
        the day. This is what distinguishes *regular* off-hours automation from an attacker who
        simply works at night.
        """
        total = self.hour_counts.sum()
        if total == 0:
            return 0.0
        probabilities = self.hour_counts[self.hour_counts > 0] / total
        return float(-(probabilities * np.log2(probabilities)).sum())

    def burstiness(self) -> float:
        """Coefficient of variation of inter-arrival gaps.

        0 means perfectly evenly spaced; larger values mean arrival in bursts. Computed from
        running sums, so it costs nothing to keep for a very high-degree node.
        """
        if self.gap_count < 2:
            return 0.0
        mean = self.gap_sum / self.gap_count
        if mean <= 0:
            return 0.0
        variance = max(self.gap_sq_sum / self.gap_count - mean * mean, 0.0)
        return float(math.sqrt(variance) / mean)


@dataclass
class _NodeHistory:
    """What the builder remembers about a node from *earlier* windows.

    This is what makes "stopped behaving like itself" expressible. Without carried history, a
    snapshot can only support peer comparison ("unlike other keys"), never self comparison
    ("unlike its own past"), and the latter is the stolen-key signal.
    """

    seen_neighbours: set[NodeKey] = field(default_factory=set)
    windows_seen: int = 0
    event_total: int = 0

    @property
    def mean_events(self) -> float:
        """Average events per window in which this node was active."""
        return self.event_total / self.windows_seen if self.windows_seen else 0.0


@dataclass(frozen=True)
class GraphSnapshot:
    """One time-windowed heterogeneous graph."""

    window: Window
    data: HeteroData
    #: Ordered entity ids per node type; index i here matches row i of that type's features.
    node_ids: dict[str, tuple[str, ...]]
    #: Human-readable feature values, aligned with :data:`FEATURE_NAMES`.
    raw_features: dict[str, np.ndarray]
    #: Per-type 15-minute activity profiles, shape (n_nodes, 96), rows aligned with node_ids.
    #: Kept separate from the feature matrices: detectors that compare *when* nodes act
    #: (burst synchronicity) need the full profile, but 96 near-sparse columns would drown the
    #: engineered features if stacked into the model matrix.
    activity_profiles: dict[str, np.ndarray]
    n_events: int

    def node_keys(self, node_type: str) -> list[NodeKey]:
        """Graph identities for one node type, in feature-row order."""
        return [(node_type, entity_id) for entity_id in self.node_ids.get(node_type, ())]

    def all_node_keys(self) -> list[NodeKey]:
        """Every node key, ordered to match :meth:`to_homogeneous`."""
        keys: list[NodeKey] = []
        for node_type in self.data.node_types:
            keys.extend(self.node_keys(node_type))
        return keys

    def raw_feature(self, node_type: str, entity_id: str, feature: str) -> float:
        """Look up one human-readable feature value, for building an explanation."""
        row = self.node_ids[node_type].index(entity_id)
        return float(self.raw_features[node_type][row, FEATURE_INDEX[feature]])

    def to_homogeneous(self) -> tuple[Data, list[NodeKey]]:
        """Flatten to a homogeneous graph, with the node ordering that matches it.

        PyGOD's detectors operate on homogeneous graphs, so scoring requires this projection. The
        heterogeneous form remains the canonical representation — node and edge *types* survive as
        ``node_type`` and ``edge_type`` tensors, so nothing about the schema is lost, and the
        returned key list keeps every score traceable back to a real entity.
        """
        return self.data.to_homogeneous(), self.all_node_keys()

    def summary(self) -> dict[str, object]:
        """Per-type node and edge counts, for run logs and the report."""
        return {
            "window": str(self.window),
            "events": self.n_events,
            "nodes": {nt: len(self.node_ids.get(nt, ())) for nt in self.data.node_types},
            "edges": {
                "-".join(rel): int(self.data[rel].edge_index.size(1))
                for rel in self.data.edge_types
            },
        }


class GraphBuilder:
    """Builds heterogeneous temporal graph snapshots from a ``TraceEvent`` stream.

    Stateful across windows: it carries per-node history so each snapshot can express how a node
    changed relative to its own past.
    """

    def __init__(self, cfg: GraphConfig) -> None:
        """Configure windowing, edge aggregation and what counts as "night"."""
        self.cfg = cfg
        start, end = cfg.working_hours
        self.night_hours = frozenset(h for h in range(24) if h < start or h >= end)
        self._history: dict[NodeKey, _NodeHistory] = {}

    # ------------------------------------------------------------------ public API

    def build_snapshots(self, events: Sequence[TraceEvent]) -> list[GraphSnapshot]:
        """Build one snapshot per time window, in chronological order.

        History accumulates as windows are processed, so this must be called on a chronological
        stream; the first window has no past and its history features are therefore neutral.
        """
        self._history = {}
        snapshots: list[GraphSnapshot] = []
        for window, window_events in split_into_windows(events, self.cfg.snapshot):
            if not window_events:
                continue
            snapshots.append(self._build_one(window, window_events))
        return snapshots

    # ------------------------------------------------------------------ internals

    def _build_one(self, window: Window, events: Sequence[TraceEvent]) -> GraphSnapshot:
        stats: dict[NodeKey, _NodeStats] = defaultdict(_NodeStats)
        edge_weights: dict[Relation, dict[tuple[NodeKey, NodeKey], int]] = defaultdict(
            lambda: defaultdict(int)
        )

        for event in events:
            actor = event.actor_key
            target = event.target_key

            # 1. The event itself: actor --[action]--> target.
            relation: Relation = (
                event.actor_type.value,
                event.action.value,
                event.target_type.value,
            )
            self._add_edge(edge_weights, relation, actor, target, stats)
            stats[actor].observe(event, self.night_hours)
            stats[target].observe(event, self.night_hours)

            # 2. Context: where the traffic came from. The shared vertex across layers.
            # On the gateway layer the actor *is* the address, so deriving a used_from edge
            # there would create an ip->ip self-loop and observe the same node twice per
            # event, doubling every gateway address's features. The relation only carries
            # information when the actor and the address are different entities.
            if event.source_ip is not None:
                ip_node: NodeKey = (NodeType.IP.value, event.source_ip)
                if ip_node != actor:
                    ip_relation: Relation = (
                        event.actor_type.value,
                        USED_FROM[1],
                        NodeType.IP.value,
                    )
                    self._add_edge(edge_weights, ip_relation, actor, ip_node, stats)
                    stats[ip_node].observe(event, self.night_hours)

            # 3. Context: who owns the credential.
            user_id = event.attributes.get(Attr.USER_ID)
            if isinstance(user_id, str) and user_id:
                user_node: NodeKey = (NodeType.USER.value, user_id)
                owns_relation: Relation = (NodeType.USER.value, OWNS[1], event.actor_type.value)
                self._add_edge(edge_weights, owns_relation, user_node, actor, stats)
                stats[user_node].observe(event, self.night_hours)

        active = {
            node: stat for node, stat in stats.items() if stat.events >= self.cfg.min_node_events
        }
        return self._assemble(window, events, active, edge_weights)

    @staticmethod
    def _add_edge(
        edge_weights: dict[Relation, dict[tuple[NodeKey, NodeKey], int]],
        relation: Relation,
        source: NodeKey,
        target: NodeKey,
        stats: dict[NodeKey, _NodeStats],
    ) -> None:
        edge_weights[relation][(source, target)] += 1
        stats[source].out_neighbours.add(target)
        stats[target].in_neighbours.add(source)

    def _assemble(
        self,
        window: Window,
        events: Sequence[TraceEvent],
        stats: dict[NodeKey, _NodeStats],
        edge_weights: dict[Relation, dict[tuple[NodeKey, NodeKey], int]],
    ) -> GraphSnapshot:
        # Stable node ordering: sorted by id within each type, so a rebuild is reproducible.
        by_type: dict[str, list[str]] = defaultdict(list)
        for node_type, entity_id in stats:
            by_type[node_type].append(entity_id)
        node_ids = {nt: tuple(sorted(ids)) for nt, ids in sorted(by_type.items())}
        index_of: dict[NodeKey, int] = {
            (nt, entity_id): i for nt, ids in node_ids.items() for i, entity_id in enumerate(ids)
        }

        raw_features: dict[str, np.ndarray] = {}
        activity_profiles: dict[str, np.ndarray] = {}
        for node_type, ids in node_ids.items():
            matrix = np.zeros((len(ids), len(FEATURE_NAMES)), dtype=np.float64)
            profiles = np.zeros((len(ids), 96), dtype=np.float64)
            for row, entity_id in enumerate(ids):
                node = (node_type, entity_id)
                matrix[row] = self._feature_row(node, stats[node])
                profiles[row] = stats[node].quarter_hour_counts
            raw_features[node_type] = matrix
            activity_profiles[node_type] = profiles

        self._update_history(stats)

        data = HeteroData()
        for node_type, ids in node_ids.items():
            data[node_type].x = torch.tensor(
                _to_model_features(raw_features[node_type]), dtype=torch.float32
            )
            data[node_type].num_nodes = len(ids)

        for relation, pairs in edge_weights.items():
            sources, targets, weights = [], [], []
            for (source, target), count in pairs.items():
                if source not in index_of or target not in index_of:
                    continue  # endpoint filtered out by min_node_events
                sources.append(index_of[source])
                targets.append(index_of[target])
                weights.append(count if self.cfg.aggregate_parallel_edges else 1)
            if not sources:
                continue
            data[relation].edge_index = torch.tensor([sources, targets], dtype=torch.long)
            data[relation].edge_weight = torch.tensor(weights, dtype=torch.float32)

        return GraphSnapshot(
            window=window,
            data=data,
            node_ids=node_ids,
            raw_features=raw_features,
            activity_profiles=activity_profiles,
            n_events=len(events),
        )

    def _feature_row(self, node: NodeKey, stat: _NodeStats) -> np.ndarray:
        events = float(stat.events)
        neighbours = stat.neighbours
        history = self._history.get(node)

        if history is None or not neighbours:
            novelty = 0.0
        else:
            unseen = neighbours - history.seen_neighbours
            novelty = len(unseen) / len(neighbours)

        if history is None or history.mean_events <= 0:
            # No past to compare against: 1.0 means "exactly as usual", the neutral value.
            volume_ratio = 1.0
        else:
            volume_ratio = events / history.mean_events

        row = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
        row[FEATURE_INDEX["events"]] = events
        row[FEATURE_INDEX["out_degree"]] = len(stat.out_neighbours)
        row[FEATURE_INDEX["in_degree"]] = len(stat.in_neighbours)
        row[FEATURE_INDEX["distinct_neighbours"]] = len(neighbours)
        row[FEATURE_INDEX["distinct_sessions"]] = len(stat.sessions)
        row[FEATURE_INDEX["error_ratio"]] = stat.errors / events if events else 0.0
        row[FEATURE_INDEX["refusal_ratio"]] = stat.refusals / events if events else 0.0
        row[FEATURE_INDEX["night_ratio"]] = stat.night / events if events else 0.0
        row[FEATURE_INDEX["weekend_ratio"]] = stat.weekend / events if events else 0.0
        row[FEATURE_INDEX["hour_entropy"]] = stat.hour_entropy()
        row[FEATURE_INDEX["burstiness"]] = stat.burstiness()
        row[FEATURE_INDEX["tokens_in_mean"]] = (
            stat.tokens_in / stat.token_samples if stat.token_samples else 0.0
        )
        row[FEATURE_INDEX["tokens_out_mean"]] = (
            stat.tokens_out / stat.token_samples if stat.token_samples else 0.0
        )
        row[FEATURE_INDEX["neighbour_novelty"]] = novelty
        row[FEATURE_INDEX["volume_ratio"]] = volume_ratio
        return row

    def _update_history(self, stats: dict[NodeKey, _NodeStats]) -> None:
        """Fold this window into the carried history, *after* features were computed.

        Ordering matters: updating before feature computation would make every node's neighbours
        "already seen", silently zeroing novelty for the entire dataset.
        """
        for node, stat in stats.items():
            history = self._history.setdefault(node, _NodeHistory())
            history.seen_neighbours |= stat.neighbours
            history.windows_seen += 1
            history.event_total += stat.events


def _to_model_features(raw: np.ndarray) -> np.ndarray:
    """Compress heavy-tailed count features for model consumption.

    Ratios and entropies are already bounded and are left untouched; counts spanning several orders
    of magnitude are ``log1p``-compressed so no single dimension dominates reconstruction error.
    """
    out = raw.copy()
    for name in _LOG_SCALED:
        column = FEATURE_INDEX[name]
        out[:, column] = np.log1p(np.clip(out[:, column], 0.0, None))
    return out
