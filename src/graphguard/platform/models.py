"""ORM models — the persistent state a deployed GraphGuard keeps.

Role
----
Defines what survives a process restart. Deliberately, that is *not* the graph: snapshots and
feature matrices are rebuilt from telemetry on every run and live in memory (``DECISIONS.md`` O3).
What persists is the state a graph cannot hold — who the analysts are, which alerts they have
judged, what they concluded, and which runs produced those alerts.

Why alerts are persisted but the graph is not
---------------------------------------------
An alert acquires a history the moment a human touches it. "Analyst X marked this false positive
at 14:02 with this reasoning" is not derivable from telemetry, cannot be recomputed, and is the
audit trail a security team is actually required to keep. The graph, by contrast, is a pure
function of the event stream and the configuration, both of which are already persisted, so
storing it would create a second copy that can drift from its own inputs.

The consequence is that :class:`Alert` rows are *materialised* from
:class:`~graphguard.detection.base.Signal` objects at the end of a run, not queried live.

Inputs / outputs
----------------
Input: pipeline output (signals, attack chains, run metrics) and analyst actions.
Output: rows consumed by the API's triage, audit and export endpoints.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from graphguard.platform.db import Base

#: JSON storage that is queryable on the deployment target and portable in tests.
#: Postgres gets ``JSONB``, which is binary, indexable with GIN and searchable by key — which is
#: what makes it reasonable to keep an alert's explanation and evidence as documents rather than
#: exploding them into columns that differ per detector. SQLite falls back to text-backed ``JSON``,
#: which cannot be indexed but behaves identically through the ORM.
JSONColumn = JSONB().with_variant(JSON(), "sqlite")


def _enum_column(enum_type: type[enum.Enum], length: int, constraint_name: str) -> Enum:
    """Build an enum column that stores member *values*, not member names.

    SQLAlchemy stores ``.name`` by default, which would put ``ADMIN`` in the database while the
    API serialises the same field as ``admin``. Two spellings of one value is a needless trap for
    anyone reading a schema dump or writing a manual query, so the storage form is pinned to the
    value. ``native_enum=False`` keeps the column a ``VARCHAR`` rather than a Postgres ``ENUM``
    type, because altering a native enum requires a migration SQLite cannot mirror.

    ``create_constraint=True`` is set explicitly because SQLAlchemy defaults it to *False*: without
    it the column is an unconstrained ``VARCHAR(n)`` that will happily accept ``role='superadmin'``
    from a bug, a migration script or a manual query. Verified against a live Postgres — the
    invalid value was accepted before this was enabled. For a security tool whose whole
    authorisation model rests on this column, the database should be the last line of defence
    rather than trusting every writer to be the ORM.

    ``constraint_name`` is passed per column rather than derived from the enum class, because
    SQLAlchemy names an enum's check constraint after the *type*, which pre-empts the metadata
    naming convention. Two columns sharing one enum on the same table — ``alert_state_changes``
    has both ``from_state`` and ``to_state`` — would then request the same constraint name twice
    and Postgres rejects the second.
    """
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        create_constraint=True,
        name=constraint_name,
        values_callable=lambda members: [m.value for m in members],
    )


def _uuid() -> str:
    """Return a new opaque identifier.

    Primary keys are random UUIDs rather than sequential integers so that an identifier appearing
    in a URL, an export or a screenshot leaks neither the number of alerts in the system nor their
    ordering.
    """
    return uuid.uuid4().hex


def _now() -> datetime:
    """Return the current instant, timezone-aware.

    Naive timestamps are rejected throughout GraphGuard (``DECISIONS.md`` D7) because a naive value
    assumed to be UTC corrupts temporal analysis invisibly. The same rule applies to the database.
    """
    return datetime.now(UTC)


class Role(enum.StrEnum):
    """What a user is allowed to do.

    Two roles, matching the two human actors the system models. ``ANALYST`` can
    read telemetry and triage alerts; ``ADMIN`` additionally changes detection configuration and
    manages users. Configuration is admin-only because a detector threshold silently loosened is
    indistinguishable, from the queue, from an absence of attacks.
    """

    ANALYST = "analyst"
    ADMIN = "admin"


class AlertState(enum.StrEnum):
    """Where an alert sits in the triage workflow.

    The state machine is the analyst triage workflow, and the legal transitions are
    enforced in the service layer rather than here, so that an invalid transition produces a clear
    API error instead of a constraint violation.
    """

    NEW = "new"
    INVESTIGATING = "investigating"
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    EXPORTED = "exported"


class RunStatus(enum.StrEnum):
    """Lifecycle of a pipeline run dispatched to the worker."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class User(Base):
    """An account that can sign in to the platform."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # Never a password. The column is named for what it holds so that a reader of a
    # schema dump cannot mistake it for one.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        _enum_column(Role, 16, "ck_users_role"), nullable=False, default=Role.ANALYST
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    state_changes: Mapped[list[AlertStateChange]] = relationship(back_populates="actor")

    def __repr__(self) -> str:
        """Identify the row without ever rendering the password hash."""
        return f"<User {self.email} role={self.role.value}>"


class Run(Base):
    """One execution of the detection pipeline, and the artifacts it produced."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        _enum_column(RunStatus, 16, "ck_runs_status"), nullable=False, default=RunStatus.QUEUED
    )
    #: Celery task id, so a queued run can be polled or revoked.
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: The effective analytical configuration, snapshotted at dispatch. D4 requires a run to be
    #: reproducible from its own artifacts, which means the config travels with the results rather
    #: than being looked up afterwards from a file that may since have changed.
    config: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: Pre-aggregated event counts per time bucket, for the dashboard timeline.
    #:
    #: Computed once here rather than on request because the raw event stream is not persisted at
    #: all — it is regenerated from the config and the seed — so serving a timeline live would mean
    #: re-running the simulator on every page load. A run of the default configuration produces
    #: ~220k events, which must never reach a browser in raw form regardless.
    timeline: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: The entity graph as the dashboard's network map draws it: nodes with their type,
    #: risk and human-readable attributes, plus the relations between them.
    #:
    #: Materialised for the same reason as :attr:`timeline` — the graph is a pure function of
    #: telemetry that is not persisted, so serving it live would mean re-running the simulator
    #: and the builder on every page load. A snapshot holds a few hundred nodes, so storing it
    #: costs little and makes the map instant.
    graph: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    #: Populated when status is FAILED, so a failure is diagnosable from the API alone.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    alerts: Mapped[list[Alert]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    chains: Mapped[list[AttackChainRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Identify the row in logs and test failures."""
        return f"<Run {self.name} status={self.status.value}>"


