"""The "invisible in raw logs, obvious in the graph" comparison (docs/DESIGN.md §5 (feature 6)).

Role
----
Implements the flat-log rules a competent analyst would actually write against raw telemetry — no
graph, no history, no peer context — and scores them on **exactly the same (node, window) grid**
as the graph detectors, so the comparison is apples-to-apples.

Inputs / outputs
----------------
Input: raw ``TraceEvent``s, the snapshot windows, ground truth, and the graph detectors' results.
Output: a :class:`ComparisonResult` table — one :class:`~graphguard.eval.metrics.EvaluationResult`
per method — for the CLI, saved runs and the report's centrepiece figure.

Fairness rules
--------------
The comparison is only meaningful if the baselines are *strong* versions of themselves:

- Each baseline sees the same events the graph builder saw, cut into the same windows.
- Baselines emit **continuous scores**, not binary flags, so they get the same benefit of
  ROC/AP evaluation instead of being strawmanned by one arbitrary threshold.
- If a baseline beats a graph detector on some scenario, that result is reported as-is
  (``docs/THREAT_MODEL.md`` §4).

What the baselines cannot see — and the graph can — is *context*: how this key's volume compares
to **its own** past, whether these models are new **for this key**, whether the IP is new
**for this key**. A flat rule keyed on absolute thresholds has to pay for every legitimate
night-shift batch job and every legitimately heavy app key. That structural difference, not tuning,
is what the figure demonstrates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from graphguard.detection.base import Signal
from graphguard.eval.metrics import EvaluationResult, evaluate
from graphguard.graph.builder import NodeKey
from graphguard.graph.snapshots import Window, events_in_window
from graphguard.schema import Action, ActorType, TraceEvent
from graphguard.simulator.labels import GroundTruth


@dataclass(frozen=True)
class BaselineScore:
    """A flat-log rule's score for one (node, window) pair."""

    node: NodeKey
    window: Window
    score: float


