"""Command-line interface.

Role
----
The operator-facing surface of the pipeline: generate datasets, inspect the effective
configuration, and (as later stages land) run detection and evaluation. Together with
``config/default.yaml`` this is the administrator's configuration surface: the one place detection
behaviour is inspected and changed.

Inputs / outputs
----------------
Input: command-line arguments and a config file.
Output: generated datasets on disk, and human-readable summaries on the terminal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from graphguard.config import GraphGuardConfig, load_config
from graphguard.simulator.dataset import generate_dataset, write_dataset

app = typer.Typer(
    name="graphguard",
    help="Graph-based security analytics for AI-platform telemetry.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(name="config", help="Inspect configuration.", no_args_is_help=True)
app.add_typer(config_app)
platform_app = typer.Typer(
    name="platform", help="Deployed-service administration.", no_args_is_help=True
)
app.add_typer(platform_app)

console = Console()

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Config file (default: nearest config/default.yaml)."),
]


def _load(config: Path | None) -> GraphGuardConfig:
    try:
        return load_config(config)
    except Exception as exc:  # noqa: BLE001 — surface any config problem as a clean CLI error
        console.print(f"[bold red]Configuration error:[/] {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def simulate(
    config: ConfigOption = None,
    out: Annotated[
        Path, typer.Option("--out", "-o", help="Output directory for the dataset.")
    ] = Path("data/synthetic"),
    seed: Annotated[
        int | None, typer.Option("--seed", help="Override the configured random seed.")
    ] = None,
) -> None:
    """Generate synthetic telemetry with injected attacks and ground-truth labels."""
    cfg = _load(config)
    if seed is not None:
        cfg.run.seed = seed

    console.print(
        f"Generating [bold]{cfg.simulator.days}[/] days from [bold]{cfg.simulator.start}[/] "
        f"with seed [bold]{cfg.run.seed}[/] ..."
    )
    dataset = generate_dataset(cfg)
    out_dir = write_dataset(dataset, cfg, out)

    summary = dataset.summary()
    table = Table(title="Generated dataset", show_header=False, title_justify="left")
    table.add_column(style="dim")
    table.add_column()
    table.add_row("events", f"{summary['events']:,}")
    table.add_row("benign", f"{summary['benign_events']:,}")
    table.add_row(
        "attack", f"{summary['attack_events']:,} ({summary['attack_event_fraction']:.2%})"
    )
    table.add_row("population", json.dumps(summary["population"]))
    table.add_row("labelled entities", json.dumps(summary["labelled_entities"]))
    table.add_row("period", f"{summary['start']} -> {summary['end']}")
    table.add_row("written to", str(out_dir))
    console.print(table)


@app.command()
def evaluate(
    config: ConfigOption = None,
    run_name: Annotated[
        str | None, typer.Option("--name", help="Run directory name (default: timestamp).")
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", help="Override the configured random seed.")
    ] = None,
) -> None:
    """Run the full pipeline — simulate, build graph, detect, evaluate — and save the run."""
    from graphguard.eval import run_experiment

    cfg = _load(config)
    if seed is not None:
        cfg.run.seed = seed

    console.print(f"Running experiment with seed [bold]{cfg.run.seed}[/] ...")
    result = run_experiment(cfg, run_name=run_name)

    table = Table(title="Log-vs-graph comparison (api_key nodes)", title_justify="left")
    table.add_column("method")
    table.add_column("ROC-AUC", justify="right")
    table.add_column("AP", justify="right")
    for k in cfg.evaluation.k_values[:2]:
        table.add_column(f"P@{k}", justify="right")
    table.add_column("first hit", justify="right")

    for name, res in result.comparison.results.items():

        def fmt(value: float | None) -> str:
            return f"{value:.3f}" if value is not None else "—"

        row = [name, fmt(res.roc_auc), fmt(res.average_precision)]
        for k in cfg.evaluation.k_values[:2]:
            row.append(fmt(res.precision_at_k.get(k)))
        row.append(str(res.first_hit_rank) if res.first_hit_rank else "—")
        table.add_row(*row)

    console.print(table)
    if result.run_dir is not None:
        console.print(f"Saved to [bold]{result.run_dir}[/]")


@app.command()
def figures(
    config: ConfigOption = None,
    out: Annotated[
        Path, typer.Option("--out", "-o", help="Directory for the generated figures.")
    ] = Path("docs/assets/figures"),
    skip_sweep: Annotated[
        bool, typer.Option("--skip-sweep", help="Skip the attack-loudness sweep (several runs).")
    ] = False,
) -> None:
    """Regenerate the result figures from a seeded pipeline run."""
    from graphguard.eval.figures import generate_all

    cfg = _load(config)
    console.print(f"Generating figures with seed [bold]{cfg.run.seed}[/] ...")
    paths = generate_all(cfg, out, with_sweep=not skip_sweep)

    table = Table(title="Figures written", show_header=False, title_justify="left")
    table.add_column(style="dim")
    for path in paths:
        table.add_row(str(path))
    console.print(table)


@config_app.command("show")
def config_show(config: ConfigOption = None) -> None:
    """Print the effective, validated configuration."""
    cfg = _load(config)
    console.print(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


@config_app.command("validate")
def config_validate(config: ConfigOption = None) -> None:
    """Validate a configuration file and report the first problem found."""
    cfg = _load(config)
    console.print("[bold green]Configuration is valid.[/]")
    console.print(
        f"  seed={cfg.run.seed} · days={cfg.simulator.days} · "
        f"users={cfg.simulator.benign.n_users} · detector={cfg.detection.node_outlier.method}"
    )


@platform_app.command("create-admin")
def platform_create_admin(
    email: Annotated[str, typer.Option(prompt=True, help="Administrator email address.")],
    password: Annotated[
        str,
        typer.Option(
            prompt=True,
            confirmation_prompt=True,
            hide_input=True,
            help="Minimum 12 characters.",
        ),
    ],
    full_name: Annotated[str, typer.Option(help="Display name.")] = "",
) -> None:
    """Create the initial administrator account.

    The password is prompted rather than accepted as a plain argument so it does not land in the
    shell history or in the process list, both of which are readable by other users on the host.
    """
    from graphguard.platform.bootstrap import BootstrapError, create_admin, user_count
    from graphguard.platform.db import session_scope

    try:
        with session_scope() as session:
            existing = user_count(session)
            user = create_admin(session, email=email, password=password, full_name=full_name)
            created_email = user.email
    except BootstrapError as exc:
        console.print(f"[bold red]Could not create administrator:[/] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(f"[bold green]Administrator created:[/] {created_email}")
    if existing:
        console.print(f"  (the database already held {existing} account(s))")


@platform_app.command("show-settings")
def platform_show_settings() -> None:
    """Print the effective deployment settings, with secrets redacted."""
    from graphguard.platform.settings import get_settings

    settings = get_settings()
    table = Table(title="Deployment settings", show_header=True)
    table.add_column("Setting")
    table.add_column("Value")
    for name, value in settings.model_dump().items():
        # Never print the signing key: this command exists to be run on a server and
        # pasted into a bug report.
        shown = "***redacted***" if "secret" in name else str(value)
        table.add_row(name, shown)
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
