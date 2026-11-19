"""Tests for the graph schema registry, time-windowing and the graph builder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graphguard.config import GraphConfig, SnapshotConfig
from graphguard.graph import GraphBuilder, NodeType, make_windows, parse_offset, split_into_windows
from graphguard.graph.model import (
    CORE_RELATIONS,
    FEATURE_INDEX,
    FEATURE_NAMES,
    relation_name,
    validate_registry,
)
from graphguard.graph.snapshots import iter_windows
from graphguard.schema import Action, ActorType, Attr, Layer, Outcome, TargetType, TraceEvent

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def event(
    *,
    minutes: int = 0,
    hours: int = 0,
    days: int = 0,
    actor: str = "key-1",
    target: str = "gpt-4o",
    ip: str | None = "10.0.0.1",
    user: str | None = "u-1",
    action: Action = Action.CALL,
    outcome: Outcome = Outcome.SUCCESS,
    tokens_in: int = 100,
    tokens_out: int = 200,
) -> TraceEvent:
    attributes: dict[str, object] = {Attr.TOKENS_IN: tokens_in, Attr.TOKENS_OUT: tokens_out}
    if user is not None:
        attributes[Attr.USER_ID] = user
    return TraceEvent(
        timestamp=T0 + timedelta(days=days, hours=hours, minutes=minutes),
        layer=Layer.AI,
        session_id=f"s-{days}",
        actor_id=actor,
        actor_type=ActorType.API_KEY,
        target_id=target,
        target_type=TargetType.MODEL,
        action=action,
        outcome=outcome,
        source_ip=ip,
        attributes=attributes,
        label="benign",
    )


def graph_config(**overrides: object) -> GraphConfig:
    base: dict[str, object] = {
        "snapshot": SnapshotConfig(window="1d", stride="1d"),
        "aggregate_parallel_edges": True,
        "min_node_events": 1,
        "working_hours": (8, 18),
    }
    base.update(overrides)
    return GraphConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- registry


def test_registry_is_self_consistent() -> None:
    validate_registry()


def test_every_feature_has_a_human_readable_description() -> None:
    """Explanations quote descriptions verbatim, so a missing one is an unexplainable score."""
    from graphguard.graph import FEATURE_DESCRIPTIONS

    assert set(FEATURE_NAMES) == set(FEATURE_DESCRIPTIONS)
    assert all(FEATURE_DESCRIPTIONS[name].strip() for name in FEATURE_NAMES)


def test_relation_rendering_is_readable() -> None:
    assert relation_name((NodeType.API_KEY, "call", NodeType.MODEL)) == "api_key-[call]->model"


def test_core_relations_include_the_cross_layer_join() -> None:
    """The ip node is the only vertex telemetry layers share; without it §8 correlation is dead."""
    assert any(rel[2] == NodeType.IP for rel in CORE_RELATIONS)


# --------------------------------------------------------------------------- windowing


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1d", timedelta(days=1)), ("6h", timedelta(hours=6)), ("30m", timedelta(minutes=30))],
)
def test_parse_offset_accepts_pandas_style_offsets(text: str, expected: timedelta) -> None:
    assert parse_offset(text) == expected


@pytest.mark.parametrize("text", ["", "d", "1y", "-1d", "1 day", "0d"])
def test_parse_offset_rejects_malformed_or_zero_offsets(text: str) -> None:
    with pytest.raises(ValueError):
        parse_offset(text)


def test_tumbling_windows_do_not_double_count_boundary_events() -> None:
    """Windows are half-open, so a boundary event belongs to exactly one tumbling window."""
    events = [event(days=d) for d in range(3)]
    windows = split_into_windows(events, SnapshotConfig(window="1d", stride="1d"))
    counted = sum(len(chunk) for _, chunk in windows)
    assert counted == len(events)


def test_sliding_windows_overlap_by_design() -> None:
    events = [event(hours=h) for h in range(0, 24, 3)]
    windows = split_into_windows(events, SnapshotConfig(window="12h", stride="6h"))
    counted = sum(len(chunk) for _, chunk in windows)
    assert counted > len(events)


def test_windows_are_anchored_on_the_first_event() -> None:
    events = [event(days=d) for d in range(3)]
    windows = make_windows(events, SnapshotConfig(window="1d", stride="1d"))
    assert windows[0].start == events[0].timestamp


def test_trailing_events_are_never_dropped() -> None:
    events = [event(days=0), event(days=0, hours=5), event(days=2, hours=7)]
    windows = split_into_windows(events, SnapshotConfig(window="1d", stride="1d"))
    assert sum(len(chunk) for _, chunk in windows) == len(events)


def test_iter_windows_rejects_non_positive_spans() -> None:
    with pytest.raises(ValueError, match="positive"):
        list(iter_windows(T0, T0 + timedelta(days=1), timedelta(0), timedelta(days=1)))


def test_empty_event_stream_yields_no_windows() -> None:
    assert make_windows([], SnapshotConfig()) == []


# --------------------------------------------------------------------------- construction


def test_builder_materialises_context_nodes_and_edges() -> None:
    """actor/target come from the event; ip and user are derived from context."""
    snapshots = GraphBuilder(graph_config()).build_snapshots([event()])
    assert len(snapshots) == 1
    data = snapshots[0].data

    assert set(data.node_types) == {"api_key", "model", "ip", "user"}
    assert ("api_key", "call", "model") in data.edge_types
    assert ("api_key", "used_from", "ip") in data.edge_types
    assert ("user", "owns", "api_key") in data.edge_types


def test_events_without_context_produce_no_context_nodes() -> None:
    snapshots = GraphBuilder(graph_config()).build_snapshots([event(ip=None, user=None)])
    assert set(snapshots[0].data.node_types) == {"api_key", "model"}


def test_actions_become_distinct_edge_types() -> None:
    events = [
        event(minutes=0),
        event(minutes=1, action=Action.ERROR, outcome=Outcome.ERROR),
        event(minutes=2, action=Action.REFUSAL, outcome=Outcome.REFUSE),
    ]
    data = GraphBuilder(graph_config()).build_snapshots(events)[0].data
    assert ("api_key", "call", "model") in data.edge_types
    assert ("api_key", "error", "model") in data.edge_types
    assert ("api_key", "refusal", "model") in data.edge_types


def test_parallel_edges_are_aggregated_with_weights() -> None:
    events = [event(minutes=m) for m in range(5)]
    data = GraphBuilder(graph_config()).build_snapshots(events)[0].data
    relation = ("api_key", "call", "model")
    assert data[relation].edge_index.size(1) == 1
    assert float(data[relation].edge_weight[0]) == 5.0


def test_min_node_events_filters_the_noise_floor() -> None:
    events = [event(minutes=m) for m in range(3)] + [event(minutes=10, actor="key-rare")]
    snapshots = GraphBuilder(graph_config(min_node_events=2)).build_snapshots(events)
    assert "key-rare" not in snapshots[0].node_ids["api_key"]
    assert "key-1" in snapshots[0].node_ids["api_key"]


def test_node_ordering_matches_the_homogeneous_projection() -> None:
    """Score arrays are aligned by position, so a mismatch here misattributes every alert."""
    events = [event(minutes=m, actor=f"key-{m}") for m in range(4)]
    snapshot = GraphBuilder(graph_config()).build_snapshots(events)[0]

    homogeneous, keys = snapshot.to_homogeneous()
    assert homogeneous.num_nodes == len(keys)
    assert keys == snapshot.all_node_keys()
    assert len(set(keys)) == len(keys)


def test_feature_matrix_width_matches_the_registry() -> None:
    snapshot = GraphBuilder(graph_config()).build_snapshots([event()])[0]
    for node_type in snapshot.data.node_types:
        assert snapshot.raw_features[node_type].shape[1] == len(FEATURE_NAMES)
        assert snapshot.data[node_type].x.shape[1] == len(FEATURE_NAMES)


# --------------------------------------------------------------------------- features


def test_night_ratio_uses_configured_working_hours() -> None:
    day_events = [event(hours=0, minutes=m) for m in range(3)]  # 09:00, inside 8-18
    night_events = [event(hours=14, minutes=m) for m in range(3)]  # 23:00, outside
    snapshot = GraphBuilder(graph_config()).build_snapshots(day_events + night_events)[0]

    row = snapshot.node_ids["api_key"].index("key-1")
    night_ratio = snapshot.raw_features["api_key"][row, FEATURE_INDEX["night_ratio"]]
    assert night_ratio == pytest.approx(0.5)


def test_error_and_refusal_ratios_are_tracked_separately() -> None:
    events = [
        event(minutes=0),
        event(minutes=1),
        event(minutes=2, action=Action.ERROR, outcome=Outcome.ERROR),
        event(minutes=3, action=Action.REFUSAL, outcome=Outcome.REFUSE),
    ]
    snapshot = GraphBuilder(graph_config()).build_snapshots(events)[0]
    row = snapshot.node_ids["api_key"].index("key-1")
    features = snapshot.raw_features["api_key"][row]
    assert features[FEATURE_INDEX["error_ratio"]] == pytest.approx(0.25)
    assert features[FEATURE_INDEX["refusal_ratio"]] == pytest.approx(0.25)


def test_regular_schedule_has_lower_hour_entropy_than_a_spread_one() -> None:
    """This is what separates regular off-hours automation from an attacker working at night."""
    regular = [event(days=0, hours=0, minutes=m) for m in range(20)]
    spread = [event(days=0, hours=h, minutes=0) for h in range(20)]

    def entropy_of(events: list[TraceEvent]) -> float:
        snapshot = GraphBuilder(graph_config()).build_snapshots(events)[0]
        return snapshot.raw_feature("api_key", "key-1", "hour_entropy")

    assert entropy_of(regular) < entropy_of(spread)


# --------------------------------------------------------------------------- history


def test_history_features_are_neutral_in_the_first_window() -> None:
    """With no past, novelty is 0 and volume_ratio is 1.0 — "exactly as usual"."""
    snapshot = GraphBuilder(graph_config()).build_snapshots([event()])[0]
    assert snapshot.raw_feature("api_key", "key-1", "neighbour_novelty") == 0.0
    assert snapshot.raw_feature("api_key", "key-1", "volume_ratio") == 1.0


def test_novelty_fires_when_a_node_reaches_a_new_neighbour() -> None:
    events = [
        event(days=0, target="gpt-4o"),
        event(days=1, target="gpt-4o"),
        event(days=2, target="claude-sonnet-4"),  # never seen before for this key
    ]
    snapshots = GraphBuilder(graph_config()).build_snapshots(events)

    assert snapshots[1].raw_feature("api_key", "key-1", "neighbour_novelty") == 0.0
    assert snapshots[2].raw_feature("api_key", "key-1", "neighbour_novelty") > 0.0


def test_volume_ratio_measures_change_against_the_nodes_own_past() -> None:
    events = [event(days=0, minutes=m) for m in range(2)]
    events += [event(days=1, minutes=m) for m in range(2)]
    events += [event(days=2, minutes=m) for m in range(10)]  # 5x the usual

    snapshots = GraphBuilder(graph_config()).build_snapshots(events)
    assert snapshots[1].raw_feature("api_key", "key-1", "volume_ratio") == pytest.approx(1.0)
    assert snapshots[2].raw_feature("api_key", "key-1", "volume_ratio") == pytest.approx(5.0)


def test_history_is_folded_in_after_features_are_computed() -> None:
    """Updating history first would mark every neighbour "already seen" and zero novelty forever."""
    events = [event(days=0, target="gpt-4o"), event(days=1, target="whisper-1")]
    snapshots = GraphBuilder(graph_config()).build_snapshots(events)
    assert snapshots[1].raw_feature("api_key", "key-1", "neighbour_novelty") > 0.0


def test_rebuilding_resets_history_so_runs_are_independent() -> None:
    builder = GraphBuilder(graph_config())
    events = [event(days=0, target="gpt-4o"), event(days=1, target="whisper-1")]

    first = builder.build_snapshots(events)
    second = builder.build_snapshots(events)
    assert first[1].raw_feature("api_key", "key-1", "neighbour_novelty") == pytest.approx(
        second[1].raw_feature("api_key", "key-1", "neighbour_novelty")
    )


def test_gateway_events_do_not_self_loop_the_address() -> None:
    """An HTTP event whose actor is the address must not add an ip->ip edge or double-observe.

    Before the fix, two gateway events produced events=4 and distinct_neighbours=2 on the IP
    node, because the used_from context edge pointed the address at itself; every gateway
    address's features were doubled on the map and in the stored per-run graph.
    """
    from datetime import UTC, datetime, timedelta

    from graphguard.graph.builder import GraphBuilder
    from graphguard.graph.model import FEATURE_INDEX
    from graphguard.schema import Action, ActorType, Layer, Outcome, TargetType, TraceEvent

    start = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    events = [
        TraceEvent(
            event_id=f"evt-{i}",
            timestamp=start + timedelta(minutes=i),
            layer=Layer.HTTP,
            actor_id="203.0.113.9",
            actor_type=ActorType.IP,
            target_id="/v1/login",
            target_type=TargetType.ENDPOINT,
            action=Action.LOGIN,
            outcome=Outcome.BLOCK,
            source_ip="203.0.113.9",
        )
        for i in range(2)
    ]
    snapshot = GraphBuilder(graph_config()).build_snapshots(events)[0]

    relations = set(snapshot.data.edge_types)
    assert ("ip", "used_from", "ip") not in relations

    row = snapshot.node_ids["ip"].index("203.0.113.9")
    features = snapshot.raw_features["ip"][row]
    assert features[FEATURE_INDEX["events"]] == 2.0
    assert features[FEATURE_INDEX["distinct_neighbours"]] == 1.0
