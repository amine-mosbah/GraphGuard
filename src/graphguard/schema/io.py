"""Serialisation for :class:`~graphguard.schema.events.TraceEvent` streams.

Role
----
JSONL is the on-disk interchange format for telemetry: one event per line, append-friendly,
streamable, and readable with ``head``/``jq`` when debugging. This is the format the Phase 1
jailbreak project exports and GraphGuard ingests (docs/DESIGN.md §10).

Inputs / outputs
----------------
Input: an iterable of ``TraceEvent`` (write) or a path (read).
Output: a JSONL file (write) or a lazy iterator of ``TraceEvent`` (read).

Reading is deliberately **lazy** — telemetry files are expected to outgrow memory, and the graph
builder consumes a stream.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO, cast

from pydantic import ValidationError

from graphguard.schema.events import SCHEMA_VERSION, TraceEvent


class SchemaVersionMismatchError(RuntimeError):
    """Raised when a trace file was written against an incompatible schema version.

    Failing loudly is the point: a silently mis-parsed trace file produces a wrong graph, and a
    wrong graph produces confident nonsense downstream.
    """


class TraceParseError(ValueError):
    """Raised when a line in a trace file is not a valid :class:`TraceEvent`.

    Carries the line number so a malformed 400k-line file can actually be debugged.
    """

    def __init__(self, path: Path, line_no: int, detail: str) -> None:
        """Record the offending location alongside the usual exception message."""
        super().__init__(f"{path}:{line_no}: {detail}")
        self.path = path
        self.line_no = line_no


def _open(path: Path, mode: str) -> TextIO:
    """Open a path for text I/O, transparently handling ``.gz``."""
    if path.suffix == ".gz":
        return cast("TextIO", gzip.open(path, mode + "t", encoding="utf-8"))
    return cast("TextIO", path.open(mode, encoding="utf-8"))


def write_jsonl(events: Iterable[TraceEvent], path: str | Path) -> int:
    """Write ``events`` to ``path`` as JSONL. Returns the number of events written.

    Parent directories are created if missing. A ``.gz`` suffix enables compression.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with _open(path, "w") as fh:
        for event in events:
            fh.write(event.model_dump_json(exclude_none=False))
            fh.write("\n")
            count += 1
    return count


def read_jsonl(
    path: str | Path,
    *,
    strict_version: bool = True,
) -> Iterator[TraceEvent]:
    """Lazily read ``TraceEvent`` records from a JSONL file.

    Parameters
    ----------
    path:
        Source file; ``.gz`` is decompressed transparently.
    strict_version:
        When True (default), a record whose ``schema_version`` differs from the current
        :data:`SCHEMA_VERSION` raises :class:`SchemaVersionMismatchError`. Set False only when
        deliberately reading an archived run.

    Raises
    ------
    TraceParseError
        If a line is not valid JSON or not a valid event, reported with its line number.

    """
    path = Path(path)
    with _open(path, "r") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceParseError(path, line_no, f"invalid JSON: {exc}") from exc

            version = raw.get("schema_version")
            if strict_version and version != SCHEMA_VERSION:
                raise SchemaVersionMismatchError(
                    f"{path}:{line_no}: trace was written with schema_version={version!r} but "
                    f"this build expects {SCHEMA_VERSION!r}. Re-export the traces or pass "
                    f"strict_version=False to read an archive."
                )

            try:
                yield TraceEvent.model_validate(raw)
            except ValidationError as exc:
                raise TraceParseError(path, line_no, str(exc)) from exc


def read_jsonl_list(path: str | Path, *, strict_version: bool = True) -> list[TraceEvent]:
    """Eager variant of :func:`read_jsonl`, for tests and small files."""
    return list(read_jsonl(path, strict_version=strict_version))
