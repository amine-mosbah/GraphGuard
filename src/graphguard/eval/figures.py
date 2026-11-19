"""Result figures for the report — generated from real runs, never hand-drawn.

Role
----
Renders the quantitative result figures: precision–recall
curves per scenario, the detector-versus-scenario matrix, the attack-loudness sweep, and the
log-versus-graph headline comparison.

Inputs / outputs
----------------
Input: a :class:`~graphguard.eval.runner.RunResult` (or a config, for the sweep, which needs
several runs).
Output: vector PDFs under ``docs/assets/figures/``, plus the same numbers as CSV beside them.

Every figure is produced from a seeded pipeline run, so a reviewer can regenerate any number in
the report with ``graphguard figures``. The CSV companion exists because a figure is not evidence
on its own — the underlying values must be checkable without re-reading pixels.

Design rules applied
--------------------
- **One axis per chart.** No dual-scale plots anywhere.
- **Colour carries identity, never rank**: each method keeps the same hue in every figure, so a
  reader who learns the legend once can read all four.
- The categorical palette is validated for colour-vision deficiency (adjacent-pair ΔE ≥ 8 in
  protanopia/deuteranopia). Because these figures are printed and may be photocopied in
  greyscale, colour is *never* the only encoding: every series also carries a distinct marker and
  line style, and every value is either directly labelled or available in the CSV.
- Grid and axes are recessive; the data is the darkest thing on the page.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: figures are files, never windows
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import precision_recall_curve  # noqa: E402

from graphguard.config import GraphGuardConfig  # noqa: E402
from graphguard.eval.log_vs_graph import ComparisonResult  # noqa: E402
from graphguard.eval.metrics import label_signals  # noqa: E402
from graphguard.eval.runner import RunResult, run_experiment  # noqa: E402
from graphguard.simulator.labels import GroundTruth  # noqa: E402

#: Validated categorical palette (light surface). Slots are assigned to methods in a fixed
#: order below and never cycled — a method keeps its hue across every figure in the report.
_PALETTE = ["#355C7D", "#B5654D", "#5F8575", "#B18A44", "#A66F83", "#3F6F68", "#6E6484"]
_MARKERS = ["o", "s", "^", "D", "v", "P", "X"]
_LINESTYLES = ["-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 1)), (0, (1, 1))]

#: Display names and fixed slot order. Graph detectors first, then flat-log baselines, so the
#: legend reads as "what we built" followed by "what it is compared against".
_METHOD_ORDER: list[tuple[str, str]] = [
    ("node_outlier:egonet", "Node-outlier (egonet)"),
    ("node_outlier:dominant", "Node-outlier (DOMINANT)"),
    ("abnormal_subgraph:similarity", "Abnormal subgraph"),
    ("community_drift:codrift", "Community drift"),
    ("baseline:volume_threshold", "Flat log: volume"),
    ("baseline:error_rate_threshold", "Flat log: error rate"),
    ("baseline:off_hours_rule", "Flat log: off-hours"),
]

_SCENARIO_LABELS = {
    "stolen_key": "Stolen key",
    "coordinated_ring": "Coordinated ring",
    "slow_group_drift": "Slow group drift",
    "credential_stuffing": "Credential stuffing",
    "external_to_internal": "External to internal",
}


def _scenario_label(scenario: str) -> str:
    """Human label for a scenario, falling back to a readable form of its key.

    The fallback matters: a scenario added to the configuration but not to the map above would
    otherwise print its raw identifier beside prettified neighbours, which reads as an oversight
    in a figure that is otherwise carefully typeset.
    """
    return _SCENARIO_LABELS.get(scenario, scenario.replace("_", " ").capitalize())


_TEXT = "#0b0b0b"
_MUTED = "#52514e"
_GRID = "#d8d7d2"


def _style() -> None:
    """Apply the shared print style: serif text, recessive chrome, no top/right spines."""
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.edgecolor": _MUTED,
            "axes.labelcolor": _TEXT,
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "text.color": _TEXT,
            "xtick.color": _MUTED,
            "ytick.color": _MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "grid.color": _GRID,
            "grid.linewidth": 0.6,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.dpi": 150,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )


def _style_for(method: str) -> tuple[str, str, object]:
    """Return the (colour, marker, linestyle) permanently assigned to ``method``."""
    names = [name for name, _ in _METHOD_ORDER]
    index = names.index(method) if method in names else len(names) - 1
    return (
        _PALETTE[index % len(_PALETTE)],
        _MARKERS[index % len(_MARKERS)],
        _LINESTYLES[index % len(_LINESTYLES)],
    )


def _present_methods(comparison: ComparisonResult) -> list[tuple[str, str]]:
    """Methods from the fixed order that this run actually produced."""
    return [(name, label) for name, label in _METHOD_ORDER if name in comparison.method_signals]


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """Write the figure's underlying numbers, so no value in the report is unverifiable."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