class Alert(Base):
    """A scored, explained observation about one entity in one window, as an analyst sees it.

    Materialised from a :class:`~graphguard.detection.base.Signal`. The score fields and the
    explanation are copied verbatim rather than recomputed, so that what an analyst judged is
    exactly what the detector said at the time — a later configuration change must not
    retroactively alter the evidence attached to a closed investigation.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        # One detector produces at most one alert per entity per window; re-running a
        # pipeline into the same Run must update rather than duplicate.
        UniqueConstraint(
            "run_id", "detector", "entity_type", "entity_id", "window_index", name="uq_alert_slot"
        ),
        # The triage queue is "open alerts, worst first" — the single hottest query.
        Index("ix_alerts_queue", "state", "normalised_score"),
        Index("ix_alerts_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    detector: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)

    window_index: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    raw_score: Mapped[float] = mapped_column(Float, nullable=False)
    normalised_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    #: The rendered plain-language explanation (docs/DESIGN.md §3.3) — stored, not regenerated,
    #: for the same reason the scores are.
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Structured contributing factors: [{feature, value, peer_median, deviation}, ...].
    reasons: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: Neighbouring node identities that form the evidence subgraph.
    evidence: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: MITRE ATLAS / OWASP LLM Top 10 tags (docs/DESIGN.md §3.3).
    tags: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    #: Ground-truth label when the run came from the simulator; null in production. Kept out of
    #: the ranking path entirely — it exists for evaluation and for the report's figures.
    ground_truth: Mapped[str | None] = mapped_column(String(64), nullable=True)

    state: Mapped[AlertState] = mapped_column(
        _enum_column(AlertState, 20, "ck_alerts_state"), nullable=False, default=AlertState.NEW
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    run: Mapped[Run] = relationship(back_populates="alerts")
    state_changes: Mapped[list[AlertStateChange]] = relationship(
        back_populates="alert",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AlertStateChange.created_at",
    )
    notes: Mapped[list[AnalystNote]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Identify the row in logs and test failures."""
        return f"<Alert {self.detector} {self.entity_type}:{self.entity_id} w{self.window_index}>"


