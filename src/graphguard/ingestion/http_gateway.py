"""HTTP gateway adapter — access logs to canonical events (Layer 2).

Role
----
Turns API-gateway / reverse-proxy access logs into ``TraceEvent``s. This is the second telemetry
layer, and its real purpose is structural: it is what makes the cross-layer attack chain of
docs/DESIGN.md §7 expressible at all.

Inputs / outputs
----------------
Input: access-log records as mappings (nginx JSON, an ALB/CloudFront export, or any gateway that
emits one object per request).
Output: HTTP-layer ``TraceEvent``s.

Why this adapter is the whole point of the layer
------------------------------------------------
An HTTP event's actor is an **IP address**, and an AI-layer event's ``source_ip`` is the same
value. Because node identity is ``(type, id)``, both produce the *same graph vertex*. That single
shared vertex is what turns two unrelated incidents — "someone brute-forced the login endpoint"
and "a valid key started behaving oddly" — into one traversable path:

``ip --[login]--> endpoint``  and  ``api_key --[used_from]--> ip``

Nothing else in the pipeline needed changing to gain this. The graph builder derives relations
from ``(actor_type, action, target_type)``, all three of which this adapter simply fills in
correctly, so the new edges appear and every existing detector runs over them unchanged. That is
the extensibility claim of the architecture, demonstrated rather than asserted.

Endpoint normalisation
----------------------
Paths are normalised so that ``/v1/users/8f3a/keys`` and ``/v1/users/22b1/keys`` become one
``endpoint`` vertex. Without this an attacker enumerating identifiers would create a fresh vertex
per request, and the graph would record thousands of one-off endpoints instead of one endpoint
being hammered — turning the clearest possible signal into noise.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from graphguard.ingestion.base import Adapter, AdapterError, first_present
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent

_PATH_KEYS = ("url.path", "request_uri", "path", "uri", "cs-uri-stem")
_METHOD_KEYS = ("http.request.method", "method", "request_method", "cs-method")
_STATUS_KEYS = ("http.response.status_code", "status", "status_code", "sc-status")
_CLIENT_IP_KEYS = ("client.address", "remote_addr", "source_ip", "c-ip", "clientIp")
_AGENT_KEYS = ("user_agent.original", "http.user_agent", "user_agent", "cs(User-Agent)")
_USER_KEYS = ("enduser.id", "user.id", "user_id", "auth_user")

#: Path segments that identify an authentication attempt rather than ordinary API traffic.
_AUTH_MARKERS = ("/login", "/auth", "/token", "/signin", "/session", "/oauth")

#: Paths whose traffic carries no security signal and would only add noise to the graph.
_IGNORED_PREFIXES = ("/health", "/healthz", "/ping", "/metrics", "/favicon.ico", "/static/")

#: Segments replaced when normalising a path into an endpoint identity.
_HEX_SEGMENT = re.compile(r"^[0-9a-fA-F]{8,}$")
_UUID_SEGMENT = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-", re.IGNORECASE)
_NUMERIC_SEGMENT = re.compile(r"^\d+$")


class HTTPGatewayAdapter(Adapter):
    """API-gateway access logs to HTTP-layer canonical events."""

    name = "http_gateway"
    layer = Layer.HTTP

    def parse_record(self, record: Mapping[str, Any]) -> TraceEvent | None:
        """Convert one access-log line."""
        path = first_present(record, *_PATH_KEYS)
        if path is None:
            raise AdapterError(self.name, "access-log record has no request path", record)

        endpoint = normalise_path(str(path))
        if endpoint is None:
            return None  # health check or static asset: legitimately not of interest

        client = first_present(record, *_CLIENT_IP_KEYS)
        if client is None:
            raise AdapterError(
                self.name,
                "access-log record has no client address; without it the event cannot be "
                "attributed to an entity, and the cross-layer join is impossible",
                record,
            )

        status = _status(record, self.name)
        action = Action.LOGIN if _is_auth_path(endpoint) else Action.REQUEST
        outcome = _outcome(status)

        attributes: dict[str, Any] = {
            Attr.ENDPOINT_PATH: endpoint,
            Attr.STATUS_CODE: status,
        }
        method = first_present(record, *_METHOD_KEYS)
        if method is not None:
            attributes[Attr.METHOD] = str(method).upper()
        agent = first_present(record, *_AGENT_KEYS)
        if agent is not None:
            attributes[Attr.USER_AGENT] = str(agent)
        user = first_present(record, *_USER_KEYS)
        if user is not None:
            attributes[Attr.USER_ID] = str(user)

        return TraceEvent(
            timestamp=_timestamp(record, self.name),
            layer=self.layer,
            session_id=None,
            # The actor is the address itself: at the gateway, before authentication, the address
            # is the only identity there is — which is exactly why it is the join key.
            actor_id=str(client),
            actor_type=ActorType.IP,
            target_id=endpoint,
            target_type=TargetType.ENDPOINT,
            action=action,
            outcome=outcome,
            source_ip=str(client),
            attributes=attributes,
            label=_optional_str(record.get("label")),
        )


# --------------------------------------------------------------------------- helpers


def normalise_path(path: str) -> str | None:
    """Collapse a request path into a stable endpoint identity.

    Returns ``None`` for paths that should not enter the graph at all. Identifier-looking
    segments — numeric, hex, UUID — are replaced by a placeholder, so that enumeration of
    thousands of ids produces one hammered endpoint vertex rather than thousands of singletons.
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    lowered = path.lower()
    if any(lowered.startswith(prefix) for prefix in _IGNORED_PREFIXES):
        return None

    segments = []
    for segment in path.split("/"):
        if not segment:
            continue
        if (
            _NUMERIC_SEGMENT.match(segment)
            or _HEX_SEGMENT.match(segment)
            or _UUID_SEGMENT.match(segment)
        ):
            segments.append("{id}")
        else:
            segments.append(segment)
    return "/" + "/".join(segments) if segments else "/"


def _is_auth_path(endpoint: str) -> bool:
    """Whether this endpoint is an authentication surface."""
    lowered = endpoint.lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


def _status(record: Mapping[str, Any], adapter: str) -> int:
    """Extract the HTTP status code."""
    raw = first_present(record, *_STATUS_KEYS)
    if raw is None:
        raise AdapterError(adapter, "access-log record has no status code", record)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise AdapterError(adapter, f"unparsable status code {raw!r}", record) from exc


def _outcome(status: int) -> Outcome:
    """Map an HTTP status code onto a canonical outcome.

    401 and 403 are ``block`` rather than ``error``: a rejected credential is a security-relevant
    decision by the platform, whereas a 500 is the platform failing. Merging them would make a
    brute-force attempt indistinguishable from an outage in the graph.
    """
    if status in (401, 403, 407, 429):
        return Outcome.BLOCK
    if status >= 500:
        return Outcome.ERROR
    if status >= 400:
        return Outcome.ERROR
    return Outcome.SUCCESS


def _timestamp(record: Mapping[str, Any], adapter: str) -> datetime:
    """Extract the request time, in UTC, refusing to guess a timezone."""
    raw = first_present(record, "timestamp", "@timestamp", "time", "time_local", "date")
    if raw is None:
        raise AdapterError(adapter, "access-log record has no timestamp", record)

    if isinstance(raw, int | float):
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
            f"timestamp {raw!r} has no timezone; a gateway log without an offset cannot be "
            "aligned with AI-layer events, which is the entire purpose of ingesting it",
            record,
        )
    return parsed.astimezone(UTC)


def _optional_str(value: Any) -> str | None:
    """Coerce to string, preserving ``None``."""
    return None if value is None else str(value)
