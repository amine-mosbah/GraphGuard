"""Tests for report figure generation.

Figures are evidence in a graduation report, so the properties worth guarding are about honesty
and reproducibility, not aesthetics: every figure must come from a real run, ship its underlying
numbers, and never draw a curve for a detector that made no decision.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest

from graphguard.config import (
    BenignConfig,
    CoordinatedRingConfig,
    EvaluationConfig,
    GraphGuardConfig,
    LogVsGraphConfig,
    PyGODConfig,
    RunConfig,
    ScenariosConfig,
    SimulatorConfig,
    SlowGroupDriftConfig,
    StolenKeyConfig,
)
from graphguard.eval.figures import (
    _METHOD_ORDER,
    _PALETTE,
    _style_for,
    generate_all,
    isolate_scenario,
)
from graphguard.eval.runner import run_experiment


def small_config() -> GraphGuardConfig:
    cfg = GraphGuardConfig(
        run=RunConfig(seed=3),
        simulator=SimulatorConfig(
            start=datetime(2026, 3, 2, tzinfo=UTC),
            days=6,
            benign=BenignConfig(
                n_users=14,
                persona_mix={"researcher": 0.3, "app_developer": 0.4, "batch_job": 0.3},
            ),
            scenarios=ScenariosConfig(
                stolen_key=StolenKeyConfig(n_victims=1, onset_fraction=0.5),
                coordinated_ring=CoordinatedRingConfig(n_accounts=6, onset_fraction=0.35),
                slow_group_drift=SlowGroupDriftConfig(cohort_size=4, onset_fraction=0.2),
            ),
        ),
        evaluation=EvaluationConfig(
            k_values=[5, 10],
            log_vs_graph=LogVsGraphConfig(baselines=["volume_threshold", "error_rate_threshold"]),
        ),
    )
    cfg.detection.node_outlier.pygod = PyGODConfig(epochs=10, hid_dim=16)
    return cfg


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory):
    cfg = small_config()
    out = tmp_path_factory.mktemp("figures")
    # The sweep re-runs the pipeline several times; excluded here to keep the suite fast.
    result = run_experiment(cfg, save=False)
    paths = generate_all(cfg, out, result=result, with_sweep=False)
    return cfg, result, out, paths


# --------------------------------------------------------------------------- palette


def test_every_method_has_a_permanently_assigned_style() -> None:
    """Colour follows the entity, never its rank: a method keeps its hue in every figure."""
    styles = {name: _style_for(name) for name, _ in _METHOD_ORDER}
    assert len({colour for colour, _, _ in styles.values()}) == len(_METHOD_ORDER)
    assert len({marker for _, marker, _ in styles.values()}) == len(_METHOD_ORDER)


def test_style_is_stable_across_calls() -> None:
    assert _style_for("node_outlier:egonet") == _style_for("node_outlier:egonet")


def test_palette_covers_the_method_roster() -> None:
    assert len(_PALETTE) >= len(_METHOD_ORDER)


def test_colour_is_never_the_only_encoding() -> None:
    """These figures are printed and may be photocopied; marker and line style must also differ."""
    seen: set[tuple[str, object]] = set()
    for name, _ in _METHOD_ORDER:
        _, marker, linestyle = _style_for(name)
        assert (marker, linestyle) not in seen
        seen.add((marker, linestyle))


# --------------------------------------------------------------------------- outputs


def test_all_figures_are_written_as_vector_pdfs(generated) -> None:
    _, _, _, paths = generated
    assert paths
    for path in paths:
        assert path.exists(), path
        assert path.suffix == ".pdf"
        assert path.stat().st_size > 1000
        assert path.read_bytes().startswith(b"%PDF")


def test_every_figure_ships_its_numbers(generated) -> None:
    """A figure is not evidence on its own — the values behind it must be checkable."""
    _, _, out, paths = generated
    for path in paths:
        companion = path.with_suffix(".csv")
        assert companion.exists(), f"{path.name} has no CSV companion"
        rows = list(csv.reader(companion.open()))
        assert len(rows) > 1, f"{companion.name} has no data rows"
    del out


def test_matrix_csv_matches_the_evaluated_metrics(generated) -> None:
    """The figure must report exactly what the harness computed, with no re-derivation."""
    _, result, out, _ = generated
    rows = list(csv.DictReader((out / "scenario_matrix_roc_auc.csv").open()))
    matrix = result.comparison.matrix("roc_auc")
    label_of = dict(_METHOD_ORDER)

    for row in rows:
        method = next(name for name, label in label_of.items() if label == row["method"])
        for scenario, value in row.items():
            if scenario == "method" or not value:
                continue
            assert float(value) == pytest.approx(matrix[method][scenario])


def test_silent_detectors_are_named_not_drawn(generated) -> None:
    """A constant-score detector has no PR curve.

    sklearn renders that degenerate case as a sweeping diagonal that *looks* like strong
    performance; drawing it would invent discrimination that does not exist. Such methods must be
    recorded with a note and no average precision instead.
    """
    _, _, out, _ = generated
    rows = list(csv.DictReader((out / "pr_curves.csv").open()))
    assert rows

    noted = [r for r in rows if r["note"]]
    for row in noted:
        assert row["average_precision"] == ""
        assert "no discrimination" in row["note"]

    # And the scored rows carry a real number.
    for row in (r for r in rows if not r["note"]):
        assert 0.0 <= float(row["average_precision"]) <= 1.0


def test_log_vs_graph_reports_the_flat_rule_even_when_it_wins(generated) -> None:
    """The headline figure must be able to refute the thesis, not only support it."""
    _, _, out, _ = generated
    rows = list(csv.DictReader((out / "log_vs_graph.csv").open()))
    assert rows
    for row in rows:
        assert row["best_flat_method"], "the flat-log competitor must always be named"
        assert 0.0 <= float(row["best_flat_ap"]) <= 1.0
        assert 0.0 <= float(row["best_graph_ap"]) <= 1.0


def test_figures_are_reproducible_from_a_seed(tmp_path) -> None:
    """Any number in the report must be regenerable by a reviewer."""
    cfg = small_config()

    def matrix_csv() -> str:
        out = tmp_path / f"run-{id(object())}"
        result = run_experiment(cfg, save=False)
        generate_all(cfg, out, result=result, with_sweep=False)
        return (out / "scenario_matrix_roc_auc.csv").read_text()

    assert matrix_csv() == matrix_csv()


def test_isolating_a_scenario_disables_every_other_one() -> None:
    """The sweep must hold the rest of the dataset still, including scenarios added later.

    ``DECISIONS.md`` D27: the sweep once named the scenarios to disable by hand, and adding the
    HTTP telemetry layer silently un-isolated it. Enumerating the model's fields is what keeps a
    future scenario from doing the same thing again, so that property is pinned here rather than
    left to the reviewer of the next scenario.
    """
    cfg = small_config()
    isolated = isolate_scenario(cfg, "stolen_key")
    scenarios = isolated.simulator.scenarios

    assert scenarios.stolen_key.enabled, "the scenario under study must survive isolation"
    others = [n for n in type(scenarios).model_fields if n != "stolen_key"]
    assert others, "guard against this passing because the config model was restructured"
    for name in others:
        assert not getattr(scenarios, name).enabled, f"{name} was left enabled by isolation"


def test_isolating_a_scenario_does_not_mutate_the_caller_config() -> None:
    """The sweep runs several variants from one base configuration."""
    cfg = small_config()
    isolate_scenario(cfg, "stolen_key")
    assert cfg.simulator.scenarios.coordinated_ring.enabled


def test_isolating_an_unknown_scenario_is_an_error() -> None:
    """A typo must fail loudly rather than disable every scenario and sweep an empty dataset."""
    with pytest.raises(ValueError, match="unknown scenario"):
        isolate_scenario(small_config(), "stolen_keys")