class AlertStateChange(Base):
    """One transition in an alert's triage history — the audit trail.

    Append-only by convention: the service layer inserts, never updates or deletes. An alert's
    current state is denormalised onto :attr:`Alert.state` for the queue query, but this table is
    the record of how it got there, and the two are written in the same transaction.
    """

    __tablename__ = "alert_state_changes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Null for the initial transition into NEW, which the system performs, not a user.
    from_state: Mapped[AlertState | None] = mapped_column(
        _enum_column(AlertState, 20, "ck_alert_state_changes_from_state"), nullable=True
    )
    to_state: Mapped[AlertState] = mapped_column(
        _enum_column(AlertState, 20, "ck_alert_state_changes_to_state"), nullable=False
    )
    #: SET NULL rather than CASCADE: deleting a user must not erase the history of decisions
    #: they made, which is the whole point of an audit trail.
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    alert: Mapped[Alert] = relationship(back_populates="state_changes")
    actor: Mapped[User | None] = relationship(back_populates="state_changes")


class AnalystNote(Base):
    """Free-text commentary an analyst attaches to an alert during investigation."""

    __tablename__ = "analyst_notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    alert: Mapped[Alert] = relationship(back_populates="notes")


class AttackChainRecord(Base):
    """A cross-layer attack chain found by the correlator (docs/DESIGN.md §7).

    Stored separately from :class:`Alert` because a chain is a *path* between entities across
    telemetry layers, not a score on one entity. Collapsing it into the alert table would lose the
    thing that makes it interesting.
    """

    __tablename__ = "attack_chains"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    internal_entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    internal_entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    #: Ordered steps of the chain, each with its layer, timestamp and description.
    steps: Mapped[list[Any]] = mapped_column(JSONColumn, nullable=False, default=list)
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    run: Mapped[Run] = relationship(back_populates="chains")


class ConfigVersion(Base):
    """One saved version of the detection configuration.

    Versions rather than a single mutable row, for a reason specific to this system: an alert is
    only interpretable against the thresholds that produced it. If configuration were overwritten
    in place, an analyst reviewing last week's verdict would have no way to see what "unusual"
    meant at the time. Keeping every version, and pointing each run at the one it used, makes that
    recoverable.

    Exactly one version is ``is_active`` at a time; new runs snapshot it.
    """

    __tablename__ = "config_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: Monotonic, human-quotable — "we were on revision 4 when this fired".
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    #: SET NULL, not CASCADE: deleting an account must not erase the record of a
    #: configuration change they made. Same reasoning as the alert audit trail.
    created_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )

    def __repr__(self) -> str:
        """Identify the row in logs and test failures."""
        return f"<ConfigVersion r{self.revision}{' active' if self.is_active else ''}>"


class InvestigationStatus(enum.StrEnum):
    """Lifecycle of an analyst's case."""

    OPEN = "open"
    CONTAINED = "contained"
    CLOSED = "closed"


class Investigation(Base):
    """Several alerts and chains gathered into one case.

    The unit an analyst actually reasons about. A compromised credential produces a dozen alerts
    across windows and detectors, and triaging each in isolation loses the fact that they are one
    incident — which is precisely the relational blindness this project argues against, reproduced
    in the interface.
    """

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[InvestigationStatus] = mapped_column(
        _enum_column(InvestigationStatus, 16, "ck_investigations_status"),
        nullable=False,
        default=InvestigationStatus.OPEN,
    )
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    members: Mapped[list[InvestigationMember]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        """Identify the row in logs and test failures."""
        return f"<Investigation {self.title!r} {self.status.value}>"


class InvestigationMember(Base):
    """One alert or chain attached to a case.

    A single table with a nullable pair of foreign keys rather than two link tables, because the
    ordering an analyst gives their evidence spans both kinds and would otherwise have to be
    merged on read.
    """

    __tablename__ = "investigation_members"
    __table_args__ = (
        UniqueConstraint("investigation_id", "alert_id", name="uq_investigation_alert"),
        UniqueConstraint("investigation_id", "chain_id", name="uq_investigation_chain"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Exactly one of these is set. Enforced in the service layer so the violation is a
    #: readable API error rather than an integrity failure.
    alert_id: Mapped[str | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chain_id: Mapped[str | None] = mapped_column(
        ForeignKey("attack_chains.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    investigation: Mapped[Investigation] = relationship(back_populates="members")
    alert: Mapped[Alert | None] = relationship()
    chain: Mapped[AttackChainRecord | None] = relationship()
