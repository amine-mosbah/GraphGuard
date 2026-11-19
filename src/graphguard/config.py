"""Typed configuration — the one place runtime parameters are defined.

Role
----
Loads and validates ``config/default.yaml`` into pydantic models. No module hard-codes a tunable;
everything adjustable lives in the YAML and reaches code through :class:`GraphGuardConfig`.

This serves two purposes at once (``docs/DECISIONS.md`` D4). Engineering: a reproducible
experiment needs its configuration to be a serialisable artifact saved next to its results, which
:meth:`GraphGuardConfig.save` provides. Product: the administrator role
needs an explicit configuration surface, and this is it.

Inputs / outputs
----------------
Input: a YAML file (default ``config/default.yaml``).
Output: a validated :class:`GraphGuardConfig`, consumed by the simulator, graph builder,
detectors, correlation and evaluation layers.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_ENV_VAR = "GRAPHGUARD_CONFIG"
DEFAULT_CONFIG_RELPATH = Path("config") / "default.yaml"


class _Base(BaseModel):
    """Shared model settings: unknown keys are an error, not a silent no-op.

    A typo in a config file must not leave a detector running on its defaults while the operator
    believes it was reconfigured.
    """

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- run


class RunConfig(_Base):
    """Global run settings."""

    seed: int = Field(1337, description="Fixed seed for every stochastic component.")
    output_dir: Path = Field(Path("runs"), description="Where experiment runs are saved.")


# --------------------------------------------------------------------------- ingestion


class AdapterConfig(_Base):
    """Toggle and settings for one telemetry adapter."""

    enabled: bool = False
    source_format: str | None = None


class IngestionConfig(_Base):
    """Which telemetry adapters are active (tiers per docs/DESIGN.md §6)."""

    adapters: dict[str, AdapterConfig] = Field(default_factory=dict)


# --------------------------------------------------------------------------- simulator


class BenignConfig(_Base):
    """Parameters of the synthetic benign population and its traffic."""

    n_users: int = Field(60, gt=0)
    persona_mix: dict[str, float] = Field(default_factory=dict)
    working_hours: tuple[int, int] = (8, 18)
    weekend_activity_factor: float = Field(0.25, ge=0.0, le=1.0)
    error_rate: float = Field(0.02, ge=0.0, le=1.0)
    refusal_rate: float = Field(0.01, ge=0.0, le=1.0)

    @field_validator("persona_mix")
    @classmethod
    def _mix_sums_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("persona_mix must not be empty")
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"persona_mix must sum to 1.0, got {total:.6f}")
        return v

    @field_validator("working_hours")
    @classmethod
    def _valid_hours(cls, v: tuple[int, int]) -> tuple[int, int]:
        start, end = v
        if not (0 <= start < end <= 24):
            raise ValueError(f"working_hours must satisfy 0 <= start < end <= 24, got {v}")
        return v


class StolenKeyConfig(_Base):
    """Behavioural deltas that define the stolen-key attack signature."""

    enabled: bool = True
    n_victims: int = Field(3, gt=0)
    onset_fraction: float = Field(0.6, ge=0.0, lt=1.0)
    volume_multiplier: float = Field(8.0, gt=0.0)
    off_hours_bias: float = Field(0.85, ge=0.0, le=1.0)
    new_model_ratio: float = Field(0.7, ge=0.0, le=1.0)
    error_rate: float = Field(0.12, ge=0.0, le=1.0)


class CoordinatedRingConfig(_Base):
    """Parameters of the coordinated-ring attack (attacker class C).

    One actor drives many freshly created accounts in unison. The signature is
    *similarity across accounts*, not deviation of any single one.
    """

    enabled: bool = True
    n_accounts: int = Field(18, gt=1)
    onset_fraction: float = Field(0.35, ge=0.0, lt=1.0)
    calls_per_day: float = Field(60.0, gt=0.0)
    #: Seconds of jitter applied to each member's copy of the shared burst schedule.
    #: Small = eerily synchronized; large = loosely coordinated (harder to detect).
    sync_jitter_s: float = Field(90.0, ge=0.0)
    #: Number of coordinated bursts per day.
    bursts_per_day: int = Field(6, gt=0)


class SlowGroupDriftConfig(_Base):
    """Parameters of the slow-group-drift scenario (attacker classes B/D).

    A whole cohort's behaviour migrates gradually. The defining property is that **no member is
    an outlier in any single window** — only the trajectory across snapshots reveals it.
    """

    enabled: bool = True
    cohort_size: int = Field(10, gt=1)
    onset_fraction: float = Field(0.25, ge=0.0, lt=1.0)
    #: Fraction of the cohort's calls diverted to the new shared target by the end of the ramp.
    final_model_shift: float = Field(0.8, ge=0.0, le=1.0)
    #: How far the cohort's activity has slid, in hours, by the end of the ramp.
    final_hour_shift: float = Field(5.0, ge=0.0, le=12.0)


class CredentialStuffingConfig(_Base):
    """A pure external attack: one address, many failed logins, no breach (attacker class A)."""

    enabled: bool = True
    onset_fraction: float = Field(0.45, ge=0.0, lt=1.0)
    attempts_per_hour: float = Field(120.0, gt=0.0)
    duration_hours: int = Field(4, gt=0)
    #: Last octet of the attacker address within the external range.
    ip_offset: int = Field(40, ge=2, le=254)


class ExternalToInternalConfig(_Base):
    """The hybrid chain: external breach, then internal abuse (attacker class E).

    The showcase scenario of docs/DESIGN.md §7 — the one whose evidence is a *path* rather than any
    single event.
    """

    enabled: bool = True
    onset_fraction: float = Field(0.55, ge=0.0, lt=1.0)
    attempts_per_hour: float = Field(90.0, gt=0.0)
    #: Hours of brute force before one attempt succeeds.
    stuffing_hours: int = Field(3, gt=0)
    #: Quiet period between the breach and the first API call — an attacker who pivots
    #: instantly is easier to catch than one who waits.
    pivot_delay_hours: float = Field(6.0, ge=0.0)
    calls_per_day: float = Field(120.0, gt=0.0)
    ip_offset: int = Field(41, ge=2, le=254)


class ScenariosConfig(_Base):
    """Attack scenarios available to the simulator.

    Scenarios are added here as they are implemented; the catalogue lives in
    ``docs/THREAT_MODEL.md`` §3.
    """

    stolen_key: StolenKeyConfig = Field(default_factory=StolenKeyConfig)
    coordinated_ring: CoordinatedRingConfig = Field(default_factory=CoordinatedRingConfig)
    slow_group_drift: SlowGroupDriftConfig = Field(default_factory=SlowGroupDriftConfig)
    credential_stuffing: CredentialStuffingConfig = Field(default_factory=CredentialStuffingConfig)
    external_to_internal: ExternalToInternalConfig = Field(default_factory=ExternalToInternalConfig)


class HTTPTrafficConfig(_Base):
    """Benign gateway traffic (Layer 2)."""

    enabled: bool = True
    requests_per_key_per_day: float = Field(18.0, ge=0.0)
    #: Humans mistype passwords. Without this, "a failed login" would be an attack signal on its
    #: own and the credential-stuffing scenario would be trivially separable.
    login_failure_rate: float = Field(0.06, ge=0.0, le=1.0)


class SimulatorConfig(_Base):
    """Synthetic telemetry generation (docs/DESIGN.md §6: synthetic-first, always)."""

    start: datetime
    days: int = Field(14, gt=0)
    benign: BenignConfig = Field(default_factory=BenignConfig)
    http: HTTPTrafficConfig = Field(default_factory=HTTPTrafficConfig)
    scenarios: ScenariosConfig = Field(default_factory=ScenariosConfig)

    @field_validator("start")
    @classmethod
    def _require_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("simulator.start must be timezone-aware (e.g. end it with '+00:00')")
        return v


# --------------------------------------------------------------------------- graph


class SnapshotConfig(_Base):
    """Time-windowing for the temporal graph."""

    window: str = Field("1d", description="Pandas-style offset, e.g. '1h', '6h', '1d'.")
    stride: str = Field("1d", description="Equal to window => tumbling; smaller => sliding.")


class GraphConfig(_Base):
    """Graph construction settings (docs/DESIGN.md §3.1)."""

    snapshot: SnapshotConfig = Field(default_factory=SnapshotConfig)
    aggregate_parallel_edges: bool = True
    min_node_events: int = Field(1, ge=0)
    working_hours: tuple[int, int] = Field(
        (8, 18),
        description=(
            "Business hours of the monitored organisation, defining the `night_ratio` feature. "
            "A deployment property, not a simulator one — it must be set even when telemetry is "
            "real."
        ),
    )

    @field_validator("working_hours")
    @classmethod
    def _valid_hours(cls, v: tuple[int, int]) -> tuple[int, int]:
        start, end = v
        if not (0 <= start < end <= 24):
            raise ValueError(f"working_hours must satisfy 0 <= start < end <= 24, got {v}")
        return v


# --------------------------------------------------------------------------- detection


class PyGODConfig(_Base):
    """Hyper-parameters passed through to a PyGOD detector."""

    hid_dim: int = Field(64, gt=0)
    num_layers: int = Field(2, gt=0)
    epochs: int = Field(100, gt=0)
    lr: float = Field(0.005, gt=0.0)
    dropout: float = Field(0.0, ge=0.0, lt=1.0)
    weight_decay: float = Field(0.0, ge=0.0)


class NodeOutlierConfig(_Base):
    """Node-outlier detector settings ("impostor scoring", docs/DESIGN.md §3.2)."""

    enabled: bool = True
    method: str = Field("pygod:dominant", description="'pygod:<name>' or 'baseline:egonet'.")
    pygod: PyGODConfig = Field(default_factory=PyGODConfig)
    contamination: float = Field(
        0.05,
        gt=0.0,
        lt=0.5,
        description="Assumed fraction of anomalous nodes — an assumption, not ground truth.",
    )
    score_node_types: list[str] = Field(default_factory=lambda: ["api_key", "user"])


class CommunityDriftConfig(_Base):
    """Community-drift detector settings (docs/DESIGN.md §3.2)."""

    enabled: bool = True
    #: Cosine similarity between two entities' *displacement* vectors above which they count as
    #: moving in the same direction. Benign displacement directions are uncorrelated (median
    #: ~0.00 measured), so this sits far above the noise floor rather than near a class boundary.
    coherence_threshold: float = Field(0.35, gt=-1.0, le=1.0)
    #: Minimum displacement magnitude (total-variation units) for an entity to count as moving at
    #: all. Filters the constant churn of ordinary behaviour before any grouping happens.
    min_displacement: float = Field(0.08, ge=0.0)
    #: Groups smaller than this are not reported — a "drifting group" of two is just two entities
    #: changing, which is node-outlier's job, not this detector's.
    min_community_size: int = Field(4, gt=1)
    #: Number of past snapshots an entity's own baseline is averaged over.
    baseline_windows: int = Field(3, gt=0)
    score_node_types: list[str] = Field(default_factory=lambda: ["api_key"])


class AbnormalSubgraphConfig(_Base):
    """Abnormal-subgraph detector settings (unison detection, docs/DESIGN.md §3.2)."""

    enabled: bool = True
    #: Combined similarity above which two entities count as behaving "in unison".
    #: Legitimate same-persona keys are *similar*; puppeted accounts are near-identical
    #: across features, targets and minute-level timing simultaneously.
    similarity_threshold: float = Field(0.8, gt=0.0, le=1.0)
    #: Smallest cluster that counts as a ring. Pairs and triples of near-identical
    #: keys occur naturally (one team, two service keys); ten do not.
    min_cluster_size: int = Field(5, gt=1)
    #: Relation names whose target-distribution is EXCLUDED from the behaviour vector.
    #: `used_from` is excluded by default: which specific IP a member uses is
    #: infrastructure identity, not behaviour — account farms rotate proxies precisely
    #: so that per-member addresses differ, and rewarding that would be a gift.
    ignore_relations: list[str] = Field(default_factory=lambda: ["used_from"])
    score_node_types: list[str] = Field(default_factory=lambda: ["api_key"])


class DetectionConfig(_Base):
    """The three-detector suite (docs/DESIGN.md §3.2)."""

    node_outlier: NodeOutlierConfig = Field(default_factory=NodeOutlierConfig)
    community_drift: CommunityDriftConfig = Field(default_factory=CommunityDriftConfig)
    abnormal_subgraph: AbnormalSubgraphConfig = Field(default_factory=AbnormalSubgraphConfig)


# --------------------------------------------------------------------------- correlation


class ExplanationConfig(_Base):
    """How much context an alert explanation carries (docs/DESIGN.md §3.3)."""

    top_k_reasons: int = Field(3, gt=0)


class CrossLayerConfig(_Base):
    """Cross-layer attack-chain search (docs/DESIGN.md §7)."""

    enabled: bool = True
    #: Blocked authentication attempts from one address within a window, above which that
    #: address is treated as conducting a brute-force attempt. Set above what a team of humans
    #: mistyping passwords from a shared office address could plausibly produce.
    min_failed_logins: int = Field(40, gt=0)
    #: How long after a brute-force burst a credential first appearing on that address still
    #: counts as the pivot. Too short misses a patient attacker; too long links unrelated events.
    max_pivot_hours: float = Field(72.0, gt=0.0)
    #: Minimum normalised detector score for the internal stage to count as corroborated.
    min_internal_score: float = Field(0.5, ge=0.0, le=1.0)


class CorrelationConfig(_Base):
    """Signal merging, ranking and explanation."""

    alert_threshold: float = Field(0.75, ge=0.0, le=1.0)
    max_alerts: int = Field(50, gt=0)
    explanation: ExplanationConfig = Field(default_factory=ExplanationConfig)
    #: Taxonomy frameworks whose tags persisted alerts carry (see correlation.tags). Both are
    #: on by default because the report and the threat model promise tagged findings; an empty
    #: list is the explicit way to disable tagging, not the accidental one.
    tag_frameworks: list[str] = Field(default_factory=lambda: ["MITRE_ATLAS", "OWASP_LLM_TOP10"])
    cross_layer: CrossLayerConfig = Field(default_factory=CrossLayerConfig)


# --------------------------------------------------------------------------- evaluation


class LogVsGraphConfig(_Base):
    """The "invisible in raw logs, obvious in the graph" comparison."""

    enabled: bool = True
    baselines: list[str] = Field(default_factory=list)


class EvaluationConfig(_Base):
    """Evaluation harness settings (docs/DESIGN.md §5 (feature 6), BOND-style reporting)."""

    metrics: list[str] = Field(default_factory=list)
    k_values: list[int] = Field(default_factory=list)
    log_vs_graph: LogVsGraphConfig = Field(default_factory=LogVsGraphConfig)


# --------------------------------------------------------------------------- api


class ApiConfig(_Base):
    """FastAPI service binding."""

    host: str = "127.0.0.1"
    port: int = Field(8000, gt=0, lt=65536)


# --------------------------------------------------------------------------- root


class GraphGuardConfig(_Base):
    """Root configuration object."""

    run: RunConfig = Field(default_factory=RunConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    simulator: SimulatorConfig
    graph: GraphConfig = Field(default_factory=GraphConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    correlation: CorrelationConfig = Field(default_factory=CorrelationConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

    @model_validator(mode="after")
    def _victims_fit_population(self) -> GraphGuardConfig:
        stolen = self.simulator.scenarios.stolen_key
        if stolen.enabled and stolen.n_victims > self.simulator.benign.n_users:
            raise ValueError(
                f"stolen_key.n_victims ({stolen.n_victims}) exceeds the benign population "
                f"({self.simulator.benign.n_users} users): there are not enough keys to compromise"
            )
        return self

    def save(self, path: str | Path) -> Path:
        """Snapshot the effective configuration next to a run's results.

        Reproducibility requires knowing exactly which parameters produced a figure, so every
        experiment run writes this alongside its metrics (docs/DESIGN.md §13).
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json")
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return path


def find_default_config(start: Path | None = None) -> Path:
    """Locate ``config/default.yaml`` by walking up from ``start`` (or this file).

    Searching upward keeps the package working both from a source checkout and from a subdirectory,
    without hard-coding an absolute path.
    """
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / DEFAULT_CONFIG_RELPATH
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"could not find {DEFAULT_CONFIG_RELPATH} above {here}; "
        f"pass an explicit path or set {CONFIG_ENV_VAR}"
    )


def load_config(path: str | Path | None = None) -> GraphGuardConfig:
    """Load and validate configuration.

    Resolution order: explicit ``path`` → ``$GRAPHGUARD_CONFIG`` → nearest ``config/default.yaml``.
    """
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        path = Path(env_path) if env_path else find_default_config()
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GraphGuardConfig.model_validate(raw)
