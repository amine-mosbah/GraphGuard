"""AI-platform adapter — OpenTelemetry GenAI spans to canonical events (Core layer).

Role
----
Turns spans emitted under the OpenTelemetry **GenAI semantic conventions** (the ``gen_ai.*``
attribute namespace) into ``TraceEvent``s. This is the adapter that makes GraphGuard deployable
behind any OTLP collector without asking the platform to emit a bespoke format.

Inputs / outputs
----------------
Input: span records as mappings — either flat (``{"gen_ai.request.model": ...}``) or with the
attributes nested under an ``attributes`` key, which is how OTLP exporters usually serialise them.
Output: AI-layer ``TraceEvent``s.

What becomes what
-----------------
The mapping is deliberately literal, because the canonical schema was designed against these
conventions in the first place (``docs/SCHEMA.md`` §3):

===========================  =========================================================
Canonical field              Span source
===========================  =========================================================
``actor_id`` / ``actor_type``  the credential — API key id, falling back to end-user id
``target_id``                  ``gen_ai.request.model``
``action``                     derived from the outcome: call, error, or refusal
``outcome``                    error status, guardrail decision, or success
``source_ip``                  ``client.address`` (OTel) or a vendor alias
``session_id``                 ``gen_ai.conversation.id`` or ``session.id``
``attributes``                 token usage, latency, temperature, detector decision
===========================  =========================================================

Refusals are their own action, not a flavour of error
-----------------------------------------------------
A guardrail declining a request and the platform failing are different relational facts, and the
graph must be able to tell them apart: a run of refusals followed by a success is the signature of
an adaptive attacker probing a content detector, whereas a run of errors is an integration
problem. Collapsing both into "not a success" would erase exactly the pattern the companion
Phase 1 project exists to study (docs/DESIGN.md §10).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from graphguard.ingestion.base import Adapter, AdapterError, first_present
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent

#: Attribute keys that carry the calling credential, most specific first.
_ACTOR_KEYS = ("gen_ai.api_key.id", "api_key.id", "api_key", "enduser.id", "user.id")
#: Keys that carry the owning human, when the actor is a credential.
_USER_KEYS = ("enduser.id", "user.id", "user_id")
#: Keys that carry the client address (OTel, nginx, cloud vendors).
_CLIENT_IP_KEYS = ("client.address", "net.peer.ip", "source_ip", "remote_addr")
#: Keys that group a multi-turn interaction.
_SESSION_KEYS = ("gen_ai.conversation.id", "session.id", "session_id")
#: Guardrail verdicts that mean the request was declined rather than served.
_REFUSAL_VERDICTS = {"refuse", "refused", "block", "blocked", "jailbreak", "violation"}


class AITelemetryAdapter(Adapter):
    """OpenTelemetry GenAI spans to AI-layer canonical events."""

    name = "ai_telemetry"
    layer = Layer.AI

    def parse_record(self, record: Mapping[str, Any]) -> TraceEvent | None:
        """Convert one GenAI span."""
        attributes = _flatten(record)

        model = first_present(attributes, Attr.MODEL_NAME, "gen_ai.response.model", "model")
        if model is None:
            # Not every span in an OTLP stream is a model call — HTTP server spans, database
            # spans and internal spans share the pipeline. A span with no model is simply not
            # this adapter's concern, which is a skip, not a failure.
            return None

        actor = first_present(attributes, *_ACTOR_KEYS)
        if actor is None:
            raise AdapterError(
                self.name,
                "model span carries no calling identity "
                f"(looked for {', '.join(_ACTOR_KEYS)}); it cannot be attributed to an entity",
                record,
            )

        timestamp = _timestamp(record, attributes, self.name)
        action, outcome = _classify(attributes)

        canonical: dict[str, Any] = {}
        _copy_if_present(attributes, canonical, Attr.TOKENS_IN, "gen_ai.usage.prompt_tokens")
        _copy_if_present(attributes, canonical, Attr.TOKENS_OUT, "gen_ai.usage.completion_tokens")
        _copy_if_present(attributes, canonical, Attr.TEMPERATURE)
        _copy_if_present(attributes, canonical, Attr.LATENCY_MS, "duration_ms")
        _copy_if_present(attributes, canonical, Attr.ERROR_TYPE, "error.type", "exception.type")
        _copy_if_present(attributes, canonical, Attr.DETECTOR_LABEL, "guardrail.verdict")

        user = first_present(attributes, *_USER_KEYS)
        if user is not None and str(user) != str(actor):
            canonical[Attr.USER_ID] = str(user)
        canonical[Attr.MODEL_NAME] = str(model)

        return TraceEvent(
            timestamp=timestamp,
            layer=self.layer,
            session_id=_optional_str(first_present(attributes, *_SESSION_KEYS)),
            actor_id=str(actor),
            actor_type=ActorType.API_KEY,
            target_id=str(model),
            target_type=TargetType.MODEL,
            action=action,
            outcome=outcome,
            source_ip=_optional_str(first_present(attributes, *_CLIENT_IP_KEYS)),
            attributes=canonical,
            # Production telemetry is unlabelled; a label is only ever set by the simulator.
            label=_optional_str(record.get("label")),
        )


# --------------------------------------------------------------------------- helpers


def _flatten(record: Mapping[str, Any]) -> dict[str, Any]:
    """Merge nested ``attributes``/``resource`` maps into one flat lookup.

    OTLP exporters variously nest span attributes, resource attributes, or neither. Flattening
    once here means the field mapping below reads the same regardless of exporter.
    """
    flat: dict[str, Any] = {}
    for key in ("resource", "resourceAttributes", "attributes", "attrs"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            flat.update(nested)
    for key, value in record.items():
        if key not in {"resource", "resourceAttributes", "attributes", "attrs"}:
            flat[key] = value
    return flat


def _timestamp(record: Mapping[str, Any], attributes: Mapping[str, Any], adapter: str) -> datetime:
    """Extract the span's start time, in UTC.

    Accepts ISO-8601 strings and Unix nanoseconds (OTLP's native form). A span with no usable
    timestamp is rejected rather than defaulted to "now": placing an event at the wrong point in
    time corrupts every temporal comparison in the system, and does so invisibly.
    """
    raw = first_present(
        {**attributes, **record}, "timestamp", "startTimeUnixNano", "start_time", "time"
    )
    if raw is None:
        raise AdapterError(adapter, "span has no timestamp", record)

    if isinstance(raw, int | float):
        # OTLP reports nanoseconds; smaller magnitudes are seconds or milliseconds.
        value = float(raw)
        if value > 1e17:
            value /= 1e9
        elif value > 1e11:
            value /= 1e3
        return datetime.fromtimestamp(value, tz=UTC)

    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AdapterError(adapter, f"unparsable timestamp {raw!r}", record) from exc
    if parsed.tzinfo is None:
        raise AdapterError(
            adapter,
            f"timestamp {raw!r} has no timezone; the source must state one rather than let it "
            "be guessed",
            record,
        )
    return parsed.astimezone(UTC)


def _classify(attributes: Mapping[str, Any]) -> tuple[Action, Outcome]:
    """Decide the action and outcome from status and guardrail fields."""
    verdict = first_present(attributes, Attr.DETECTOR_LABEL, "guardrail.verdict", "guardrail")
    if verdict is not None and str(verdict).lower() in _REFUSAL_VERDICTS:
        return Action.REFUSAL, Outcome.REFUSE

    status = first_present(attributes, "status", "otel.status_code", "status_code")
    error_type = first_present(attributes, Attr.ERROR_TYPE, "error.type", "exception.type")
    if error_type is not None or (status is not None and str(status).upper() in {"ERROR", "5"}):
        return Action.ERROR, Outcome.ERROR

    return Action.CALL, Outcome.SUCCESS


def _copy_if_present(
    source: Mapping[str, Any], target: dict[str, Any], canonical_key: str, *aliases: str
) -> None:
    """Copy a value into the canonical attribute bag under its reserved key."""
    value = first_present(source, canonical_key, *aliases)
    if value is not None:
        target[canonical_key] = value


def _optional_str(value: Any) -> str | None:
    """Coerce to string, preserving ``None``."""
    return None if value is None else str(value)