# --------------------------------------------------------------------------- figures


def precision_recall_panels(
    comparison: ComparisonResult, truth: GroundTruth, out_dir: Path
) -> Path:
    """Precision–recall curves, one panel per attack scenario.

    Small multiples rather than one crowded axes: each scenario has its own positive rate, so a
    single panel would invite comparing curves whose baselines differ. The dotted line in each
    panel is that scenario's random-classifier precision, which is what "better than chance"
    actually means here.
    """
    _style()
    # The no-breach control compromises nothing, so it has no positive class and no curve; a
    # panel for it would be blank axes. It is excluded here and the exclusion is stated in the
    # figure's caption, which is more honest than drawing an empty plot.
    scenarios = sorted(s for s in truth.scenarios if s != "credential_stuffing")
    methods = _present_methods(comparison)

    n_cols = 2
    n_rows = (len(scenarios) + n_cols - 1) // n_cols
    figure, axes_grid = plt.subplots(
        n_rows, n_cols, figsize=(3.2 * n_cols, 2.9 * n_rows), sharey=True, sharex=True
    )
    axes = np.atleast_1d(axes_grid).ravel()
    for axis in axes[len(scenarios) :]:
        axis.set_visible(False)
    rows: list[list[object]] = []

    for axis, scenario in zip(axes[: len(scenarios)], scenarios, strict=True):
        chance: float | None = None
        silent: list[str] = []
        for method, label in methods:
            observations = [
                o
                for o in label_signals(comparison.method_signals[method], truth)
                if o.scenario in (None, scenario)
            ]
            y_true = np.array([o.scenario == scenario for o in observations], dtype=np.int8)
            y_score = np.array([o.score for o in observations], dtype=np.float64)
            if y_true.sum() == 0 or y_true.sum() == len(y_true):
                continue
            chance = float(y_true.mean())

            # A detector that returns the same score for every entity has made no decision, and
            # sklearn renders that degenerate case as a sweeping diagonal that *looks* like
            # strong performance. Drawing it would invent discrimination that does not exist, so
            # such methods are named as silent instead — which is the real finding: this detector
            # does not respond to this attack mode at all.
            if float(y_score.std()) == 0.0:
                silent.append(label)
                rows.append([scenario, label, None, "constant score — no discrimination"])
                continue

            precision, recall, _ = precision_recall_curve(y_true, y_score)
            colour, marker, linestyle = _style_for(method)
            axis.plot(
                recall,
                precision,
                color=colour,
                linestyle=linestyle,
                linewidth=2.0,
                label=label,
                marker=marker,
                markevery=max(len(recall) // 6, 1),
                markersize=4,
            )
            result = comparison.per_scenario.get(method, {}).get(scenario)
            rows.append([scenario, label, result.average_precision if result else None, ""])

        if chance is not None:
            axis.axhline(chance, color=_MUTED, linewidth=0.8, linestyle=(0, (1, 2)))
            axis.text(
                0.98,
                chance,
                "chance",
                ha="right",
                va="bottom",
                fontsize=7,
                color=_MUTED,
            )
        axis.set_title(_scenario_label(scenario), pad=16)
        if silent:
            # Placed in the margin between the title and the axes: it cannot collide with a
            # curve, with the shared legend, or with a neighbouring panel's title, and it reads
            # as part of the result rather than as a footnote.
            axis.text(
                0.5,
                1.01,
                f"silent: {', '.join(s.split(' (')[0] for s in silent)}",
                transform=axis.transAxes,
                ha="center",
                va="bottom",
                fontsize=7,
                color=_MUTED,
            )
        axis.set_xlim(0, 1)
        axis.set_ylim(0, 1.02)
        axis.grid(True, axis="both", zorder=0)
        axis.set_axisbelow(True)

    for axis in axes[len(scenarios) - n_cols : len(scenarios)]:
        axis.set_xlabel("Recall")
    for axis in axes[::n_cols]:
        axis.set_ylabel("Precision")
    # One legend for all panels: identity is learned once, then reused across the figure.
    handles: list = []
    labels: list[str] = []
    for axis in axes[: len(scenarios)]:
        for handle, label in zip(*axis.get_legend_handles_labels(), strict=True):
            if label not in labels:
                handles.append(handle)
                labels.append(label)
    figure.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.14))
    figure.subplots_adjust(top=0.90, hspace=0.42)

    path = out_dir / "pr_curves.pdf"
    figure.savefig(path)
    plt.close(figure)
    _write_csv(out_dir / "pr_curves.csv", ["scenario", "method", "average_precision", "note"], rows)
    return path


