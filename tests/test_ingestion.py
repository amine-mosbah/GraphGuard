"""Tests for the ingestion adapters.

These guard the seam that makes the rest of the system format-agnostic: an adapter must either
produce a well-typed canonical event or fail loudly, and must never quietly drop a record that
should have become one.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from graphguard.ingestion import AdapterError, build_adapter, normalise_path
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType


def ai_span(**overrides: object) -> dict[str, object]:
    attributes: dict[str, object] = {
        "gen_ai.request.model": "gpt-4o",
        "api_key.id": "key-007",
        "enduser.id": "u-7",
        "client.address": "10.0.0.5",
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 300,
        "gen_ai.conversation.id": "sess-1",
    }
    attributes.update(overrides)
    return {"timestamp": "2026-03-02T09:00:00Z", "attributes": attributes}


def http_log(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "time": "2026-03-02T02:14:09Z",
        "remote_addr": "203.0.113.9",
        "request_uri": "/v1/models",
        "status": 200,
        "method": "GET",
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------- registry


def test_unknown_adapter_is_rejected() -> None:
    """A config typo must not silently ingest nothing."""
    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter("carrier_pigeon")


# --------------------------------------------------------------------------- AI adapter


def test_ai_adapter_maps_a_genai_span_to_a_canonical_event() -> None:
    event = next(iter(build_adapter("ai_telemetry").parse([ai_span()])))
    assert event.layer is Layer.AI
    assert event.actor_type is ActorType.API_KEY
    assert event.actor_id == "key-007"
    assert event.target_type is TargetType.MODEL
    assert event.target_id == "gpt-4o"
    assert event.action is Action.CALL
    assert event.outcome is Outcome.SUCCESS
    assert event.source_ip == "10.0.0.5"
    assert event.session_id == "sess-1"
    assert event.attributes[Attr.TOKENS_IN] == 120
    assert event.attributes[Attr.USER_ID] == "u-7"


def test_ai_adapter_accepts_flat_and_nested_attribute_shapes() -> None:
    """OTLP exporters variously nest attributes; the mapping must not care."""
    nested = ai_span()
    flat = {"timestamp": nested["timestamp"], **nested["attributes"]}  # type: ignore[dict-item]
    adapter = build_adapter("ai_telemetry")
    assert str(next(iter(adapter.parse([nested])))) == str(next(iter(adapter.parse([flat]))))


def test_ai_adapter_parses_otlp_nanosecond_timestamps() -> None:
    span = {"startTimeUnixNano": 1772442000000000000, "attributes": ai_span()["attributes"]}
    event = next(iter(build_adapter("ai_telemetry").parse([span])))
    assert event.timestamp.tzinfo == UTC
    assert event.timestamp.year == 2026


def test_ai_adapter_rejects_a_naive_timestamp() -> None:
    """Guessing a timezone corrupts every temporal comparison, invisibly."""
    with pytest.raises(AdapterError, match="no timezone"):
        list(
            build_adapter("ai_telemetry").parse([{**ai_span(), "timestamp": "2026-03-02T09:00:00"}])
        )


def test_ai_adapter_skips_spans_that_are_not_model_calls() -> None:
    """An OTLP stream carries HTTP and database spans too; those are not a failure."""
    adapter = build_adapter("ai_telemetry")
    events = list(
        adapter.parse(
            [{"timestamp": "2026-03-02T09:00:00Z", "attributes": {"db.system": "postgres"}}]
        )
    )
    assert events == []
    assert adapter.report.skipped == 0  # skipped as uninteresting, not as malformed


def test_ai_adapter_rejects_a_model_span_with_no_identity() -> None:
    span = ai_span()
    for key in ("api_key.id", "enduser.id"):
        span["attributes"].pop(key)  # type: ignore[union-attr]
    with pytest.raises(AdapterError, match="no calling identity"):
        list(build_adapter("ai_telemetry").parse([span]))


def test_ai_adapter_distinguishes_refusals_from_errors() -> None:
    """A guardrail declining and the platform failing are different relational facts."""
    refused = next(
        iter(build_adapter("ai_telemetry").parse([ai_span(**{"guardrail.verdict": "blocked"})]))
    )
    assert refused.action is Action.REFUSAL and refused.outcome is Outcome.REFUSE

    failed = next(iter(build_adapter("ai_telemetry").parse([ai_span(**{"error.type": "timeout"})])))
    assert failed.action is Action.ERROR and failed.outcome is Outcome.ERROR


# --------------------------------------------------------------------------- HTTP adapter


def test_http_adapter_maps_an_access_log_to_a_canonical_event() -> None:
    event = next(iter(build_adapter("http_gateway").parse([http_log()])))
    assert event.layer is Layer.HTTP
    assert event.actor_type is ActorType.IP
    assert event.actor_id == "203.0.113.9"
    assert event.target_type is TargetType.ENDPOINT
    assert event.action is Action.REQUEST
    # The actor and the source address are the same value: at the gateway, before
    # authentication, the address is the only identity there is.
    assert event.source_ip == event.actor_id


def test_http_adapter_classifies_auth_paths_as_logins() -> None:
    event = next(
        iter(
            build_adapter("http_gateway").parse(
                [http_log(request_uri="/v1/auth/login", status=401)]
            )
        )
    )
    assert event.action is Action.LOGIN
    assert event.outcome is Outcome.BLOCK


def test_rejected_credentials_are_blocks_not_errors() -> None:
    """Merging 401 with 500 would make a brute force indistinguishable from an outage."""
    adapter = build_adapter("http_gateway")
    blocked = next(iter(adapter.parse([http_log(status=401)])))
    failed = next(iter(adapter.parse([http_log(status=503)])))
    assert blocked.outcome is Outcome.BLOCK
    assert failed.outcome is Outcome.ERROR


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/users/8f3a91bc/keys", "/v1/users/{id}/keys"),
        ("/v1/projects/42", "/v1/projects/{id}"),
        ("/v1/models?limit=10", "/v1/models"),
        ("/v1/x/550e8400-e29b-41d4-a716-446655440000", "/v1/x/{id}"),
    ],
)
def test_identifier_segments_collapse_into_one_endpoint(path: str, expected: str) -> None:
    """Otherwise enumeration creates thousands of singleton vertices instead of one hot endpoint."""
    assert normalise_path(path) == expected


@pytest.mark.parametrize("path", ["/healthz", "/metrics", "/favicon.ico", "/static/app.js"])
def test_noise_paths_never_enter_the_graph(path: str) -> None:
    assert normalise_path(path) is None


def test_http_adapter_rejects_a_record_with_no_client_address() -> None:
    """Without the address there is no join key, and the cross-layer path is impossible."""
    record = http_log()
    record.pop("remote_addr")
    with pytest.raises(AdapterError, match="no client address"):
        list(build_adapter("http_gateway").parse([record]))


# --------------------------------------------------------------------------- error policy


def test_strict_mode_raises_on_a_malformed_record() -> None:
    with pytest.raises(AdapterError):
        list(build_adapter("http_gateway", strict=True).parse([{"status": 200}]))


def test_lenient_mode_skips_and_reports_rather_than_swallowing() -> None:
    """A pipeline that silently drops records builds a graph missing edges nobody knows about."""
    adapter = build_adapter("http_gateway", strict=False)
    events = list(adapter.parse([http_log(), {"status": 200}, http_log()]))

    assert len(events) == 2
    assert adapter.report.skipped == 1
    assert adapter.report.reasons, "a skip must be explained, not merely counted"
    assert "1 skipped" in adapter.report.summary()


def test_jsonl_ingestion_reports_the_offending_line(tmp_path) -> None:
    path = tmp_path / "access.jsonl"
    path.write_text('{"bad json\n', encoding="utf-8")
    with pytest.raises(AdapterError, match="invalid JSON"):
        list(build_adapter("http_gateway").parse_jsonl(path))


def test_adapters_emit_events_the_graph_builder_accepts() -> None:
    """The end-to-end point of the seam: adapter output feeds the builder with no translation."""
    from graphguard.config import GraphConfig
    from graphguard.graph import GraphBuilder

    events = list(build_adapter("ai_telemetry").parse([ai_span()]))
    events += list(build_adapter("http_gateway").parse([http_log(remote_addr="10.0.0.5")]))
    events.sort(key=lambda e: e.timestamp)

    snapshots = GraphBuilder(GraphConfig()).build_snapshots(events)
    assert snapshots
    node_types = set(snapshots[0].data.node_types)
    assert {"api_key", "model", "ip"} <= node_types
    assert "endpoint" in {t for _, _, t in snapshots[0].data.edge_types} | node_types


def test_both_layers_share_the_ip_vertex(tmp_path) -> None:
    """The single fact that makes cross-layer correlation possible."""
    del tmp_path
    ai = next(
        iter(build_adapter("ai_telemetry").parse([ai_span(**{"client.address": "203.0.113.9"})]))
    )
    http = next(iter(build_adapter("http_gateway").parse([http_log(remote_addr="203.0.113.9")])))
    assert ai.source_ip == http.actor_id
    assert http.actor_key == ("ip", "203.0.113.9")
