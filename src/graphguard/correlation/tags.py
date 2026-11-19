"""Framework tags for detector findings — the taxonomy label an alert carries.

Role
----
Maps each detector family to the MITRE ATLAS tactic and OWASP LLM Top 10 category its target
attack mode falls under, so a persisted alert names where it sits in the taxonomies an analyst
already knows. The mapping is the one recorded in ``docs/THREAT_MODEL.md``: the stolen-credential
mode is ATLAS *Valid Accounts* and OWASP *LLM10 Unbounded Consumption*; a coordinated ring is
ATLAS *Resource Development*; group drift is ATLAS *Discovery*.

Inputs / outputs
----------------
Input: a detector method name (``"node_outlier:egonet"``) and the ``tag_frameworks`` list from
:class:`~graphguard.config.CorrelationConfig`.
Output: a tuple of tag strings, filtered to the frameworks the deployment enabled. An empty
``tag_frameworks`` disables tagging rather than guessing at a default, so the configuration
surface stays the single source of truth.

Why a static map rather than inference
--------------------------------------
A tag is an index into a taxonomy, not a finding. The detector already *is* the classification —
each one targets a single attack mode by design (docs/DESIGN.md §3.2) — so deriving the tag from the
detector is exact, whereas inferring it per alert from features would manufacture a second,
weaker classifier and present its guesses as taxonomy.
"""

from __future__ import annotations

# Framework prefixes as they appear in config ``correlation.tag_frameworks``.
_ATLAS = "MITRE_ATLAS"
_OWASP = "OWASP_LLM_TOP10"

#: Detector family -> framework tags, per the threat-model mapping. Keys are matched on the
#: method prefix before the colon, so ``node_outlier:egonet`` and ``node_outlier:dominant``
#: share one entry. Flat-log baselines are deliberately absent: they exist for comparison and
#: never reach the persisted queue.
DETECTOR_TAGS: dict[str, tuple[str, ...]] = {
    "node_outlier": (
        f"{_ATLAS}:Valid_Accounts",
        f"{_OWASP}:LLM10_Unbounded_Consumption",
    ),
    "abnormal_subgraph": (
        f"{_ATLAS}:Resource_Development",
        f"{_OWASP}:LLM10_Unbounded_Consumption",
    ),
    "community_drift": (f"{_ATLAS}:Discovery",),
}


def tags_for(method: str, enabled_frameworks: list[str]) -> list[str]:
    """Tags for one detector method, restricted to the enabled frameworks.

    ``method`` is the full method name (``family:variant``); only the family selects tags.
    Unknown families return no tags rather than raising, because the tag is an annotation on an
    alert, and refusing to persist the alert over a missing label would invert their importance.
    """
    family = method.split(":", 1)[0]
    candidates = DETECTOR_TAGS.get(family, ())
    if not enabled_frameworks:
        return []
    return [tag for tag in candidates if tag.split(":", 1)[0] in enabled_frameworks]