def scenario_matrix(comparison: ComparisonResult, out_dir: Path, metric: str = "roc_auc") -> Path:
    """Detector-versus-scenario heatmap — the report's summary table as a figure.

    A sequential single-hue ramp (magnitude, not identity), with every cell annotated. The
    annotation is not decoration: it is the "table view" that makes the figure readable in
    greyscale and to a colour-blind reader.
    """
    _style()
    matrix = comparison.matrix(metric)
    methods = _present_methods(comparison)
    scenarios = sorted({s for by in matrix.values() for s in by})

    grid = np.array(
        [
            [matrix.get(name, {}).get(scenario) or np.nan for scenario in scenarios]
            for name, _ in methods
        ]
    )

    figure, axis = plt.subplots(figsize=(1.7 * len(scenarios) + 3.4, 0.42 * len(methods) + 1.5))
    image = axis.imshow(grid, cmap="Blues", vmin=0.0, vmax=1.0, aspect="auto")

    axis.set_xticks(range(len(scenarios)))
    axis.set_xticklabels([_scenario_label(s) for s in scenarios])
    axis.set_yticks(range(len(methods)))
    axis.set_yticklabels([label for _, label in methods])
    axis.set_xticks(np.arange(len(scenarios) + 1) - 0.5, minor=True)
    axis.set_yticks(np.arange(len(methods) + 1) - 0.5, minor=True)
    # 2px surface gap between cells, so adjacent fills never touch.
    axis.grid(which="minor", color="#fcfcfb", linewidth=2)
    axis.tick_params(which="minor", length=0)
    axis.tick_params(which="major", length=0)

    for row in range(grid.shape[0]):
        for column in range(grid.shape[1]):
            value = grid[row, column]
            if np.isnan(value):
                # A scenario with no positives among the scored vertices yields no metric at all.
                # Leaving the cell blank reads as a rendering fault; saying so makes the absence
                # the finding it actually is — see the no-breach control in the results chapter.
                axis.text(column, row, "n/a", ha="center", va="center", fontsize=8, color=_MUTED)
                continue
            axis.text(
                column,
                row,
                f"{value:.3f}",
                ha="center",
                va="center",
                fontsize=8,
                # Ink, not the series colour: dark text on light cells and vice versa.
                color="#ffffff" if value > 0.6 else _TEXT,
            )

    bar = figure.colorbar(image, ax=axis, fraction=0.025, pad=0.02)
    bar.set_label(metric.replace("_", " ").upper(), fontsize=8, color=_MUTED)
    bar.outline.set_visible(False)
    bar.ax.tick_params(labelsize=7, length=0, colors=_MUTED)

    path = out_dir / f"scenario_matrix_{metric}.pdf"
    figure.savefig(path)
    plt.close(figure)
    _write_csv(
        out_dir / f"scenario_matrix_{metric}.csv",
        ["method", *scenarios],
        [[label, *[matrix.get(name, {}).get(s) for s in scenarios]] for name, label in methods],
    )
    return path


