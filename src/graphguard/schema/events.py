"""Canonical telemetry schema — the seam every component agrees on.

Role
----
Defines :class:`TraceEvent`, the single normalised record type that flows through GraphGuard.
Ingestion adapters translate raw source formats *into* this type; **nothing downstream of an
adapter ever parses a raw log format** (docs/DESIGN.md §8.3). The graph builder, detectors,
correlation and evaluation layers all speak ``TraceEvent`` and nothing else.

This module is also the contract shared with the upstream *Phase 1* jailbreak project, whose
labelled session traces must drop straight into the graph builder with no converter
(docs/DESIGN.md §10). Changing a field here is a cross-project change: bump ``SCHEMA_VERSION`` and
record it in ``docs/DECISIONS.md``.

Inputs / outputs
----------------
Input: nothing (leaf module — pure definitions).
Output: :class:`TraceEvent` and its enums, consumed by every other package.

Model
-----
An event is one timestamped fact of the form::

    actor --[action]--> target   (with an outcome)

plus *context* that lets the graph builder materialise additional nodes and edges:
``session_id``, ``source_ip``, and typed keys inside ``attributes`` (see :class:`Attr`).
``source_ip`` is the field that lets the HTTP and AI layers share vertices, which is what makes
cross-layer attack-chain correlation possible at all (docs/DESIGN.md §7).
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumped whenever the field set or semantics change. Both GraphGuard and the Phase 1 project
# stamp their exports with this so a mismatched trace file fails loudly instead of silently
# producing a wrong graph.
SCHEMA_VERSION = "1.0"

BENIGN_LABEL = "benign"


class Layer(StrEnum):
    """Telemetry layer an event came from.

    Maps to the scope tiers in docs/DESIGN.md §6: ``ai`` is Core, ``http`` is Layer 2,
    ``network`` is Stretch.
    """

    AI = "ai"
    HTTP = "http"
    NETWORK = "network"


class ActorType(StrEnum):
    """Type of the entity that initiated the event — the "who"."""

    USER = "user"
    API_KEY = "api_key"
    IP = "ip"
    HOST = "host"
    ATTACKER_SESSION = "attacker_session"


class TargetType(StrEnum):
    """Type of the entity the event acted upon — the "what"."""

    MODEL = "model"
    ENDPOINT = "endpoint"
    HOST = "host"
    SERVICE = "service"
    DETECTOR = "detector"


class Action(StrEnum):
    """What happened. Becomes the *edge type* in the graph."""

    CALL = "call"
    ERROR = "error"
    REFUSAL = "refusal"
    LOGIN = "login"
    REQUEST = "request"
    FILE_ACCESS = "file_access"
    ROUND = "round"


class Outcome(StrEnum):
    """How it ended."""

    ALLOW = "allow"
    BLOCK = "block"
    REFUSE = "refuse"
    SUCCESS = "success"
    ERROR = "error"


class Attr:
    """Reserved keys inside :attr:`TraceEvent.attributes`.

    ``attributes`` is deliberately free-form so a new telemetry layer never forces a schema
    change. But keys the *graph builder* or *detectors* read must be discoverable rather than
    magic strings scattered across modules, so the well-known ones are named here.

    AI-layer names follow the OpenTelemetry GenAI semantic conventions (``gen_ai.*``) where an
    equivalent exists, so GraphGuard is deployable behind any OTLP collector (docs/DESIGN.md §12).
    """

    # --- identity context (graph builder materialises these as extra nodes/edges) ---
    USER_ID = "user_id"
    """Owning user of an ``api_key`` actor. Yields a ``user -[owns]-> api_key`` edge."""

    # --- AI layer (OTel gen_ai.*) ---
    TOKENS_IN = "gen_ai.usage.input_tokens"
    TOKENS_OUT = "gen_ai.usage.output_tokens"
    MODEL_NAME = "gen_ai.request.model"
    TEMPERATURE = "gen_ai.request.temperature"
    LATENCY_MS = "latency_ms"
    ERROR_TYPE = "error_type"

    # --- HTTP layer ---
    STATUS_CODE = "http.response.status_code"
    ENDPOINT_PATH = "url.path"
    METHOD = "http.request.method"
    USER_AGENT = "user_agent.original"

    # --- Phase 1 jailbreak traces ---
    PROMPT_ID = "prompt_id"
    ROUND_IDX = "round_idx"
    JUDGE_SCORE = "judge_score"
    DETECTOR_LABEL = "detector_label"
    ATTACK_METHOD = "attack_method"
    """``pair`` | ``tap`` — which adaptive attack produced the session."""


class TraceEvent(BaseModel):
    """One normalised telemetry event.

    Immutable by design: an event is an observed fact, and detectors must never be able to mutate
    the evidence they are scoring.

    Notes
    -----
    ``timestamp`` **must be timezone-aware**. Naive timestamps are rejected rather than assumed to
    be UTC: silently guessing a timezone on an event from a foreign adapter is precisely the bug
    that corrupts a temporal analysis, and every detector in this system reasons about time.

    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="Unique per event.")
    timestamp: datetime = Field(
        ..., description="Timezone-aware event time, normalised to UTC on construction."
    )
    layer: Layer = Field(..., description="Telemetry layer this event came from.")

    session_id: str | None = Field(
        default=None, description="Groups multi-round interactions (a conversation, a login flow)."
    )

    actor_id: str = Field(..., description='The "who" — user / api_key / ip / host.')
    actor_type: ActorType
    target_id: str = Field(..., description='The "what" — model / endpoint / host / service.')
    target_type: TargetType

    action: Action = Field(..., description="Becomes the edge type in the graph.")
    outcome: Outcome

    source_ip: str | None = Field(
        default=None,
        description="Cross-layer join key: the vertex HTTP and AI layers share (DESIGN.md §7).",
    )

    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Free-form payload; well-known keys listed in `Attr`."
    )

    label: str | None = Field(
        default=None,
        description=(
            "Ground truth for evaluation: 'benign' or an attack-scenario name. "
            "**None means unlabelled**, i.e. production telemetry."
        ),
    )

    schema_version: str = Field(
        default=SCHEMA_VERSION, description="Schema contract version (see module docstring)."
    )

    # ------------------------------------------------------------------ validators

    @field_validator("timestamp")
    @classmethod
    def _require_aware_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError(
                "timestamp must be timezone-aware; adapters must state the timezone explicitly "
                "rather than let it be guessed"
            )
        return v.astimezone(UTC)

    @field_validator("actor_id", "target_id")
    @classmethod
    def _require_non_empty_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("entity ids must be non-empty")
        return v

    @field_validator("source_ip")
    @classmethod
    def _require_valid_ip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        try:
            # Normalise so that e.g. '10.0.0.1' and '10.00.0.1' become the same graph vertex.
            return str(ipaddress.ip_address(v))
        except ValueError as exc:
            raise ValueError(
                f"source_ip must be a valid IPv4/IPv6 address, got {v!r}; it is the cross-layer "
                "join key, so a malformed value would silently break correlation"
            ) from exc

    @field_validator("label")
    @classmethod
    def _normalise_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("label must be None (unlabelled) or a non-empty scenario name")
        return v

    # ------------------------------------------------------------------ derived views

    @property
    def actor_key(self) -> tuple[str, str]:
        """Graph identity of the actor.

        Node identity is ``(type, id)``, never the bare id: a user called ``"gpt-4"`` and a model
        called ``"gpt-4"`` must not collapse into one vertex.
        """
        return (self.actor_type.value, self.actor_id)

    @property
    def target_key(self) -> tuple[str, str]:
        """Graph identity of the target. See :attr:`actor_key`."""
        return (self.target_type.value, self.target_id)

    @property
    def is_attack(self) -> bool:
        """True when this event is labelled as belonging to an attack scenario.

        Unlabelled (production) events are **not** attacks for evaluation purposes; they are
        simply unknown, and callers that need that distinction should test ``label is None``.
        """
        return self.label is not None and self.label != BENIGN_LABEL

    def __str__(self) -> str:
        """Render the event as a one-line human-readable fact, for logs and debugging."""
        ts = self.timestamp.isoformat(timespec="seconds")
        return (
            f"{ts} [{self.layer.value}] {self.actor_type.value}:{self.actor_id} "
            f"--{self.action.value}--> {self.target_type.value}:{self.target_id} "
            f"({self.outcome.value})"
        )