@dataclass(frozen=True)
class ComparisonResult:
    """All methods evaluated on the same grid.

    ``results`` pools every scenario; ``per_scenario`` breaks each method down by attack
    scenario (method -> scenario -> result), which is what the report actually quotes.
    """

    results: dict[str, EvaluationResult]
    per_scenario: dict[str, dict[str, EvaluationResult]] = field(default_factory=dict)
    #: Each method's scored signals, retained so figures can recompute PR/ROC curves from the
    #: same observations the metrics were derived from. Never serialised.
    method_signals: dict[str, list[Signal]] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, for saved runs."""
        return {
            "pooled": {name: result.to_dict() for name, result in self.results.items()},
            "per_scenario": {
                name: {scenario: r.to_dict() for scenario, r in by_scenario.items()}
                for name, by_scenario in self.per_scenario.items()
            },
        }

    def matrix(self, metric: str = "roc_auc") -> dict[str, dict[str, float | None]]:
        """Detector-vs-scenario table for one metric, for figures and quick inspection."""
        return {
            name: {scenario: getattr(r, metric) for scenario, r in by_scenario.items()}
            for name, by_scenario in self.per_scenario.items()
        }


# --------------------------------------------------------------------------- baselines


def _per_key_window_events(
    events: Sequence[TraceEvent], windows: Sequence[Window]
) -> dict[Window, dict[str, list[TraceEvent]]]:
    """Group AI-layer api_key events by window and key, once, for all baselines."""
    grouped: dict[Window, dict[str, list[TraceEvent]]] = {}
    for window in windows:
        per_key: dict[str, list[TraceEvent]] = defaultdict(list)
        for event in events_in_window(events, window):
            if event.actor_type is ActorType.API_KEY:
                per_key[event.actor_id].append(event)
        grouped[window] = per_key
    return grouped


def volume_threshold_baseline(
    grouped: dict[Window, dict[str, list[TraceEvent]]],
) -> list[BaselineScore]:
    """Rule: "alert on keys with unusually many events this window".

    Score = the key's event count in the window. The strongest version of the everyday SIEM
    volume rule: continuous, so evaluation picks every possible threshold.
    """
    scores: list[BaselineScore] = []
    for window, per_key in grouped.items():
        for key_id, key_events in per_key.items():
            scores.append(
                BaselineScore(node=("api_key", key_id), window=window, score=float(len(key_events)))
            )
    return scores


def error_rate_baseline(
    grouped: dict[Window, dict[str, list[TraceEvent]]],
) -> list[BaselineScore]:
    """Rule: "alert on keys with a high error rate this window"."""
    scores: list[BaselineScore] = []
    for window, per_key in grouped.items():
        for key_id, key_events in per_key.items():
            errors = sum(1 for e in key_events if e.action is Action.ERROR)
            scores.append(
                BaselineScore(
                    node=("api_key", key_id),
                    window=window,
                    score=errors / len(key_events) if key_events else 0.0,
                )
            )
    return scores


def off_hours_baseline(
    grouped: dict[Window, dict[str, list[TraceEvent]]],
    working_hours: tuple[int, int],
) -> list[BaselineScore]:
    """Rule: "alert on keys active outside business hours".

    This is the rule the deliberately-awkward population punishes: legitimate batch jobs run at
    03:00 every night, and this rule cannot help ranking them at the top.
    """
    start, end = working_hours
    scores: list[BaselineScore] = []
    for window, per_key in grouped.items():
        for key_id, key_events in per_key.items():
            night = sum(
                1 for e in key_events if e.timestamp.hour < start or e.timestamp.hour >= end
            )
            scores.append(
                BaselineScore(
                    node=("api_key", key_id),
                    window=window,
                    score=night / len(key_events) if key_events else 0.0,
                )
            )
    return scores


BASELINE_BUILDERS = {
    "volume_threshold": lambda grouped, hours: volume_threshold_baseline(grouped),
    "error_rate_threshold": lambda grouped, hours: error_rate_baseline(grouped),
    "off_hours_rule": off_hours_baseline,
}


# --------------------------------------------------------------------------- comparison


def _baseline_to_signals(name: str, scores: list[BaselineScore]) -> list[Signal]:
    """Adapt baseline scores to the Signal shape so one evaluation path serves everything.

    Baselines carry no reasons or evidence — they are rules, not explained detectors; that absence
    is itself part of the comparison story.
    """
    return [
        Signal(
            detector=name,
            node=score.node,
            window=score.window,
            raw_score=score.score,
            normalised_score=score.score,
            reasons=(),
            evidence=(),
        )
        for score in scores
    ]


def compare(
    events: Sequence[TraceEvent],
    windows: Sequence[Window],
    truth: GroundTruth,
    detector_results: dict[str, list[Signal]],
    *,
    baselines: list[str],
    working_hours: tuple[int, int],
    k_values: list[int],
) -> ComparisonResult:
    """Evaluate flat-log baselines and graph detectors on the identical grid.

    ``detector_results`` maps a display name to that detector's signals; baseline names come from
    config (``evaluation.log_vs_graph.baselines``).
    """
    grouped = _per_key_window_events(events, windows)

    method_signals: dict[str, list[Signal]] = {}
    for name in baselines:
        builder = BASELINE_BUILDERS.get(name)
        if builder is None:
            raise ValueError(f"unknown baseline {name!r}; available: {sorted(BASELINE_BUILDERS)}")
        method_signals[f"baseline:{name}"] = _baseline_to_signals(
            f"baseline:{name}", builder(grouped, working_hours)
        )
    for name, signals in detector_results.items():
        method_signals[name] = [s for s in signals if s.node_type == "api_key"]

    results = {
        name: evaluate(name, signals, truth, k_values) for name, signals in method_signals.items()
    }

    # Per-scenario breakdown: each detector targets a different attack mode, so a pooled number
    # scores every detector against attacks it was never meant to catch. This matrix is the
    # honest reporting unit and the source of the report's detector-vs-scenario table.
    per_scenario = {
        name: {
            scenario: evaluate(name, signals, truth, k_values, scenario=scenario)
            for scenario in sorted(truth.scenarios)
        }
        for name, signals in method_signals.items()
    }

    return ComparisonResult(
        results=results, per_scenario=per_scenario, method_signals=method_signals
    )
