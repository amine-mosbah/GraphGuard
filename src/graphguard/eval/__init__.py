"""Evaluation harness: BOND-style metrics, the log-vs-graph comparison, and saved runs."""

from graphguard.eval.log_vs_graph import ComparisonResult, compare
from graphguard.eval.metrics import EvaluationResult, evaluate, label_signals
from graphguard.eval.runner import RunResult, run_experiment

__all__ = [
    "ComparisonResult",
    "EvaluationResult",
    "RunResult",
    "compare",
    "evaluate",
    "label_signals",
    "run_experiment",
]