def log_vs_graph_summary(comparison: ComparisonResult, out_dir: Path) -> Path:
    """Plot the headline comparison: best graph detector versus best flat-log rule.

    Grouped bars with direct value labels. This is the figure that states the thesis, and it is
    deliberately drawn so it can also *refute* it: where a flat rule wins, the bar is taller.
    """
    _style()
    # The no-breach control is n/a in the matrix (no positive class); drawing it as a 0.00 bar
    # here would present the same absence as a measurement. Excluded, and the caption says so.
    scenarios = sorted(
        {s for by in comparison.per_scenario.values() for s in by} - {"credential_stuffing"}
    )
    graph_methods = [n for n, _ in _present_methods(comparison) if not n.startswith("baseline:")]
    flat_methods = [n for n, _ in _present_methods(comparison) if n.startswith("baseline:")]

    def best(methods: list[str], scenario: str) -> tuple[float, str]:
        scored = [
            (comparison.per_scenario[m][scenario].average_precision or 0.0, m)
            for m in methods
            if scenario in comparison.per_scenario.get(m, {})
        ]
        return max(scored) if scored else (0.0, "")

    graph_values = [best(graph_methods, s) for s in scenarios]
    flat_values = [best(flat_methods, s) for s in scenarios]

    positions = np.arange(len(scenarios))
    width = 0.36
    figure, axis = plt.subplots(figsize=(1.9 * len(scenarios) + 2.0, 3.2))

    for offset, values, label, colour, hatch in (
        (-width / 2 - 0.01, graph_values, "Best graph detector", _PALETTE[0], None),
        (+width / 2 + 0.01, flat_values, "Best flat-log rule", _PALETTE[1], "//"),
    ):
        bars = axis.bar(
            positions + offset,
            [v for v, _ in values],
            width,
            label=label,
            color=colour,
            edgecolor="#fcfcfb",
            linewidth=1.0,
            hatch=hatch,
        )
        for bar_patch, (value, _method) in zip(bars, values, strict=True):
            axis.text(
                bar_patch.get_x() + bar_patch.get_width() / 2,
                value + 0.02,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=_TEXT,
            )

    axis.set_xticks(positions)
    axis.set_xticklabels([_scenario_label(s) for s in scenarios])
    axis.set_ylabel("Average precision")
    axis.set_ylim(0, 1.12)
    axis.grid(True, axis="y", zorder=0)
    axis.set_axisbelow(True)
    # Below the axes rather than inside them: with four scenario groups there is no corner the
    # legend cannot collide with.
    axis.legend(loc="upper center", ncol=2, bbox_to_anchor=(0.5, -0.12))

    path = out_dir / "log_vs_graph.pdf"
    figure.savefig(path)
    plt.close(figure)
    _write_csv(
        out_dir / "log_vs_graph.csv",
        ["scenario", "best_graph_ap", "best_graph_method", "best_flat_ap", "best_flat_method"],
        [
            [s, g_value, g_method, f_value, f_method]
            for s, (g_value, g_method), (f_value, f_method) in zip(
                scenarios, graph_values, flat_values, strict=True
            )
        ],
    )
    return path


