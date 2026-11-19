"""The synthetic population: who exists on the platform and how each one habitually behaves.

Role
----
Builds a stable cast of users, API keys, models and source IPs, each with a **persistent
behavioural profile**. Both the benign generator and the attack scenarios draw on this population,
so an attacker can compromise a *specific* key whose normal behaviour is already defined.

Inputs / outputs
----------------
Input: :class:`~graphguard.config.BenignConfig` and a seeded ``numpy`` generator.
Output: a :class:`Population`, consumed by ``simulator.benign`` and ``simulator.scenarios.*``.

Why profiles must be persistent
-------------------------------
The node-outlier detector's claim is *"this account stopped behaving like itself"*
(docs/DESIGN.md §3.2). That sentence is only meaningful if "itself" exists — i.e. if each key has
a stable model affinity, hour-of-day rhythm, volume level and home IP that persist across the
observation period. A population of interchangeable random agents would make the detector's task
either trivial or impossible, and either way unrepresentative.

Why the personas are deliberately awkward
-----------------------------------------
``batch_job`` keys run at 03:00 and ``app_developer`` keys are very high volume. That is
intentional: it means *"activity at 3am"* and *"lots of calls"* are **not**, by themselves, attack
signals. A naive flat-log rule will therefore raise false positives on these legitimate accounts,
which is precisely the comparison the log-vs-graph evaluation needs to be honest
(``docs/THREAT_MODEL.md`` §4). A population where every attack signal is also a trivial rule would
make the whole thesis unfalsifiable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from graphguard.config import BenignConfig

# --------------------------------------------------------------------------- catalogue

#: Models offered by the simulated platform, with relative popularity.
#: Popularity is uneven on purpose — a uniform catalogue would make "used a model it never
#: touched before" indistinguishable from ordinary exploration.
MODEL_CATALOGUE: dict[str, float] = {
    "gpt-4o": 0.26,
    "gpt-4o-mini": 0.24,
    "claude-sonnet-4": 0.18,
    "llama-3-70b": 0.12,
    "mistral-large": 0.08,
    "text-embed-3": 0.07,
    "whisper-1": 0.03,
    "code-assist-2": 0.02,
}

#: Office network — most corporate traffic originates here.
OFFICE_PREFIX = "10.0."
#: Residential/remote workers.
REMOTE_PREFIX = "198.51.100."


@dataclass(frozen=True)
class Persona:
    """A behavioural archetype.

    Attributes
    ----------
    calls_per_day:
        (low, high) range for a key's mean daily call volume; the actual per-day count is Poisson
        around this mean.
    n_models:
        (low, high) range for how many distinct models the key habitually uses.
    peak_hours:
        Hours of day the persona centres its activity on. ``None`` means "use working hours".
    hour_spread:
        Standard deviation, in hours, of the activity bump. Small = very regular.
    baseline_hour_weight:
        Floor probability for every hour, so no persona is perfectly deterministic.
    respects_weekend:
        Whether weekend activity is damped. Automation does not take weekends off.
    tokens_in_mu / tokens_in_sigma:
        Log-normal parameters for prompt size.

    """

    name: str
    calls_per_day: tuple[float, float]
    n_models: tuple[int, int]
    peak_hours: tuple[float, ...] | None
    hour_spread: float
    baseline_hour_weight: float
    respects_weekend: bool
    tokens_in_mu: float
    tokens_in_sigma: float


PERSONAS: dict[str, Persona] = {
    # Exploratory: many models, bursty, spills into the evening.
    "researcher": Persona(
        name="researcher",
        calls_per_day=(30.0, 120.0),
        n_models=(3, 5),
        peak_hours=None,
        hour_spread=4.0,
        baseline_hour_weight=0.35,
        respects_weekend=True,
        tokens_in_mu=6.2,
        tokens_in_sigma=0.8,
    ),
    # Production traffic: few models, high volume, tight working hours.
    "app_developer": Persona(
        name="app_developer",
        calls_per_day=(150.0, 420.0),
        n_models=(1, 2),
        peak_hours=None,
        hour_spread=2.5,
        baseline_hour_weight=0.10,
        respects_weekend=True,
        tokens_in_mu=5.4,
        tokens_in_sigma=0.5,
    ),
    # Scheduled automation: single model, off-hours, extremely regular, ignores weekends.
    # This persona is why "off-hours" alone cannot be an attack signal.
    "batch_job": Persona(
        name="batch_job",
        calls_per_day=(200.0, 600.0),
        n_models=(1, 1),
        peak_hours=(2.0, 3.0, 4.0),
        hour_spread=0.8,
        baseline_hour_weight=0.02,
        respects_weekend=False,
        tokens_in_mu=5.0,
        tokens_in_sigma=0.3,
    ),
    # Occasional human use: low volume, irregular.
    "casual": Persona(
        name="casual",
        calls_per_day=(5.0, 30.0),
        n_models=(1, 3),
        peak_hours=None,
        hour_spread=5.0,
        baseline_hour_weight=0.55,
        respects_weekend=True,
        tokens_in_mu=5.8,
        tokens_in_sigma=0.9,
    ),
}


# --------------------------------------------------------------------------- entities


@dataclass(frozen=True)
class ApiKey:
    """A credential with a persistent behavioural fingerprint.

    This is the primary entity the node-outlier detector scores: a compromise shows up as this
    key's observed behaviour diverging from the profile recorded here.
    """

    key_id: str
    user_id: str
    persona: Persona
    #: Models this key habitually calls, with usage weights (sums to 1).
    model_affinity: dict[str, float]
    #: Source IPs this key normally originates from, with weights (sums to 1).
    home_ips: dict[str, float]
    #: Mean calls per day.
    rate: float
    #: Probability of activity per hour of day; length 24, sums to 1.
    hour_weights: np.ndarray = field(repr=False)

    @property
    def known_models(self) -> frozenset[str]:
        """Models this key legitimately uses — the complement defines "a model it never used"."""
        return frozenset(self.model_affinity)


@dataclass(frozen=True)
class Population:
    """The full simulated cast."""

    keys: tuple[ApiKey, ...]
    models: tuple[str, ...]

    def key_by_id(self, key_id: str) -> ApiKey:
        """Look up a key, raising a clear error if it is absent."""
        for key in self.keys:
            if key.key_id == key_id:
                return key
        raise KeyError(f"no api_key {key_id!r} in population")

    def summary(self) -> dict[str, int]:
        """Count keys per persona, for logging and the report's dataset description."""
        counts: dict[str, int] = {}
        for key in self.keys:
            counts[key.persona.name] = counts.get(key.persona.name, 0) + 1
        counts["total_keys"] = len(self.keys)
        counts["total_users"] = len({k.user_id for k in self.keys})
        return counts


