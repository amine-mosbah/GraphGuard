"""Architectural constraints, enforced rather than documented.

``DECISIONS.md`` D17 states a one-way dependency rule: the platform layer imports the analytics
packages, and the analytics packages never import the platform. That rule is what keeps the thesis
contribution separable from the service around it, and what keeps every reported figure
reproducible by someone who clones the repository and runs the CLI with no database, no Redis and
no accounts.

A rule that lives only in a document is a rule that erodes. One convenience import — a detector
reaching for a database session to cache something — would silently make the evaluation harness
require infrastructure, and nobody would notice until a reader tried to reproduce a number.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "graphguard"

#: The analytics core: everything the thesis contribution lives in.
ANALYTICS_PACKAGES = (
    "schema",
    "ingestion",
    "graph",
    "detection",
    "correlation",
    "simulator",
    "eval",
)

#: Modules the analytics core is forbidden to import.
FORBIDDEN_PREFIXES = ("graphguard.platform", "sqlalchemy", "celery", "redis", "fastapi", "jwt")


def _imported_modules(path: Path) -> set[str]:
    """Return every module name imported by the file at ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _analytics_modules() -> list[Path]:
    """Every Python file in the analytics packages."""
    return sorted(
        path
        for package in ANALYTICS_PACKAGES
        for path in (SRC / package).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_the_analytics_packages_exist() -> None:
    """Guard against this whole file silently passing because a path was renamed."""
    modules = _analytics_modules()
    assert len(modules) > 15, f"only found {len(modules)} analytics modules — has the layout moved?"


@pytest.mark.parametrize("module_path", _analytics_modules(), ids=lambda p: p.name)
def test_analytics_never_imports_the_platform(module_path: Path) -> None:
    """No analytics module may import the platform layer or any service dependency."""
    offenders = sorted(
        imported
        for imported in _imported_modules(module_path)
        if imported.startswith(FORBIDDEN_PREFIXES)
    )
    relative = module_path.relative_to(SRC.parent.parent)
    assert not offenders, (
        f"{relative} imports {', '.join(offenders)}. The analytics core must run with no "
        f"database, no broker and no web framework (DECISIONS.md D17)."
    )


def test_the_analytics_core_imports_without_any_service_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing and running the pipeline must not touch a database or a broker.

    Pointing every service variable at an address that cannot be reached makes the test fail loudly
    if any of them is ever contacted, rather than passing because a local server happened to exist.
    """
    monkeypatch.setenv("GRAPHGUARD_DATABASE_URL", "postgresql+psycopg://nobody@203.0.113.1:1/none")
    monkeypatch.setenv("GRAPHGUARD_REDIS_URL", "redis://203.0.113.1:1/0")

    for name in [module for module in sys.modules if module.startswith("graphguard")]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    from graphguard.config import load_config
    from graphguard.graph.builder import GraphBuilder
    from graphguard.simulator.dataset import generate_dataset

    cfg = load_config()
    cfg.simulator.days = 2
    cfg.simulator.benign.n_users = 5
    cfg.simulator.scenarios.slow_group_drift.enabled = False
    cfg.simulator.scenarios.coordinated_ring.enabled = False

    dataset = generate_dataset(cfg)
    snapshots = GraphBuilder(cfg.graph).build_snapshots(list(dataset.events))

    assert dataset.events
    assert snapshots
