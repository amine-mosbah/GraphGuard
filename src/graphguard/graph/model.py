"""Node and edge type registry — the graph schema as a first-class artifact.

Role
----
Declares what may exist in the heterogeneous graph: node types, relation triples, and the node
feature layout. Everything about the graph's *shape* is stated here rather than scattered across
the builder, so the schema can be read, tested and drawn.

Inputs / outputs
----------------
Input: nothing (leaf module — pure declarations).
Output: type/relation constants and :data:`FEATURE_NAMES`, consumed by ``graph.builder``, the
detectors (for naming contributing features) and the correlation layer (for explanations).

Documented artifact
-------------------
The graph-schema registry is the system's **data model** (GraphGuard's equivalent of an ER
diagram) and part of its **class diagram**. Keeping it as declarations rather than comments means
the figure can be generated from the code instead of drifting away from it.

Relation naming
---------------
A relation is the PyG triple ``(source_type, action, target_type)``. Because a ``TraceEvent``
already carries ``actor_type``, ``action`` and ``target_type``, most relations are *derived* from
events with no lookup table: an event is its own edge type. Two relations are **derived from
context** rather than from the event's action, and are declared explicitly below.
"""

from __future__ import annotations

from enum import StrEnum

#: A PyG relation triple.
Relation = tuple[str, str, str]


class NodeType(StrEnum):
    """Entity types that can appear as graph vertices.

    The first four are used by the AI layer (Core). The rest are declared now so the schema
    diagram shows the intended extension points for Layer 2 and Stretch (docs/DESIGN.md §6), even
    before those adapters exist.
    """

    # --- AI layer (Core) ---
    USER = "user"
    API_KEY = "api_key"
    MODEL = "model"
    IP = "ip"
    # --- HTTP layer (Layer 2) ---
    ENDPOINT = "endpoint"
    # --- network/auth layer (Stretch) ---
    HOST = "host"
    SERVICE = "service"
    # --- Phase 1 jailbreak traces ---
    ATTACKER_SESSION = "attacker_session"
    DETECTOR = "detector"


#: Relation derived from ``attributes["user_id"]``: which user owns a credential.
#: This is what lets a compromised key be traced back to a human owner in an alert.
OWNS: Relation = (NodeType.USER, "owns", NodeType.API_KEY)

#: Relation derived from ``source_ip``: where an actor's traffic originated.
#:
#: This is the load-bearing relation of the whole design. The ``ip`` node is the single vertex
#: the HTTP and AI layers share, which is what turns two unrelated incidents into one traversable
#: path and makes cross-layer attack-chain correlation possible at all (docs/DESIGN.md §7).
USED_FROM: Relation = (NodeType.API_KEY, "used_from", NodeType.IP)

#: Relations produced directly by AI-layer events, for documentation and validation.
AI_LAYER_RELATIONS: tuple[Relation, ...] = (
    (NodeType.API_KEY, "call", NodeType.MODEL),
    (NodeType.API_KEY, "error", NodeType.MODEL),
    (NodeType.API_KEY, "refusal", NodeType.MODEL),
)

#: Context relations the builder materialises rather than reading off an event's action.
DERIVED_RELATIONS: tuple[Relation, ...] = (OWNS, USED_FROM)

#: Every relation the Core (AI) layer can produce.
CORE_RELATIONS: tuple[Relation, ...] = AI_LAYER_RELATIONS + DERIVED_RELATIONS


# --------------------------------------------------------------------------- features

#: Ordered node feature layout, shared by every node type.
#:
#: A single shared layout (rather than per-type features) is required because PyGOD's detectors
#: operate on a homogeneous graph, and ``HeteroData.to_homogeneous()`` can only stack node features
#: when every type has the same width. Features that are meaningless for a type are simply zero —
#: a ``model`` node has no ``distinct_models``, for instance.
#:
#: Names are load-bearing, not cosmetic: explanations quote them back to the analyst, so each one
#: must be a phrase a human can act on ("this key's traffic was 9x its usual volume").
FEATURE_NAMES: tuple[str, ...] = (
    # --- volume and connectivity within the window ---
    "events",
    "out_degree",
    "in_degree",
    "distinct_neighbours",
    "distinct_sessions",
    # --- outcome mix ---
    "error_ratio",
    "refusal_ratio",
    # --- temporal shape ---
    "night_ratio",
    "weekend_ratio",
    "hour_entropy",
    "burstiness",
    # --- payload ---
    "tokens_in_mean",
    "tokens_out_mean",
    # --- relation to the node's own past (zero in the first window) ---
    "neighbour_novelty",
    "volume_ratio",
)

#: Features whose value is defined only relative to earlier snapshots.
HISTORY_FEATURES: frozenset[str] = frozenset({"neighbour_novelty", "volume_ratio"})

#: Index of each feature name, for detectors that need to name a contributing dimension.
FEATURE_INDEX: dict[str, int] = {name: i for i, name in enumerate(FEATURE_NAMES)}

#: Human-readable meaning of each feature, used verbatim when building explanations.
FEATURE_DESCRIPTIONS: dict[str, str] = {
    "events": "number of events involving this entity in the window",
    "out_degree": "distinct entities this one acted on",
    "in_degree": "distinct entities that acted on this one",
    "distinct_neighbours": "distinct entities connected in either direction",
    "distinct_sessions": "distinct sessions this entity appeared in",
    "error_ratio": "share of this entity's events that failed",
    "refusal_ratio": "share of this entity's events that were refused by a guardrail",
    "night_ratio": "share of activity outside working hours",
    "weekend_ratio": "share of activity at the weekend",
    "hour_entropy": "how spread out across the day the activity was (low = very regular)",
    "burstiness": "variability of the gaps between events (high = arrives in bursts)",
    "tokens_in_mean": "average prompt size",
    "tokens_out_mean": "average completion size",
    "neighbour_novelty": "share of this window's connections never seen for this entity before",
    "volume_ratio": "this window's activity relative to this entity's own recent average",
}


def relation_name(relation: Relation) -> str:
    """Render a relation triple as a readable string, e.g. ``api_key-[call]->model``."""
    source, action, target = relation
    return f"{source}-[{action}]->{target}"


def validate_registry() -> None:
    """Check the registry's internal consistency.

    Called by tests rather than at import time; a schema that contradicts itself should fail a
    build, not a production start-up.
    """
    if len(FEATURE_NAMES) != len(set(FEATURE_NAMES)):
        raise ValueError("FEATURE_NAMES contains duplicates")

    missing = set(FEATURE_NAMES) - set(FEATURE_DESCRIPTIONS)
    if missing:
        raise ValueError(f"features without a description cannot be explained: {sorted(missing)}")

    unknown = set(FEATURE_DESCRIPTIONS) - set(FEATURE_NAMES)
    if unknown:
        raise ValueError(f"descriptions for unknown features: {sorted(unknown)}")

    if not set(FEATURE_NAMES) >= HISTORY_FEATURES:
        raise ValueError("HISTORY_FEATURES references a feature that is not in FEATURE_NAMES")

    for relation in CORE_RELATIONS:
        source, _, target = relation
        for node_type in (source, target):
            if node_type not in set(NodeType):
                raise ValueError(f"relation {relation} references unknown node type {node_type!r}")
