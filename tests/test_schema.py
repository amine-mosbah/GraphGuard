"""Contract tests for the canonical TraceEvent schema.

This schema is shared with the Phase 1 jailbreak project, so these tests guard a cross-project
interface, not just internal behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from graphguard.schema import (
    SCHEMA_VERSION,
    Action,
    ActorType,
    Attr,
    Layer,
    Outcome,
    SchemaVersionMismatchError,
    TargetType,
    TraceEvent,
    TraceParseError,
    read_jsonl_list,
    write_jsonl,
)

UTC_NOW = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


def make_event(**overrides: object) -> TraceEvent:
    """Build a valid AI-layer event, overriding fields as needed."""
    base: dict[str, object] = {
        "timestamp": UTC_NOW,
        "layer": Layer.AI,
        "session_id": "sess-1",
        "actor_id": "key-042",
        "actor_type": ActorType.API_KEY,
        "target_id": "gpt-4o",
        "target_type": TargetType.MODEL,
        "action": Action.CALL,
        "outcome": Outcome.SUCCESS,
        "source_ip": "10.0.0.7",
        "attributes": {Attr.TOKENS_IN: 120, Attr.TOKENS_OUT: 380},
        "label": "benign",
    }
    base.update(overrides)
    return TraceEvent(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- basics


def test_event_id_is_unique_by_default() -> None:
    assert make_event().event_id != make_event().event_id


def test_str_is_human_readable() -> None:
    rendered = str(make_event())
    assert "api_key:key-042" in rendered
    assert "--call-->" in rendered
    assert "model:gpt-4o" in rendered


def test_event_is_immutable() -> None:
    """Events are observed facts; a detector must not be able to edit its own evidence."""
    event = make_event()
    with pytest.raises(ValidationError):
        event.actor_id = "someone-else"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    """extra='forbid' catches adapter typos instead of silently dropping data."""
    with pytest.raises(ValidationError):
        make_event(actorid="typo")


# --------------------------------------------------------------------------- timestamps


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_event(timestamp=datetime(2026, 3, 2, 14, 30))


def test_non_utc_timestamp_is_normalised_to_utc() -> None:
    tunis = timezone(timedelta(hours=1))
    event = make_event(timestamp=datetime(2026, 3, 2, 15, 30, tzinfo=tunis))
    assert event.timestamp == UTC_NOW
    assert event.timestamp.tzinfo == UTC


# --------------------------------------------------------------------------- identity


def test_node_keys_are_type_qualified() -> None:
    """A user named 'gpt-4o' and a model named 'gpt-4o' must be different vertices."""
    event = make_event()
    assert event.actor_key == ("api_key", "key-042")
    assert event.target_key == ("model", "gpt-4o")
    assert event.actor_key != event.target_key

    collision = make_event(actor_id="gpt-4o", actor_type=ActorType.USER)
    assert collision.actor_key != collision.target_key


@pytest.mark.parametrize("bad_id", ["", "   "])
def test_empty_ids_are_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        make_event(actor_id=bad_id)


def test_ids_are_stripped() -> None:
    assert make_event(actor_id="  key-042  ").actor_id == "key-042"


# --------------------------------------------------------------------------- source_ip


def test_invalid_source_ip_is_rejected() -> None:
    """source_ip is the cross-layer join key; a malformed value breaks §8 correlation silently."""
    with pytest.raises(ValidationError, match="valid IPv4/IPv6"):
        make_event(source_ip="not-an-ip")


def test_source_ip_is_normalised() -> None:
    assert make_event(source_ip="::1").source_ip == "::1"
    assert make_event(source_ip=" 10.0.0.7 ").source_ip == "10.0.0.7"


def test_blank_source_ip_becomes_none() -> None:
    assert make_event(source_ip="").source_ip is None


# --------------------------------------------------------------------------- labels


def test_none_label_means_unlabelled_not_attack() -> None:
    event = make_event(label=None)
    assert event.label is None
    assert event.is_attack is False


def test_benign_label_is_not_an_attack() -> None:
    assert make_event(label="benign").is_attack is False


def test_scenario_label_is_an_attack() -> None:
    assert make_event(label="stolen_key").is_attack is True


def test_blank_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_event(label="   ")


# --------------------------------------------------------------------------- round-trip


def test_jsonl_round_trip_preserves_events(tmp_path) -> None:
    events = [
        make_event(),
        make_event(action=Action.ERROR, outcome=Outcome.ERROR, label="stolen_key"),
        make_event(layer=Layer.HTTP, target_type=TargetType.ENDPOINT, target_id="/v1/auth"),
    ]
    path = tmp_path / "trace.jsonl"

    assert write_jsonl(events, path) == 3
    assert read_jsonl_list(path) == events


def test_jsonl_round_trip_handles_gzip(tmp_path) -> None:
    events = [make_event()]
    path = tmp_path / "trace.jsonl.gz"
    write_jsonl(events, path)
    assert read_jsonl_list(path) == events


def test_write_creates_missing_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "deeper" / "trace.jsonl"
    write_jsonl([make_event()], path)
    assert path.exists()


def test_blank_lines_are_skipped(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    write_jsonl([make_event()], path)
    path.write_text(path.read_text() + "\n\n")
    assert len(read_jsonl_list(path)) == 1


# --------------------------------------------------------------------------- failure modes


def test_malformed_line_reports_its_line_number(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    write_jsonl([make_event(), make_event()], path)
    path.write_text(path.read_text() + "{not json\n")

    with pytest.raises(TraceParseError) as exc_info:
        read_jsonl_list(path)
    assert exc_info.value.line_no == 3


def test_schema_version_mismatch_fails_loudly(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    write_jsonl([make_event()], path)
    path.write_text(path.read_text().replace(f'"{SCHEMA_VERSION}"', '"0.1"'))

    with pytest.raises(SchemaVersionMismatchError):
        read_jsonl_list(path)

    # ...but an archived run can still be read deliberately.
    assert len(read_jsonl_list(path, strict_version=False)) == 1
