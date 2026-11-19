"""Dataset assembly: benign traffic + injected attacks + ground truth, as one reproducible unit.

Role
----
Orchestrates the simulator. Builds the population, generates benign traffic, injects every enabled
attack scenario, merges everything into one chronological event stream, and pairs it with the
ground-truth intervals the evaluation harness needs.

Inputs / outputs
----------------
Input: a validated :class:`~graphguard.config.GraphGuardConfig`.
Output: a :class:`Dataset` — events, ground truth and the population that produced them — plus
:func:`write_dataset`, which persists all three next to the config that generated them.

Reproducibility
---------------
A single seed drives the whole pipeline, and :func:`write_dataset` saves the effective config
alongside the data (docs/DESIGN.md §13). Any reported figure can therefore be regenerated from
the files sitting next to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from graphguard.config import GraphGuardConfig
from graphguard.schema import TraceEvent, write_jsonl
from graphguard.simulator.benign import CallPlan, plan_benign_calls, plan_to_event
from graphguard.simulator.http_traffic import (
    HTTPPlan,
    generate_benign_http,
    inject_credential_stuffing,
    inject_external_to_internal,
    plans_to_events,
)
from graphguard.simulator.labels import GroundTruth, ScenarioLabel
from graphguard.simulator.population import Population, build_population
from graphguard.simulator.scenarios.coordinated_ring import inject_coordinated_ring
from graphguard.simulator.scenarios.slow_group_drift import apply_slow_group_drift
from graphguard.simulator.scenarios.stolen_key import inject_stolen_key

EVENTS_FILENAME = "events.jsonl"
LABELS_FILENAME = "labels.json"
CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class Dataset:
    """A generated telemetry dataset with its ground truth."""

    events: tuple[TraceEvent, ...]
    ground_truth: GroundTruth
    population: Population

    def summary(self) -> dict[str, object]:
        """Headline counts, for run logs and the report's dataset description."""
        attack_events = sum(1 for e in self.events if e.is_attack)
        return {
            "events": len(self.events),
            "benign_events": len(self.events) - attack_events,
            "attack_events": attack_events,
            "attack_event_fraction": (
                round(attack_events / len(self.events), 5) if self.events else 0.0
            ),
            "population": self.population.summary(),
            "labelled_entities": self.ground_truth.summary(),
            "start": self.events[0].timestamp.isoformat() if self.events else None,
            "end": self.events[-1].timestamp.isoformat() if self.events else None,
        }


def generate_dataset(cfg: GraphGuardConfig) -> Dataset:
    """Generate benign traffic, inject enabled attack scenarios, and return the labelled dataset.

    Attack plans are merged with benign plans and re-sorted, so a compromised key's malicious and
    legitimate calls interleave exactly as they would in a real log — the owner keeps working
    while the attacker is active.
    """
    rng = np.random.default_rng(cfg.run.seed)

    population = build_population(cfg.simulator.benign, rng)
    plans: list[CallPlan] = plan_benign_calls(cfg.simulator, population, rng)

    labels: list[ScenarioLabel] = []
    for inject in (inject_stolen_key, inject_coordinated_ring):
        attack_plans, attack_labels = inject(cfg.simulator, population, rng)
        plans.extend(attack_plans)
        labels.extend(attack_labels)

    # Drift runs last and *rewrites* existing plans rather than adding any, so it must see the
    # final plan list. Keys already claimed by another scenario are excluded: overlapping labels
    # would make per-scenario evaluation ambiguous about which attack a detection belongs to.
    plans, drift_labels = apply_slow_group_drift(
        cfg.simulator,
        population,
        plans,
        rng,
        exclude={label.entity_id for label in labels},
    )
    labels.extend(drift_labels)

    plans.sort(key=lambda p: p.timestamp)
    events: list[TraceEvent] = [plan_to_event(p) for p in plans]

    # ---- HTTP layer (Layer 2) -------------------------------------------------------------
    # Emitted after the AI layer so the chain scenario can avoid compromising a key another
    # scenario already owns. Both layers land in one event stream and one graph; the `ip` vertex
    # they share is what makes cross-layer correlation possible (docs/DESIGN.md §7).
    http_plans: list[HTTPPlan] = []
    if cfg.simulator.http.enabled:
        http_plans.extend(generate_benign_http(cfg.simulator, population, rng))

    stuffing_plans, stuffing_labels = inject_credential_stuffing(cfg.simulator, rng)
    http_plans.extend(stuffing_plans)
    labels.extend(stuffing_labels)

    chain_http, chain_ai, chain_labels = inject_external_to_internal(
        cfg.simulator,
        population,
        rng,
        exclude={label.entity_id for label in labels},
    )
    http_plans.extend(chain_http)
    events.extend(chain_ai)
    labels.extend(chain_labels)

    if http_plans:
        http_plans.sort(key=lambda p: p.timestamp)
        events.extend(plans_to_events(http_plans))

    events.sort(key=lambda e: e.timestamp)

    return Dataset(
        events=tuple(events),
        ground_truth=GroundTruth.from_labels(labels),
        population=population,
    )


def write_dataset(dataset: Dataset, cfg: GraphGuardConfig, out_dir: str | Path) -> Path:
    """Persist events, ground truth and the generating config into ``out_dir``.

    The three files travel together on purpose: data without its labels cannot be evaluated, and
    either without its config cannot be reproduced.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(dataset.events, out_dir / EVENTS_FILENAME)

    labels_payload = [
        {
            "scenario": label.scenario,
            "entity_type": label.entity_type,
            "entity_id": label.entity_id,
            "start": label.start.isoformat(),
            "end": label.end.isoformat(),
        }
        for label in dataset.ground_truth.labels
    ]
    (out_dir / LABELS_FILENAME).write_text(
        json.dumps(labels_payload, indent=2) + "\n", encoding="utf-8"
    )

    cfg.save(out_dir / CONFIG_FILENAME)
    return out_dir


def read_labels(path: str | Path) -> GroundTruth:
    """Read ground truth back from a written dataset."""
    from datetime import datetime

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return GroundTruth.from_labels(
        ScenarioLabel(
            scenario=item["scenario"],
            entity_type=item["entity_type"],
            entity_id=item["entity_id"],
            start=datetime.fromisoformat(item["start"]),
            end=datetime.fromisoformat(item["end"]),
        )
        for item in payload
    )
