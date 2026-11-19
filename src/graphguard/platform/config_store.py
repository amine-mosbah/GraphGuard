"""Persisted detection configuration.

Role
----
Lets an administrator change detection parameters through the platform rather than by editing
``config/default.yaml`` on the server, and keeps every version that has ever been active.

Why versions instead of a mutable settings row
----------------------------------------------
An alert only means something against the thresholds that produced it. "This entity was unusual"
is a claim relative to a window size, a contamination rate and an alert threshold; overwrite those
and an analyst reviewing last month's verdict has no way to reconstruct what the system was
asserting at the time. Keeping each version and stamping runs with the one they used makes the
history interpretable rather than merely recorded.

It also makes a bad change reversible: activating an earlier revision is one call, which matters
because a threshold quietly raised looks — from the queue — exactly like a period with no attacks.

Relationship to ``config/default.yaml``
---------------------------------------
The YAML remains the origin. With no saved version the platform uses it unchanged, so a fresh
deployment and the CLI agree, and the evaluation harness is unaffected by anything done here
(``DECISIONS.md`` D17 — the analytics core never reads this module).

What happens when the schema moves under a stored revision
----------------------------------------------------------
A revision is a *snapshot of a payload*, but :class:`~graphguard.config.GraphGuardConfig` forbids
unknown keys (``config.py`` — a typo must not leave a detector silently on its defaults). Those two
facts collide the moment a field is renamed or dropped: a revision saved months earlier no longer
validates, and every caller of :func:`effective_config` raises.

That was not hypothetical. ``correlation.explanation.subgraph_radius`` was removed from the schema
while revision 4 was the active one, which took out ``GET /api/v1/config`` and the run-dispatch
path with a 500 — the settings page could not be opened at all.

So loading is fault-tolerant while *saving* stays strict. An unparseable revision is reported, not
raised on: :func:`effective_state` falls back to the file and names the revision and the exact
validation error, so the platform keeps running on a configuration that is known-good and an
administrator is told which stored revision needs migrating. Failing closed here would be the
wrong trade — it takes down detection to protect a stale artifact.

Inputs / outputs
----------------
Input: a validated :class:`~graphguard.config.GraphGuardConfig` and the administrator saving it.
Output: ``config_versions`` rows, and the effective configuration new runs are dispatched with.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from graphguard.config import GraphGuardConfig, load_config
from graphguard.platform.models import ConfigVersion, User


def active_version(session: Session) -> ConfigVersion | None:
    """Return the configuration version currently in force, if one has been saved."""
    return session.scalar(select(ConfigVersion).where(ConfigVersion.is_active))


@dataclass(frozen=True)
class EffectiveConfig:
    """The configuration in force, and how it was arrived at."""

    config: GraphGuardConfig
    """What a new run will actually be dispatched with."""

    version: ConfigVersion | None
    """The stored revision it came from, or ``None`` when the file is in use."""

    stale_version: ConfigVersion | None = None
    """A revision that is marked active but no longer validates against the current schema."""

    stale_reason: str = ""
    """The validation error from that revision, phrased for an administrator."""

    @property
    def source(self) -> str:
        """Where the configuration came from, for display."""
        if self.stale_version is not None:
            return "config/default.yaml (fallback)"
        return "database" if self.version is not None else "config/default.yaml"


def effective_state(session: Session) -> EffectiveConfig:
    """Return the configuration a new run should use, and its provenance.

    Falls back to the file when nothing has been saved, so the platform, the CLI and the
    evaluation harness all start from the same place — and equally when a stored revision no
    longer parses, rather than propagating the failure to every caller (see the module docstring).
    """
    version = active_version(session)
    if version is None:
        return EffectiveConfig(config=load_config(), version=None)
    try:
        parsed = GraphGuardConfig.model_validate(version.payload)
        return EffectiveConfig(config=parsed, version=version)
    except ValidationError as exc:
        return EffectiveConfig(
            config=load_config(),
            version=None,
            stale_version=version,
            stale_reason=_describe(exc),
        )


def _describe(exc: ValidationError) -> str:
    """Render a pydantic failure as the lines an administrator has to act on.

    ``str(exc)`` carries a documentation URL per error and the input repr, which is noise in a
    banner. What matters is which key is at fault and why.
    """
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{location}: {error['msg']}")
    return "\n".join(lines) or "the stored revision does not match the current schema"


def effective_config(session: Session) -> GraphGuardConfig:
    """Return the configuration a new run should use."""
    return effective_state(session).config


def save_version(
    session: Session,
    cfg: GraphGuardConfig,
    *,
    author: User | None,
    note: str = "",
    activate: bool = True,
) -> ConfigVersion:
    """Store ``cfg`` as a new revision and, by default, make it the active one."""
    highest = session.scalar(select(ConfigVersion.revision).order_by(ConfigVersion.revision.desc()))
    version = ConfigVersion(
        revision=(highest or 0) + 1,
        payload=cfg.model_dump(mode="json"),
        note=note,
        is_active=False,
        created_by_id=author.id if author else None,
    )
    session.add(version)
    session.flush()
    if activate:
        activate_version(session, version)
    return version


def activate_version(session: Session, version: ConfigVersion) -> ConfigVersion:
    """Make ``version`` the only active configuration.

    Deactivating every other row first rather than only the previously active one: if a bug or a
    partially-applied migration ever left two rows active, ``effective_config`` would return
    whichever the database happened to order first, and detection would silently use parameters
    nobody chose.
    """
    for other in session.scalars(select(ConfigVersion).where(ConfigVersion.is_active)):
        other.is_active = False
    version.is_active = True
    session.flush()
    return version


def history(session: Session, limit: int = 50) -> list[ConfigVersion]:
    """Return saved versions, newest first."""
    return list(
        session.scalars(select(ConfigVersion).order_by(ConfigVersion.revision.desc()).limit(limit))
    )


def parses(version: ConfigVersion) -> bool:
    """Whether this stored revision still validates against the current schema."""
    try:
        GraphGuardConfig.model_validate(version.payload)
    except ValidationError:
        return False
    return True


def version_summary(version: ConfigVersion) -> dict[str, object]:
    """Compact representation for the settings page.

    ``parses`` is included so the revision list can mark the ones that can no longer be activated,
    rather than offering a rollback that will be rejected the moment it is used.
    """
    return {
        "id": version.id,
        "revision": version.revision,
        "note": version.note,
        "is_active": version.is_active,
        "parses": parses(version),
        "created_by_id": version.created_by_id,
        "created_at": version.created_at.isoformat(),
    }
