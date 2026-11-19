"""Adapter interface — the boundary where every raw telemetry format stops.

Role
----
Defines :class:`Adapter`, the contract every ingestion source implements: take records in some
vendor's shape, emit canonical :class:`~graphguard.schema.TraceEvent`s. This is the seam that
makes the rest of the system format-agnostic — the graph builder, the detectors and the
correlation layer never learn that an HTTP access log and an OpenTelemetry span are different
things (docs/DESIGN.md §8.3).

Inputs / outputs
----------------
Input: raw records (mappings, as parsed from JSON/JSONL) from one source.
Output: ``TraceEvent`` objects, plus an :class:`IngestionReport` describing what was skipped.

Why adding a telemetry layer means writing an adapter and nothing else
----------------------------------------------------------------------
This is the concrete form of the project's extensibility claim. A relation in the graph is the
triple ``(actor_type, action, target_type)``, and each of those comes straight off a
``TraceEvent`` — so an adapter that emits well-typed events automatically produces correctly typed
graph edges, and every detector runs on them unchanged. The HTTP layer was added this way: one
adapter module, no change to the builder, no change to any detector.

Strict versus lenient
---------------------
Real telemetry contains malformed records. Both responses are legitimate and the caller must
choose, so neither is hard-coded:

- **strict** (default) — a bad record raises. Correct when ingesting a controlled export, where a
  parse failure means the contract is broken and silently continuing would corrupt the graph.
- **lenient** — a bad record is counted and skipped. Correct when tailing a production feed, where
  one malformed line should not stop the pipeline.

In both modes the skips are *reported* rather than swallowed. An ingestion path that quietly drops
records produces a graph missing edges nobody knows are missing, which is the worst outcome
available: every downstream conclusion is then drawn from data whose incompleteness is invisible.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphguard.schema import Layer, TraceEvent


class AdapterError(ValueError):
    """Raised when a raw record cannot be turned into a canonical event.

    Carries the offending record so a failure in a million-line feed can actually be diagnosed.
    """

    def __init__(self, adapter: str, detail: str, record: Mapping[str, Any] | None = None) -> None:
        """Record which adapter rejected what, and why."""
        super().__init__(f"{adapter}: {detail}")
        self.adapter = adapter
        self.record = dict(record) if record is not None else None


@dataclass
class IngestionReport:
    """What one ingestion pass did — including, explicitly, what it could not do."""

    adapter: str
    parsed: int = 0
    skipped: int = 0
    #: One message per skipped record, capped so a pathological feed cannot exhaust memory.
    reasons: list[str] = field(default_factory=list)
    _reason_cap: int = 50

    def record_skip(self, detail: str) -> None:
        """Note a skipped record."""
        self.skipped += 1
        if len(self.reasons) < self._reason_cap:
            self.reasons.append(detail)

    @property
    def total(self) -> int:
        """Records seen, parsed or not."""
        return self.parsed + self.skipped

    @property
    def skip_rate(self) -> float:
        """Fraction of records that could not be parsed."""
        return self.skipped / self.total if self.total else 0.0

    def summary(self) -> str:
        """One line an operator can read in a log."""
        line = f"{self.adapter}: parsed {self.parsed:,} of {self.total:,} records"
        if self.skipped:
            line += f" ({self.skipped:,} skipped, {self.skip_rate:.1%})"
        return line


class Adapter(ABC):
    """Base class for telemetry adapters.

    Subclasses implement :meth:`parse_record` for a single raw record. Everything else — iteration,
    error policy, reporting, file reading — is handled here so no adapter can invent its own
    failure semantics.
    """

    #: Stable identifier, used in reports and configuration.
    name: str = "adapter"
    #: Telemetry layer every event from this adapter belongs to.
    layer: Layer

    def __init__(self, *, strict: bool = True) -> None:
        """Choose the error policy for this adapter instance."""
        self.strict = strict
        self.report = IngestionReport(adapter=self.name)

    # ------------------------------------------------------------------ subclass API

    @abstractmethod
    def parse_record(self, record: Mapping[str, Any]) -> TraceEvent | None:
        """Convert one raw record into a canonical event.

        Return ``None`` for a record that is *legitimately* not an event of interest (a health
        check, a static asset request). Raise :class:`AdapterError` for a record that should have
        been an event but is malformed — the distinction matters, because the first is normal and
        the second is a data-quality problem an operator needs to see.
        """

    # ------------------------------------------------------------------ public API

    def parse(self, records: Iterable[Mapping[str, Any]]) -> Iterator[TraceEvent]:
        """Convert a stream of raw records, applying the configured error policy."""
        for record in records:
            try:
                event = self.parse_record(record)
            except AdapterError as exc:
                if self.strict:
                    raise
                self.report.record_skip(str(exc))
                continue
            except (KeyError, TypeError, ValueError) as exc:
                # A subclass that forgot to wrap its own failure still must not crash a lenient
                # pipeline, nor pass a half-built event downstream.
                wrapped = AdapterError(self.name, f"unhandled parse failure: {exc}", record)
                if self.strict:
                    raise wrapped from exc
                self.report.record_skip(str(wrapped))
                continue

            if event is None:
                continue
            self.report.parsed += 1
            yield event

    def parse_jsonl(self, path: str | Path) -> Iterator[TraceEvent]:
        """Convert a JSONL file of raw records, one object per line."""
        path = Path(path)
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    error = AdapterError(self.name, f"{path}:{line_no}: invalid JSON: {exc}")
                    if self.strict:
                        raise error from exc
                    self.report.record_skip(str(error))
                    continue
                yield from self.parse([record])

    def reset_report(self) -> None:
        """Start a fresh ingestion report."""
        self.report = IngestionReport(adapter=self.name)


def first_present(record: Mapping[str, Any], *keys: str) -> Any | None:
    """Return the first key present and non-empty in ``record``.

    Telemetry vendors disagree about field names for the same concept — a client address is
    ``client.address`` in OpenTelemetry, ``remote_addr`` in nginx, ``sourceIPAddress`` in
    CloudTrail. Accepting a list of aliases keeps that disagreement inside the adapter, which is
    exactly where it belongs.
    """
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None
