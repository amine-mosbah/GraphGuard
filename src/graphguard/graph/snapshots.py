"""Time-windowing — turning a continuous event stream into discrete graph snapshots.

Role
----
Splits a chronologically ordered ``TraceEvent`` stream into overlapping or tumbling windows. The
temporal graph is represented as a *sequence of snapshots*: node-outlier scoring runs within one,
and community drift is defined as change *across* them (docs/DESIGN.md §3.1).

Inputs / outputs
----------------
Input: sorted events plus :class:`~graphguard.config.SnapshotConfig`.
Output: :class:`Window` intervals and the events falling inside each, consumed by ``graph.builder``.

Windowing semantics
-------------------
Windows are half-open ``[start, end)``, so an event on a boundary belongs to exactly one tumbling
window and is never double-counted. When ``stride < window`` the windows overlap and an event
legitimately appears in several — that is what sliding windows are for, and it is the caller's job
not to treat overlapping snapshots as independent samples when computing metrics.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from graphguard.config import SnapshotConfig
from graphguard.schema import TraceEvent

_OFFSET_PATTERN = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)

_OFFSET_UNITS: dict[str, str] = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_offset(offset: str) -> timedelta:
    """Parse a pandas-style offset such as ``'1d'``, ``'6h'`` or ``'30m'``.

    Raises
    ------
    ValueError
        If the offset is malformed or zero. A zero-length window would produce infinitely many
        empty snapshots, so it is rejected rather than silently clamped.

    """
    match = _OFFSET_PATTERN.match(offset.strip())
    if match is None:
        raise ValueError(
            f"invalid offset {offset!r}; expected a number followed by one of s/m/h/d/w (e.g. '1d')"
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount == 0:
        raise ValueError(f"offset {offset!r} must be greater than zero")
    return timedelta(**{_OFFSET_UNITS[unit]: amount})


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open time interval ``[start, end)``."""

    index: int
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        """Length of the window."""
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside this window."""
        return self.start <= moment < self.end

    def __str__(self) -> str:
        """Render the interval compactly, for logs and figure captions."""
        return f"[{self.start.isoformat()} .. {self.end.isoformat()})"


def iter_windows(
    start: datetime,
    last: datetime,
    window: timedelta,
    stride: timedelta,
) -> Iterator[Window]:
    """Yield windows covering every moment in the **closed** interval ``[start, last]``.

    ``last`` is the timestamp of the final observation, not an exclusive bound. Windows keep being
    emitted while their start is at or before it, so the last observation always lands inside a
    window and a stream whose events share one timestamp still yields one window rather than none.
    Individual windows remain half-open ``[start, end)``.
    """
    if window <= timedelta(0) or stride <= timedelta(0):
        raise ValueError("window and stride must both be positive")

    index = 0
    cursor = start
    while cursor <= last:
        yield Window(index=index, start=cursor, end=cursor + window)
        cursor += stride
        index += 1


def make_windows(
    events: Sequence[TraceEvent],
    cfg: SnapshotConfig,
) -> list[Window]:
    """Build the window sequence spanning ``events``.

    Windows are anchored at the first event's timestamp rather than at a calendar boundary, so a
    dataset that starts mid-day is not given a truncated leading snapshot. The span is closed on
    the last event, so no trailing observation falls outside every window.
    """
    if not events:
        return []
    window = parse_offset(cfg.window)
    stride = parse_offset(cfg.stride)
    return list(iter_windows(events[0].timestamp, events[-1].timestamp, window, stride))


def events_in_window(events: Sequence[TraceEvent], window: Window) -> Sequence[TraceEvent]:
    """Slice the events falling inside ``window``.

    Assumes ``events`` is sorted by timestamp — the simulator and adapters both guarantee this —
    and uses binary search, so windowing a long stream stays cheap.
    """
    timestamps = _TimestampView(events)
    lo = bisect_left(timestamps, window.start)
    hi = bisect_left(timestamps, window.end)
    return events[lo:hi]


class _TimestampView(Sequence[datetime]):
    """Read-only timestamp view over an event sequence, so ``bisect`` needs no extra list."""

    __slots__ = ("_events",)

    def __init__(self, events: Sequence[TraceEvent]) -> None:
        self._events = events

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int) -> datetime:  # type: ignore[override]
        return self._events[index].timestamp


def split_into_windows(
    events: Sequence[TraceEvent],
    cfg: SnapshotConfig,
) -> list[tuple[Window, Sequence[TraceEvent]]]:
    """Return each window paired with the events inside it."""
    return [(window, events_in_window(events, window)) for window in make_windows(events, cfg)]
