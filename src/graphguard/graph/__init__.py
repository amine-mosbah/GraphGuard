"""Heterogeneous temporal graph construction.

``graph.model`` declares the schema, ``graph.snapshots`` handles time-windowing, and
``graph.builder`` turns a ``TraceEvent`` stream into snapshots. See ``docs/SCHEMA.md`` §2.
"""

from graphguard.graph.builder import GraphBuilder, GraphSnapshot, NodeKey
from graphguard.graph.model import (
    CORE_RELATIONS,
    FEATURE_DESCRIPTIONS,
    FEATURE_INDEX,
    FEATURE_NAMES,
    OWNS,
    USED_FROM,
    NodeType,
    Relation,
    relation_name,
)
from graphguard.graph.snapshots import Window, make_windows, parse_offset, split_into_windows

__all__ = [
    "CORE_RELATIONS",
    "FEATURE_DESCRIPTIONS",
    "FEATURE_INDEX",
    "FEATURE_NAMES",
    "OWNS",
    "USED_FROM",
    "GraphBuilder",
    "GraphSnapshot",
    "NodeKey",
    "NodeType",
    "Relation",
    "Window",
    "make_windows",
    "parse_offset",
    "relation_name",
    "split_into_windows",
]
