"""Correlation — turning detector signals into ranked, explained, cross-layer findings.

``crosslayer`` correlates the two telemetry layers on the shared address identity the graph
schema establishes, at event resolution, and reports each finding as an ordered attack chain
(docs/DESIGN.md §7). ``tags`` maps each detector family to its MITRE ATLAS / OWASP LLM taxonomy
labels, per the threat-model mapping.
"""

from graphguard.correlation.crosslayer import AttackChain, ChainStep, find_attack_chains
from graphguard.correlation.tags import DETECTOR_TAGS, tags_for

__all__ = ["AttackChain", "ChainStep", "DETECTOR_TAGS", "find_attack_chains", "tags_for"]