# --------------------------------------------------------------------------- construction


def hour_weights(
    persona: Persona,
    working_hours: tuple[int, int],
    rng: np.random.Generator,
) -> np.ndarray:
    """Build a 24-hour activity distribution for one key.

    The shape is a Gaussian bump over the persona's peak hours plus a baseline floor, with a small
    per-key jitter of the peak so that two keys of the same persona are similar but not identical.
    Without that jitter, every ``app_developer`` would share one rhythm and per-key deviation would
    be undetectable by construction.
    """
    if persona.peak_hours is None:
        start, end = working_hours
        centre = (start + end) / 2.0
    else:
        centre = float(np.mean(persona.peak_hours))

    # Per-key personality: shift the peak slightly and vary the spread.
    centre = centre + rng.normal(0.0, 0.9)
    spread = max(0.4, persona.hour_spread * rng.uniform(0.8, 1.2))

    hours = np.arange(24, dtype=float)
    # Circular distance, so a peak near midnight does not get truncated.
    delta = np.minimum(np.abs(hours - centre), 24.0 - np.abs(hours - centre))
    bump = np.exp(-0.5 * (delta / spread) ** 2)

    weights = bump + persona.baseline_hour_weight
    return weights / weights.sum()


def _assign_ips(persona: Persona, rng: np.random.Generator) -> dict[str, float]:
    """Give a key one to three home IPs, weighted toward a dominant one.

    Automation sits on a single fixed host; humans move between office and home. A key with a
    stable IP set is what makes "logged in from somewhere new" a meaningful observation.
    """
    is_automation = persona.name == "batch_job"
    n_ips = 1 if is_automation else int(rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]))

    ips: list[str] = []
    for _ in range(n_ips):
        if is_automation or rng.random() < 0.75:
            ips.append(f"{OFFICE_PREFIX}{rng.integers(1, 8)}.{rng.integers(2, 254)}")
        else:
            ips.append(f"{REMOTE_PREFIX}{rng.integers(2, 254)}")

    # Dominant-first weights: the primary IP carries most traffic.
    raw = np.sort(rng.dirichlet(np.full(len(ips), 1.5)))[::-1]
    return dict(zip(ips, (float(w) for w in raw), strict=True))


def _assign_models(persona: Persona, rng: np.random.Generator) -> dict[str, float]:
    """Pick the key's habitual models, weighted by catalogue popularity."""
    names = list(MODEL_CATALOGUE)
    popularity = np.array([MODEL_CATALOGUE[n] for n in names], dtype=float)
    popularity /= popularity.sum()

    low, high = persona.n_models
    n = int(rng.integers(low, high + 1))
    chosen = rng.choice(names, size=n, replace=False, p=popularity)

    weights = np.sort(rng.dirichlet(np.full(n, 2.0)))[::-1]
    return dict(zip((str(c) for c in chosen), (float(w) for w in weights), strict=True))


def build_population(cfg: BenignConfig, rng: np.random.Generator) -> Population:
    """Create the simulated population.

    Every user owns one API key; roughly one in five owns a second (a service key alongside a
    personal one), which gives the graph a non-trivial ``user -[owns]-> api_key`` fan-out rather
    than a boring one-to-one mapping.
    """
    persona_names = list(cfg.persona_mix)
    persona_probs = np.array([cfg.persona_mix[n] for n in persona_names], dtype=float)

    unknown = set(persona_names) - set(PERSONAS)
    if unknown:
        raise ValueError(
            f"config persona_mix references unknown personas {sorted(unknown)}; "
            f"known personas are {sorted(PERSONAS)}"
        )

    keys: list[ApiKey] = []
    for user_idx in range(cfg.n_users):
        user_id = f"u-{user_idx:03d}"
        n_keys = 2 if rng.random() < 0.2 else 1

        for key_idx in range(n_keys):
            persona = PERSONAS[str(rng.choice(persona_names, p=persona_probs))]
            low, high = persona.calls_per_day
            keys.append(
                ApiKey(
                    key_id=f"key-{user_idx:03d}-{key_idx}",
                    user_id=user_id,
                    persona=persona,
                    model_affinity=_assign_models(persona, rng),
                    home_ips=_assign_ips(persona, rng),
                    rate=float(rng.uniform(low, high)),
                    hour_weights=hour_weights(persona, cfg.working_hours, rng),
                )
            )

    return Population(keys=tuple(keys), models=tuple(MODEL_CATALOGUE))
