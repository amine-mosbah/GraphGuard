"""Canonical telemetry schema — the contract shared by every GraphGuard component.

See :mod:`graphguard.schema.events` for the model and ``docs/SCHEMA.md`` for the specification.
"""

from graphguard.schema.events import (
    BENIGN_LABEL,
    SCHEMA_VERSION,
    Action,
    ActorType,
    Attr,
    Layer,
    Outcome,
    TargetType,
    TraceEvent,
)
from graphguard.schema.io import (
    SchemaVersionMismatchError,
    TraceParseError,
    read_jsonl,
    read_jsonl_list,
    write_jsonl,
)

__all__ = [
    "BENIGN_LABEL",
    "SCHEMA_VERSION",
    "Action",
    "ActorType",
    "Attr",
    "Layer",
    "Outcome",
    "SchemaVersionMismatchError",
    "TargetType",
    "TraceEvent",
    "TraceParseError",
    "read_jsonl",
    "read_jsonl_list",
    "write_jsonl",
]