def isolate_scenario(cfg: GraphGuardConfig, keep: str) -> GraphGuardConfig:
    """Return a copy of ``cfg`` with every attack scenario disabled except ``keep``.

    A sweep over one scenario's parameters has to hold the rest of the dataset still: other
    attacks shift the random stream and change the population the detectors are fitted against,
    so leaving one enabled makes the swept figure measure two things at once.

    Scenarios are enumerated from the configuration model rather than named here. An earlier
    version of the sweep listed them by hand and was silently invalidated when the HTTP telemetry
    layer added two more (``DECISIONS.md`` D27): its "isolated" runs stopped being isolated, and
    the published figure understated the graph detector for a month. Reflection means a scenario
    added later is excluded automatically instead of quietly corrupting the figure.
    """
    variant = cfg.model_copy(deep=True)
    scenarios = variant.simulator.scenarios
    known = sorted(type(scenarios).model_fields)
    if keep not in known:
        raise ValueError(f"unknown scenario {keep!r}; expected one of {known}")
    for name in type(scenarios).model_fields:
        if name != keep:
            getattr(scenarios, name).enabled = False
    return variant


def loudness_sweep(
    cfg: GraphGuardConfig,
    out_dir: Path,
    multipliers: Sequence[float] = (1.5, 2.0, 4.0, 8.0),
) -> Path:
    """Plot detection quality on ``stolen_key`` as a function of how loud the attack is.

    The single most important robustness figure in the report. A detector that only works against
    a careless attacker is a weak result, so this sweeps the attacker from nearly-quiet to
    obvious and reports what each method retains.
    """
    _style()
    rows: list[list[object]] = []
    series: dict[str, list[float]] = {}

    for multiplier in multipliers:
        variant = isolate_scenario(cfg, "stolen_key")
        variant.simulator.scenarios.stolen_key.volume_multiplier = multiplier

        result = run_experiment(variant, save=False)
        for method, label in _present_methods(result.comparison):
            outcome = result.comparison.per_scenario.get(method, {}).get("stolen_key")
            value = (outcome.average_precision if outcome else None) or 0.0
            series.setdefault(label, []).append(value)
            rows.append([multiplier, label, value])

    figure, axis = plt.subplots(figsize=(5.6, 3.4))
    for method, label in _METHOD_ORDER:
        if label not in series:
            continue
        colour, marker, linestyle = _style_for(method)
        axis.plot(
            list(multipliers),
            series[label],
            color=colour,
            marker=marker,
            linestyle=linestyle,
            linewidth=2.0,
            markersize=6,
            label=label,
        )

    axis.set_xlabel("Attack volume multiplier (× the key's normal rate)")
    axis.set_ylabel("Average precision")
    axis.set_xticks(list(multipliers))
    axis.set_xticklabels([f"{m:g}×" for m in multipliers])
    axis.set_ylim(0, 1.02)
    axis.grid(True, zorder=0)
    axis.set_axisbelow(True)
    axis.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))

    path = out_dir / "loudness_sweep.pdf"
    figure.savefig(path)
    plt.close(figure)
    _write_csv(
        out_dir / "loudness_sweep.csv", ["volume_multiplier", "method", "average_precision"], rows
    )
    return path


def generate_all(
    cfg: GraphGuardConfig,
    out_dir: Path,
    *,
    result: RunResult | None = None,
    with_sweep: bool = True,
) -> list[Path]:
    """Produce every report figure, returning the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run = result if result is not None else run_experiment(cfg, save=False)

    paths = [
        precision_recall_panels(run.comparison, run.dataset.ground_truth, out_dir),
        scenario_matrix(run.comparison, out_dir, metric="roc_auc"),
        scenario_matrix(run.comparison, out_dir, metric="average_precision"),
        log_vs_graph_summary(run.comparison, out_dir),
    ]
    if with_sweep:
        paths.append(loudness_sweep(cfg, out_dir))
    return paths
