# DECISIONS.md — running decision log

> Append-only. One entry per decision: **date · decision · why · consequences**.
> When a decision changes, add a new entry that supersedes the old one (don't edit history),
> and update the affected docs in the **same commit**.
>
> *Public copy:* entries that concerned only the formatting of the thesis manuscript (D3, D16,
> D28–D31, D33, D34, D36, D37, D40, D44) are omitted, so the numbering has gaps.

---

## 2026-07-24 — D1. Python pinned to 3.12 via `uv`

**Decision.** The project targets Python **3.12** exactly (`requires-python = ">=3.12,<3.13"`),
managed by [`uv`](https://docs.astral.sh/uv/) with a committed `uv.lock`.

**Why.** The development machine had **only Python 3.14**, for which PyTorch, PyTorch Geometric
and PyGOD publish no wheels — the entire detector layer (DESIGN.md §13) would have been
unbuildable. 3.12 is the newest interpreter with full wheel coverage across the whole stack.
`uv` was chosen over `venv+pip` and conda because it manages the interpreter *and* the lockfile,
so the environment is reproducible on the lab machine without touching the system Python.

**Consequences.** `uv sync --extra dev` is the only setup step. Verified working:
torch 2.13.0, torch-geometric 2.8.0, pygod 1.1.0, networkx 3.6.1, pydantic 2.13.4.

---

## 2026-07-24 — D2. Repository root is `GraphGuard/`, not a nested `graphguard/`

**Decision.** The structure in DESIGN.md §8.2 is created **at the repository root**, with the Python package under `src/graphguard/`.

**Why.** The first sketch drew the tree with a `graphguard/` top node, which is the repo itself, not an extra
directory. A `src/` layout keeps the installed package separate from repo-level files (docs,
report, config) and prevents accidental imports from the working directory during tests.

**Consequences.** `pythonpath = ["src"]` in pytest config; hatchling packages `src/graphguard`.

---

## 2026-07-24 — D4. Configuration lives in `config/default.yaml` and is genuinely consumed

**Decision.** All runtime parameters — simulator population and attack deltas, snapshot window,
detector hyper-parameters, alert threshold, evaluation metrics — live in `config/default.yaml`.
No module hard-codes a tunable.

**Why.** Two reasons at once. Engineering: reproducible experiments require the run configuration
to be a serialisable artifact saved next to results (DESIGN.md §13). Product: the administrator role needs an
explicit configuration surface for the **"Configure Detection"** use case. One artifact pays for both.

**Consequences.** Every experiment run snapshots the effective config into its output directory.

---

## 2026-07-24 — D5. Build order is a vertical slice, not breadth-first stubs

**Decision.** First milestone is one runnable end-to-end path — schema → benign generator →
stolen-key scenario → graph builder → node-outlier detector → evaluation + log-vs-graph
comparison — before any second detector, second telemetry layer, or dashboard work.

**Why.** Everything downstream needs data to develop against. A working thin
pipeline surfaces integration problems immediately and produces the project's core evidentiary
figure early; six half-finished modules produce nothing demonstrable.

**Consequences.** Community drift, abnormal-subgraph, HTTP layer and dashboard are explicitly
deferred and disabled in `config/default.yaml` (`enabled: false`) rather than half-implemented.

---

## 2026-07-24 — D6. `TraceEvent` gains a `schema_version` field, checked on read

**Decision.** One field beyond DESIGN.md §9: `schema_version`, defaulted to `SCHEMA_VERSION`
(currently `"1.0"`) and stamped on every exported event. `read_jsonl` raises
`SchemaVersionMismatchError` on a mismatch unless `strict_version=False` is passed explicitly.

**Why.** §10 is a contract shared across **two independently evolving codebases** — GraphGuard and
the Phase 1 jailbreak project. Without a version stamp, a Phase 1 export written against an older
field set would parse "successfully" into a subtly wrong graph, and every downstream detector would
then produce confident nonsense with no visible failure. A version check converts a silent
correctness bug into a loud startup error.

**Consequences.** DESIGN.md §9 updated in the same commit. Versioning policy documented in
`docs/SCHEMA.md` §6. Phase 1 must stamp its exports with the same constant.

---

## 2026-07-24 — D7. Schema validation is strict: naive timestamps and malformed IPs are rejected

**Decision.** `TraceEvent` rejects timezone-naive timestamps, rejects `source_ip` values that are
not valid IP addresses, rejects unknown fields (`extra="forbid"`), and is immutable
(`frozen=True`). Timestamps are normalised to UTC and IPs to their canonical form.

**Why.** Each rule blocks a specific silent-corruption failure mode rather than a cosmetic one:

- A naive timestamp assumed to be UTC corrupts every temporal analysis in the system, and the
  corruption is invisible — the graph still builds.
- `source_ip` is the *only* vertex the HTTP and AI layers share, so a malformed value silently
  disables cross-layer correlation (§8) instead of erroring.
- `extra="forbid"` turns an adapter field-name typo into an ingestion failure instead of a
  quietly-dropped feature the graph builder depended on.
- Immutability prevents a detector from mutating the evidence it is scoring.

**Consequences.** Adapters must state timezones explicitly. Accepted cost: slightly noisier
ingestion of sloppy third-party data, which is the correct trade for an analytics system whose
conclusions are only as trustworthy as its timestamps.

---

## 2026-07-24 — D8. Snapshots carry two feature matrices: raw and model-ready

**Decision.** Each `GraphSnapshot` exposes `raw_features` (human units) *and* `data[type].x`
(the same values with heavy-tailed counts `log1p`-compressed). Detectors train on the latter;
explanations quote the former.

**Why.** A single matrix forces a bad choice. Feed raw counts to a detector and the `events`
dimension — spanning 10 to 10,000 — dominates reconstruction error and the model effectively
ignores every other feature. Feed compressed values to the analyst and the explanation reads
"log-volume 8.7", which nobody can act on. Since explanations are a mandatory first-class output
(DESIGN.md §13), both representations have to exist.

**Consequences.** `graph.model.FEATURE_NAMES` is shared by both, so a feature index means the same
thing in either. `FEATURE_DESCRIPTIONS` is validated to cover every feature, so no score can be
produced that cannot be explained.

---

## 2026-07-24 — D9. History-relative features exist, and their baseline drift is a known limitation

**Decision.** The builder carries per-node history across snapshots to compute two self-referential
features: `neighbour_novelty` (share of this window's connections never seen before for this node)
and `volume_ratio` (this window's activity over the node's own past mean). History is folded in
*after* each window's features are computed.

**Why.** Without carried history a snapshot supports only peer comparison ("unlike other keys"),
never self comparison ("unlike its own past") — and the latter *is* the stolen-key signal
(DESIGN.md §3.2). The update ordering matters: folding history in first would mark every neighbour
already-seen and silently zero novelty across the entire dataset.

**Observed limitation — the attack becomes the new normal.** On the default dataset the victim's
`volume_ratio` peaks at **9.48** in the first full attack window and then decays — 5.05, 3.54,
2.94, 2.28, **2.03** — as the running mean absorbs the attack traffic. `neighbour_novelty` fires
once at onset and returns to 0.0, since the attacker's models are no longer new. Both behave
correctly; the point is that **a persistent attack erodes its own baseline**.

This does not break detection here — `night_ratio` (0.77 vs 0.20), `error_ratio` (0.11 vs 0.02) and
`distinct_neighbours` (12 vs 5) stay elevated for the whole attack window — but it is a real
weakness against a slow, patient adversary, and results must not be quoted as if the peak value
persisted.

**Consequences.** This is textbook **concept drift** in an evolving graph, which is exactly what
Unicorn (gradual forgetting) and METANOIA (lifelong PIDS) address — see `docs/RELATED_WORK.md`
Family 1. Candidate mitigations for Weeks 6–7: a bounded trailing baseline, a robust (median)
baseline, or freezing the baseline once a node is flagged. **Report this honestly as a limitation
with a literature-grounded mitigation path**, rather than tuning it away silently.

---

## 2026-07-24 — D10. PyGOD runs full-batch via a loader substitution, not neighbour sampling

**Decision.** `DominantOutlierDetector` patches PyGOD's `NeighborLoader` with a full-batch
stand-in for the duration of `fit()` (scoped, restored in `finally`). PyGOD's model, loss and
scoring are untouched.

**Why.** PyGOD's training loop unconditionally builds a PyG `NeighborLoader`, whose sampler
requires the native `pyg-lib`/`torch-sparse` extensions — neither of which publishes a wheel for
arm64 macOS on torch 2.13, so DOMINANT crashed at fit time. Neighbour sampling exists purely to
*approximate* full-batch training on graphs too large for memory; GraphGuard snapshots hold a few
hundred nodes, so full-batch is the exact computation the sampler would be approximating, and
faster. This preserves DESIGN.md §13's "wrap PyGOD, never reimplement" — only the data-loading
strategy changes.

**Consequences.** Revisit if snapshots ever grow to the point where full-batch does not fit in
memory (then install the native extensions on a platform that has wheels, e.g. the lab's Linux
boxes, and delete the patch). A regression test asserts the patch is restored after fitting.

---

## 2026-07-24 — D11. Explanations are peer-deviation factors, not model internals

**Decision.** Every signal's reasons are computed as robust z-scores against the window's peer
population (MAD-based, with a std fallback where MAD is zero and suppression where the feature is
constant), independent of how the detector computed its score. Reasons quote `raw_features` in
human units.

**Why.** DOMINANT's score is a reconstruction error — not decomposable per feature — so presenting
per-feature factors as "the model's reasoning" would be a false claim. What the analyst needs is
"what is unusual about this entity", which peer deviation answers honestly, uniformly across all
detectors. The MAD fallback matters: most keys share identical ratio values (e.g. refusal 0.00),
making MAD zero; dividing by a floor manufactured a "10,207,553x higher" reason that crowded out
real ones. Found by running the pipeline, fixed, regression-tested.

**Consequences.** The explanation layer works identically for any future detector. The report must
describe reasons as "contributing factors" (peer deviation), never "model attribution".

---

## 2026-07-25 — D12. Detection results are reported per scenario, not pooled

**Decision.** `evaluate()` takes an optional `scenario`, and every run now produces a
**detector × scenario matrix** alongside the pooled numbers. When scoring against one scenario,
observations belonging to *other* scenarios are dropped from the grid rather than counted as
negatives.

**Why.** Each detector targets a different attack *mode* (DESIGN.md §3.2), so one pooled number
scores every detector against attacks it was never built for and describes none of them. This was
not hypothetical: adding the coordinated ring to the default dataset dropped the node-outlier
detector's pooled ROC-AUC from 0.91 to **0.19** — purely because it (correctly) does not rank ring
members highly. Dropping other scenarios' nodes rather than calling them negatives matters too: a
node compromised by a *different* attack is neither a positive nor an honest negative, and scoring
it as the latter would punish a detector for flagging a real attack.

**Consequences.** `metrics.json` is now `{pooled, per_scenario}`. `ComparisonResult.matrix()`
produces the report's detector-vs-scenario table directly.

---

## 2026-07-25 — D13. The log-vs-graph claim is carried by the ring, not by the stolen key

**Decision.** Report the detector × scenario matrix as the primary result, and state plainly that
the flat-log baselines **beat** the graph detectors on `stolen_key`.

**Why.** The measured matrix (default config, ROC-AUC) says so:

| method | `coordinated_ring` | `stolen_key` |
|---|---|---|
| `abnormal_subgraph:similarity` | **1.000** | 0.500 |
| `baseline:error_rate_threshold` | 0.559 | **0.963** |
| `baseline:volume_threshold` | 0.346 | 0.959 |
| `baseline:off_hours_rule` | 0.466 | 0.847 |
| `node_outlier:egonet` | 0.263 | 0.907 |
| `node_outlier:dominant` | 0.126 | 0.907 |

Two honest readings, both of which belong in the report:

1. **The thesis holds, decisively, on the ring.** Every flat-log rule sits at or below chance
   (0.35–0.56) while the graph detector reaches 1.000. This is the real "invisible in logs,
   obvious in the graph" evidence, because ring members are individually unremarkable *by
   construction* — a per-event rule has nothing to key on.
2. **On the stolen key, the flat rules win *at this loudness*.** A volume threshold (0.959) beats
   both graph detectors (0.907) — the attack-loudness caveat landing exactly where predicted: at
   `volume_multiplier: 8.0` the compromise is ~17% of all telemetry, and a crude rule catches loud
   things well. The already-measured loudness sweep in `THREAT_MODEL.md` supplies the rest of the
   picture: as the attacker quietens, the volume baseline collapses (AP 0.874 → 0.124 at 1.5×)
   while the egonet detector holds flat at ≈0.86 throughout. So the single-point comparison at 8×
   is the *worst* case for the graph, not a refutation of it.

**Consequences.** Neither scenario alone tells the truth, and neither does a pooled average. The
report must present three things together: the ring matrix (where the graph wins outright), the
stolen-key matrix (where a naive rule wins at 8×), and the loudness sweep (which explains why).
Cross-referenced from `THREAT_MODEL.md`.

---

## 2026-07-25 — D14. Community drift is detected by direction of travel, not by tracking communities

**Decision.** The community-drift detector does **not** cluster entities into communities and
track those communities across windows. It computes each entity's *displacement* from its own
recent baseline, removes the component the whole population shares, and clusters entities by the
**direction** of what remains. A group all moving the same way is the finding.

**Why.** The textbook design was implemented first and measured. It failed twice:

1. **Communities split exactly when it matters.** A drifting cohort separates from its parent
   community as it migrates — measured here, the ten drifting keys sat inside two larger
   communities early on and formed their own 13-member community by the end. Membership overlap
   between those is far below any sensible continuation threshold, so a community-tracking
   detector loses the group precisely when it has drifted furthest.
2. **Community assignment is unstable for everyone else.** Benign entities hop between clusters as
   the Louvain partition shifts window to window, and that hopping manufactures large spurious
   drift. It swamped the real signal completely: drifting cohort mean 0.16 versus benign
   maximum 0.82 — i.e. worse than useless.

Displacement direction has neither problem, because it never asks which group an entity belongs
to, only which way it is going. Measured: cohort displacements align at a median cosine of
**+0.52 to +0.60** in every window of the drift, while benign displacements are uncorrelated at
**+0.00**. Ordinary behavioural churn is real but *directionless*; a group pushed by one cause
moves as one.

**Subtracting the population-common movement** is required for the same reason the unison detector
subtracts a median rhythm: everyone's activity profile shifts together on a Saturday, and that
seasonal coherence produced ten spurious detections in a single window before the correction.
The median is used rather than the mean so a large cohort cannot drag the reference toward itself
and cancel its own signal.

**Consequences.** The classical formulation is not implemented; this is a deliberate
reinterpretation, documented as such in the module and to be defended as such in the report. It
addresses the same phenomena Ranshous et al. catalogue (splitting, merging, flickering communities)
while sidestepping the identity-tracking problem that makes them hard.

---

## 2026-07-25 — D15. Detectors overlap across scenarios, and that is reported, not suppressed

**Decision.** Report the full detector × scenario matrix including the off-diagonal cells, rather
than only each detector's own scenario.

**Why.** The overlaps are real and informative:

- The **community-drift** detector scores 0.843 ROC-AUC on `coordinated_ring` — because a ring
  driven by one script genuinely does move together as its burst schedule shifts. That is a true
  detection of coordination by a drift detector, not a false positive.
- A **drifting cohort converges into unison** as it migrates toward a shared model and schedule,
  so the abnormal-subgraph detector legitimately fires on late-stage drift.
- The two stolen keys in the default dataset are flagged as *mutually similar* by the unison
  detector — correctly: they are driven by the same attacker with the same off-hours bias.

**Consequences.** Tests that characterise one scenario disable the others, because overlapping
scenarios in one dataset make an "expected zero false positives" assertion meaningless. The
`tests/test_eval.py` fixture deliberately runs all three at once so the full matrix stays covered
end to end.

---

## 2026-08-03 — D17. GraphGuard is deployed as a service, in a `platform/` layer that analytics never imports

**Decision.** The project ships a deployable multi-user platform — database, authentication,
background jobs, containers — in a new `src/graphguard/platform/` package. The dependency rule is
one-way: **platform imports analytics; analytics never imports platform.**

**Why the boundary matters more than the layer.** The thesis contribution is the graph analytics,
and it has to stay separable from the plumbing around it for two reasons. Evidentially: `schema/`,
`graph/`, `detection/`, `correlation/` and `eval/` still run from the CLI with no database, no
Redis and no accounts, which is what keeps every reported figure reproducible from a single seed by
anyone who clones the repository. Defensively: at a viva, "which part is the research and which
part is the web application?" has a one-sentence answer backed by the import graph.

**Consequences.** A test asserts the analytics suite runs without any platform service. The
platform's own tests use a throwaway SQLite file and `fakeredis`, so the full suite needs no running
infrastructure.

---

## 2026-08-03 — D18. The backend framework is FastAPI, and the reason is the schema, not the speed

**Decision.** FastAPI + Uvicorn. Django, Flask, Spring Boot and NestJS were considered and rejected.

**Why.** Two arguments, in order of force.

*The constraint that eliminates half the field.* The detection layer is PyTorch, PyTorch Geometric,
PyGOD, NetworkX and scikit-learn — none of which exist on the JVM or in Node. A Spring Boot or
NestJS backend would need a second Python service and an inter-process hop on every detection run:
a network boundary, a serialisation format and a failure mode added in order to call code we
already own. The criterion is *pick the language the core computation lives in, then the best
framework in that language*. Two peer ENISo reports used Spring Boot; both were building CRUD over
SQL with no ML runtime, which is a different problem.

*The argument that picks the Python framework.* The canonical `TraceEvent` schema is already
pydantic v2, deliberately strict, frozen and version-stamped (D6, D7), because an undetected schema
mismatch is this system's worst failure mode — it produces a subtly wrong graph and confident
nonsense downstream with no visible error. FastAPI uses pydantic as its *native* request and
response layer, so one class definition serves ingestion validation, API validation, the OpenAPI
schema and the generated TypeScript client. Django REST Framework would require a model *and* a
serializer; Spring would require a DTO in Java. **Duplicating the schema reintroduces exactly the
divergence D6 exists to prevent.**

Django was additionally a poor structural fit: its strengths are an ORM-centric CRUD stack, and
GraphGuard's central object is an in-memory heterogeneous graph, not ORM rows. Flask would have
needed three add-on libraries to reach where FastAPI starts.

**Consequences.** The frontend generates its types from the OpenAPI schema, so a schema change
that breaks the client is a compile error rather than a runtime surprise.

---

## 2026-08-03 — D19. PostgreSQL is the database of record; the graph stays in memory

**Decision.** PostgreSQL 16 in deployment, SQLite for tests and a fresh checkout, both reached
through SQLAlchemy 2.0 with Alembic migrations. **No graph database** — this closes open item O3.

**Why Postgres rather than staying on SQLite.** Not data volume; at current scale SQLite would
cope, and the report should say so. The reasons are the deployment profile:

1. **JSONB.** Alert explanations, contributing factors, evidence subgraphs and ATLAS/OWASP tags are
   semi-structured and differ per detector. Postgres indexes JSONB natively, so those can stay
   documents without giving up queryability or adding a second store.
2. **Concurrency.** SQLite serialises writers. A Celery worker writing run results while analysts
   update triage state is precisely the contention SQLite handles badly and MVCC handles by design.
3. **A growth path that can be named without being built** — TimescaleDB for time-partitioned
   telemetry, pgvector if prompts are ever embedded.

**Why not Neo4j (O3).** Snapshots hold **247–304 nodes and 922–1335 edges** (measured, default
config). That is an in-memory object. A graph database would add a second store to keep synchronised
with the PyG structure the detectors actually consume, for no measured benefit.

**Verified, not assumed.** The platform suite runs against both backends via
`GRAPHGUARD_TEST_DATABASE_URL`, and CI executes both. Running it against a real Postgres found a
defect SQLite had hidden: the enum columns had no CHECK constraint, so `role='superadmin'` was
accepted. Fixed; the database now enforces the domain that the authorisation model rests on.

---

## 2026-08-03 — D20. Pipeline runs execute on a Celery worker, not in the request

**Decision.** A run is dispatched to a Celery worker with Redis as broker. The API returns `202`
with a run identifier.

**Why.** A full run measures **7.1 s** end to end (timed, default configuration). That is far too
long to hold an HTTP request open: a browser spinner, an eventual proxy read timeout, and a worker
process blocked on numerical work instead of serving other analysts.

**The honest caveat, which belongs in the report.** At *this* data volume an in-process background
task would also work. The queue is chosen for the deployment profile — several analysts, runs that
grow with telemetry volume, a worker restartable and scalable independently of the API — not
because 7 s is unmanageable today. Claiming otherwise would be inventing a justification.

**Consequences.** Redis earns its place three times over: Celery broker, refresh-token revocation
list, and rate-limit store. With no broker configured Celery runs eagerly in-process, so tests
exercise the same task function the worker runs rather than a stub. The worker runs
`--concurrency=1`: a run is CPU-bound on numerical work that already uses multiple threads, so
concurrent runs on one host contend for the same cores and each finishes later than if queued.

---

## 2026-08-03 — D21. Authentication is argon2id plus short access tokens and revocable rotating refresh tokens

**Decision.** Real JWT authentication with two roles. Passwords hashed with **argon2id**. Access
tokens live ~15 minutes; refresh tokens live ~14 days, rotate on every exchange, and are checked
against a Redis denylist.

**Why implement it rather than delegate it.** For a *cybersecurity* graduation project, "how does
authentication work in your system?" is a question the author should be able to answer in depth.
Delegating to a managed identity provider produces the answer "the vendor handles it", which is
weaker at a viva than a design whose properties are stated and tested. It also mirrors the
reference report's Authentication & Authorization chapter section.

**Why these mechanisms specifically.**

- **argon2id over bcrypt** — the OWASP Password Storage Cheat Sheet's first recommendation and the
  Password Hashing Competition winner. It is *memory*-hard, so GPU and ASIC attackers gain far less
  per unit of hardware; bcrypt additionally truncates input at 72 bytes, silently weakening exactly
  the long passphrases a security-conscious user chooses.
- **Short access + revocable refresh** — a JWT cannot be un-issued, so a single long-lived token
  means logout is cosmetic. Splitting the credential keeps the per-request path a stateless
  signature check while making revocation real.
- **Rotation on every refresh** — makes refresh tokens single-use, so a stolen one stops working as
  soon as either party uses it. Without it, theft grants access for the full multi-day lifetime,
  silently.
- **A `typ` claim, enforced both ways** — without it a refresh token authenticates every endpoint,
  which nullifies the short access lifetime entirely. This is a real vulnerability, not a nicety,
  and it is tested.
- **Login takes the same path for an unknown email as for a wrong password** — otherwise the
  endpoint is an account-enumeration oracle, which is the reconnaissance step before the
  credential-stuffing attack this system exists to detect elsewhere.
- **`is_revoked` raises rather than returning `False` on a Redis outage** — failing open would turn
  an infrastructure failure into an authentication bypass.

**Consequences.** The first administrator is created by CLI, not by an HTTP route: an
unauthenticated "create the first user" endpoint is a permanent privilege-escalation hole if its
first-user check is ever wrong. Deactivating an account invalidates live tokens immediately,
because the user row is re-read per request.

---

## 2026-08-03 — D22. Single-tenant, with the multi-tenant boundary named rather than half-built

**Decision.** One deployment per customer. Every authenticated analyst sees every alert. There is
no organisation model and no row-level scoping.

**Why.** This is how SOC tooling actually ships, and it matches the sensitivity of the data: an
organisation's security telemetry sitting in the same tables as another's is a risk most buyers
would refuse. Retrofitting tenancy is expensive — every table gains a scope column, every query is
filtered, and the tests that matter are the ones proving no cross-tenant leakage — so building it
speculatively for a prototype with one deployment would be cost without evidence.

**Consequences.** Stated as a boundary in `platform/auth.py` and to be stated in the report, not
left for a jury to discover. The migration path (organisation table, scoped queries, leakage tests)
belongs in future work.

---

## 2026-08-03 — D23. Supabase and Convex were evaluated and rejected

**Decision.** No Backend-as-a-Service. Self-hosted Postgres, self-implemented auth.

**Why Convex is structurally wrong.** Its backend functions run in a TypeScript runtime, so PyTorch,
PyG and PyGOD cannot execute there. It could only ever be a database beside a separate Python
service — meaning its actual value proposition, reactive TypeScript functions colocated with the
data, is exactly the part that is unusable. The cost would be a vendor runtime, a document data
model discarding the SQLAlchemy layer, and a second auth system.

**Why Supabase is mostly redundant.** Component by component: its **Postgres** works today (one
environment variable — verified), but a Postgres container also works, locally and free. **GoTrue**
would replace D21, which is the one security feature worth being able to explain. **PostgREST**
exposes tables, whereas this API serves computed pipeline output. **Realtime** is genuinely
attractive for the live status map, but the pipeline is batch — a run writes its alerts at the end,
so there is no stream to subscribe to without the streaming tier. **Storage** and **Edge Functions**
are inapplicable.

**The argument that settles it.** GraphGuard is positioned as *self-hosted* security analytics. A
jury can reasonably ask why security telemetry and analyst investigation decisions would live on
third-party infrastructure; most SOC tooling is self-hosted for exactly that reason. Keeping it
self-hosted makes that an argument the report gets to *make* rather than one it has to defend.
Self-hosting Supabase would also mean running roughly eight containers to use one of them.

**Consequences.** The persistence layer stays URL-portable, so managed Postgres (Supabase's
included) remains a deployment-time choice with no code change. That is a genuine option kept open,
not a door closed.

---

## 2026-08-03 — D24. Deployment is nginx + containers with no load balancer; CI gates in Actions, deployment in Jenkins

**Decision.** Docker Compose runs nginx, api, worker, postgres and redis on a VPS. **GitHub
Actions** runs the quality gates; **Jenkins** performs deployment. The legacy `/api/*` endpoints are
hidden from the OpenAPI schema and scheduled for deletion.

**Why no load balancer.** One in front of a single-instance service is decoration, and a jury member
who knows infrastructure will say so. What the deployment actually needs is a reverse proxy: TLS
termination, static dashboard, `/api` routing, rate limiting. The report then makes a stronger claim
— *the API is stateless, session state lives in Redis, so scaling out is adding replicas behind this
proxy and nothing else* — which is true and which the design demonstrates.

**Why the CI/CD split.** GitHub Actions runs the four gates plus three jobs local development
cannot: the platform suite against real Postgres, `alembic check` for model/migration drift with a
downgrade cycle, and a Docker build. Jenkins handles deployment to the VPS, which is where a
self-hosted runner with server credentials is the appropriate tool and where the reference reports'
CI/CD figures sit. Actions alone would have been cheaper; the split is a deliberate choice to have
a real deployment pipeline rather than a green badge.

**Why the legacy API is hidden rather than deleted now.** It has three consumers: seven tests, the
bare triage page, and one table in Chapter 4. It carries **no authentication**, so a generated
client that could see it in the schema might be pointed at unauthenticated security telemetry by
accident. Hiding it removes that risk immediately at zero cost; deletion waits for the dashboard to
replace the bare page, so Chapter 4 is edited once rather than twice.

**Consequences.** `docs/DEPLOYMENT.md` records the topology and, deliberately, the gaps: no TLS in
the repository, `X-Forwarded-For` trusted only because nginx sets it, a single worker, no log
aggregation, secrets as environment variables. **Not yet verified:** the image has never been built
and the stack has never been started, because no Docker daemon was available on the development
machine. The compose file parses and both container entrypoints import cleanly; everything beyond
that is unproven until it runs on the VPS.

---

## 2026-08-03 — D25. Phase 1 trace timestamps are synthesised, and that is a threat to validity

> **Status in this repository:** the `adaptive_jailbreak` scenario described below is a recorded
> design and is **not implemented** here.

**Decision.** The `adaptive_jailbreak` scenario ingests the companion Phase 1 project's PAIR and
TAP traces and assigns timestamps,
because the traces carry none. Session ordering within a run is real; placement in time is modelled.

**Why timestamps have to be invented at all.** Verified rather than assumed: `RoundRecord` in the
Phase 1 harness has no time field, and every trace file's mtime is identical (a batch copy), so
neither the record nor the filesystem preserves timing. What survives is genuine and useful: the
`round` and `parent_round` fields give true ordering and TAP's branching structure, and each run's
filename carries its start time.

**What is modelled.** Sessions are **spread across a realistic attack campaign** rather than
compressed into the few hours the lab runs actually occupied. Faithful placement would put nearly
everything into one or two snapshot windows, which tells us little about temporal detection; the
spread makes the cross-session correlation claim (DESIGN.md §10) testable. The campaign parameters
live in `config/default.yaml` so the assumption is visible and tunable rather than buried.

**The honest cost.** The detectors are sensitive to timing, and the temporal placement is therefore
partly an artifact of our modelling rather than of the attack. **This must be reported as a threat
to validity in the same breath as any result derived from it**, and the scenario must never be
described as "real traces" without the qualifier "with synthesised timing".

**What remains genuinely real, and why it still matters.** The prompts, the detector decisions, the
model responses, the judge scores, the round structure and the blocked-then-successful pattern are
all real, labelled, and produced by an actual adaptive attack against actual detectors. That is a
strictly stronger evidence base than fully synthetic traffic, which is GraphGuard's largest
documented limitation. The fix at source — re-running Phase 1 with timestamps emitted — is a
one-field change to `RoundRecord` and belongs in future work.

---

## OPEN — decisions still to make

| # | Question | Deadline | Notes |
|---|---|---|---|
| ~~O2~~ | ~~Cytoscape.js vs sigma.js; React vs T3~~ | — | **CLOSED: React + TypeScript + Vite + Tailwind. Renderer superseded by D26 — the map is `d3-force-3d` plus a hand-written canvas projection, not Cytoscape.js.** Settled by measurement — snapshots hold 247–304 nodes and 922–1335 edges, so sigma.js's WebGL advantage (which starts paying at ~10k nodes) buys nothing, while Cytoscape's layout algorithms and typed-node styling matter at this size. T3 rejected: SSR is pointless for a single-page analyst console, and tRPC's typesafety is wasted against a Python backend that already emits an OpenAPI schema. |
| ~~O3~~ | ~~In-memory PyG only, or add Neo4j~~ | — | **CLOSED by D19: in-memory, no graph database.** |
| ~~O5~~ | ~~Which Phase 1 trace export format lands first~~ | — | **Format decided by D25: replayed JSONL**; the harness is never run live. The ingestion itself is not implemented in this repository. |
| O8 | Whether the **network/auth telemetry layer** (Stretch tier) is worth building | ~3 weeks in | Would make the cross-layer attack chain (DESIGN.md §7) span external → internal identity → lateral movement, which is a strictly better demo. Decide with real information, not now. |

---

## 2026-08-15 — D26. The network map is a hand-written canvas renderer over a 3-D force layout, not Cytoscape.js

**Decision.** The dashboard renders the network map with `d3-force-3d` for layout and a hand-written
canvas 2D projection for drawing. **Cytoscape.js is not used and is not a dependency.** This
supersedes the resolution recorded against open item O2, which named Cytoscape.js as the renderer.

**Why the earlier answer stopped being right.** O2 was settled before the map was built, on the
reasoning that at 247–304 nodes Cytoscape's layout algorithms and typed-node styling would matter
while sigma.js's WebGL advantage would not. The first half of that turned out not to survive
contact with the requirement. The map GraphGuard actually needs is **three-dimensional and
orbitable**, because the analyst question it answers — which entities sit near which, and where the
flagged ones cluster — is easier to read when the estate can be turned. Cytoscape.js is a 2-D
library; using it would have meant either abandoning the depth cue or wrapping a second engine
around it. A force simulation in three dimensions plus a projection is roughly two hundred lines,
and it removes a dependency rather than adding one.

The measurement that closed O2 still holds and still argues against WebGL: at a few hundred
vertices and around a thousand edges, canvas 2D holds frame rate comfortably, so a 3-D engine would
have been paid for and not used.

**Consequences.** `DESIGN.md` §11.3 and `docs/ROADMAP.md` P10 corrected in the same commit. The
motion discipline of DESIGN.md §11.2 is implemented in that renderer directly — only flagged nodes
pulse, the healthy bulk is drawn smaller and dimmer, and `prefers-reduced-motion` disables the pulse
— which is easier to guarantee in code we own than to configure in a library. Charts are likewise
hand-written SVG rather than a charting library, for the same reason at smaller scale: two specific
shapes, each of which a general library would be configured down to.

---

## 2026-08-15 — D27. Every reported figure must be regenerated from the current default configuration before submission

**Decision.** The result figures in `docs/assets/figures/` are treated as build artefacts of the
current `config/default.yaml`, and regenerating them is a release step rather than a one-off.

**Why — a defect found, not a policy invented.** The committed figures were generated at commit
`0cda81c` (2026-07-25). The HTTP telemetry layer and its two scenarios landed the next day in
`a7c65cc` and were enabled by default. The figures were never regenerated. Re-running
`graphguard figures` against the repository as it stands therefore produces a **different** result
set from the one Chapter 5 quotes: the detector × scenario matrix gains `credential_stuffing` and
`external_to_internal` columns that the report does not discuss, several off-diagonal cells move
substantially, and the attack-loudness sweep changes character.

Two distinct problems were behind that, and they need separating:

1. **Staleness.** Chapter 5 reports `\num{204748}` events, 141,030 benign; the current default
   configuration produces **220,122** events, 154,805 benign, 65,317 attack, across five labelled
   scenarios rather than three. Chapter 3 already quotes "roughly 220,000", so the report
   contradicted itself.
2. **A real bug in the loudness sweep.** `loudness_sweep()` disables `coordinated_ring` and
   `slow_group_drift` to isolate the stolen key — its comment states that intent explicitly — but
   was never updated to disable the two HTTP scenarios added afterwards. Its "isolated" runs are
   therefore not isolated, and the figure understates the graph detector.

**Measured, on the current default configuration.** Average precision on `stolen_key` across the
sweep, with the isolation list corrected:

| method | 1.5× | 2× | 4× | 8× |
|---|---|---|---|---|
| Graph: node-outlier (egonet) | **0.708** | **0.655** | **0.743** | 0.738 |
| Flat log: volume threshold | 0.124 | 0.281 | 0.621 | **0.874** |
| Flat log: error-rate threshold | 0.200 | 0.313 | 0.471 | 0.520 |

The qualitative claim the thesis rests on **survives and is arguably cleaner**: the graph detector is
flat across the range while the volume rule is entirely determined by attack loudness, and the two
now visibly cross over. What changes is the level — ≈0.71 rather than the ≈0.86 currently printed —
so the numbers in Chapter 5, the abstract and the general conclusion must be restated, not the
argument.

**Consequences — resolved the same day.** `isolate_scenario()` now disables every scenario except
the one under study, enumerating them from the configuration model rather than from a hand-written
list, so a scenario added later is excluded automatically instead of silently corrupting the figure.
Three regression tests pin that property, including one asserting the helper rejects an unknown
scenario name rather than disabling everything and sweeping an empty dataset. All five figures were
regenerated and Chapter 5, the abstract, the résumé and the general conclusion were restated against
them.

The scenario matrix now carries the two gateway columns. `external_to_internal` is reported (the
egonet detector reaches 1.000 average precision on it against 0.011 for the best flat rule);
`credential_stuffing` renders as *n/a* in every cell and the figure now says so explicitly, because
the scenario is the no-breach control and contributes no positive observation to a grid of
credential vertices. An unannotated blank column read as a rendering fault rather than as the
finding it is.

**What changed in the reported results.** The loudness sweep's graph line moves from ≈0.86 to
≈0.71 and now visibly crosses the volume rule between 4× and 8×, which is a stronger figure than a
flat line that never meets its competitor. The two relational scenarios are essentially unchanged
(ring 1.000 vs 0.222; drift 0.622 vs 0.145). The one genuinely less flattering change is that the
egonet detector now scores 0.793 on the coordinated ring and 0.483 — below chance — on slow group
drift; both are reported.

---

## 2026-08-20 — D32. The dashboard gets a design system, and colour has exactly one source

**Decision.** The console gains a component layer between the CSS tokens and the pages (page frame,
fields, table, skeletons, one chip control), every colour resolves from `styles.css`, and
**Recharts** replaces the hand-rolled SVG charts. D26 stands: the network map is still a
hand-written canvas renderer over a 3-D force layout, and no graph library is introduced.

**Why colour needed one source.** Two separate mechanisms were duplicating the palette by hand.
The donut segments were hard-coded hexes at the call site and did not flip with the theme. More
subtly, a `<canvas>` cannot inherit a custom property: assigning `var(--color-critical)` to
`strokeStyle` is not a colour, it is a silent parse failure that paints black, so the map had grown
its own copies of the severity ramp, the accent, the selection ring and five entity hues in two
files that had to be kept in step with the stylesheet manually. Those hues now live in `styles.css`
and are resolved once to literals, driven by a `MutationObserver` on `data-theme` rather than by the
theme context, because `ThemeProvider` writes that attribute from an effect and effects run
children-first, so a consumer re-resolving on the context value reads the previous theme's tokens
and never corrects itself.

**Why this mattered for the report specifically.** Six of the Chapter 4 figures are light-mode
captures, so every dark-only literal in the interface was a defect that gets printed and graded.

**Why Recharts.** Its marks take SVG presentation attributes, which are parsed as style
declarations, so `fill="var(--color-accent)"` resolves like any other custom property and a chart
follows the theme with no colour plumbing in the component. It also animates in JavaScript, so the
global `prefers-reduced-motion` rule in CSS does not reach it and is honoured explicitly instead.

**What the interface was previously dropping on the floor.** The volume chart discarded `by_layer`
while downsampling, which threw away the one thing the overview figure should show. The
explainability panel scaled each factor by `Math.abs(deviation) * 12`, a magic constant with no
axis or reference point, in the panel an examiner is most likely to interrogate; `peer_median` was
in the payload all along. The chain narrative, the ATLAS/OWASP tags and the analyst notes were all
fetched and never rendered, and `GraphLink.weight` was on the wire and ignored.

**Consequences.** `scripts/capture_screens.py` makes the figures reproducible as D27 requires,
pinning viewport, device pixel ratio, theme, reduced motion and which alert the detail figure shows,
and driving Chrome over the DevTools Protocol with the websockets library the project already has
rather than adding Playwright to a repository whose analytics packages carry no frontend tooling.
The script asserts nothing, so it is a capture tool and not a test; Chapter 4 says so. Chapter 4's
stack table gains Recharts, and its dashboard section now describes what the console actually
renders. Report at 122 pages, still zero undefined references.

---

## 2026-08-23 — D35. The chain search is event-resolution by design, and alerts now carry real framework tags

**Decision.** Two audit findings resolved in opposite directions, deliberately.

*The cross-layer search stays on the event stream, and the report now says so.* The pre-defence
audit found the report describing `find_attack_chains` as "a path search through the graph" when
the function never touches a snapshot: it joins the two layers' events on the address identity and
orders the stages. Rewriting the search as a snapshot traversal was considered and rejected: the
discriminating evidence (forty blocked logins within one hour; a first credential use hours after
a burst) sits below the one-day snapshot resolution, and snapshot edges carry neither outcomes nor
sub-window timing, so a faithful traversal would require outcome-qualified edge types and
sub-window timestamps — a schema redesign, days before submission, that risks the measured
one-chain/zero-false result for no evidential gain. Instead the module docstring, Chapter 1 §1.6,
the gap statement and Chapter 3 §3.4 now state the actual mechanism: the graph supplies the
canonical shared address identity (D7) and the corroborating detector verdict; the search runs at
the resolution the evidence requires. This is the defensible answer at a viva, and it is true.

*Framework tagging went the other way: implemented rather than deleted.* The report promised
alerts tagged against MITRE ATLAS and the OWASP LLM Top 10; persisted alerts always carried
`tags=[]`, and `correlation.tag_frameworks` was parsed and read by nothing. A new
`correlation/tags.py` maps each detector family to the tactics `docs/THREAT_MODEL.md` already
assigns (node-outlier → Valid Accounts + LLM10; abnormal-subgraph → Resource Development + LLM10;
community-drift → Discovery), `platform/runs.py` persists them filtered by `tag_frameworks`, and
the config default now enables both frameworks, so the YAML value stopped being decorative. A test
pins persisted tags to the threat-model mapping. Flat-log baselines are untagged on purpose: they
exist for comparison and never reach the queue.

**Consequences.** Suite at 327. The alert screenshots must be recaptured from a fresh run before
they show tags; the existing `devdata.db` run predates them.

---

## 2026-08-29 — D38. The dashboard ships inside the nginx image, and Jenkins auto-deploys `main` by polling

**Decision.** The `dashboard-dist` named volume is gone. `deploy/Dockerfile.nginx` builds the SPA
in a Node stage and copies the dist into the nginx image; `docker compose build` now produces both
images. The Jenkins job gains `pollSCM('H/2 * * * *')`, `RUN_DEPLOY` defaults to on, and the deploy
stage's branch guard reads `GIT_BRANCH` instead of using `branch 'main'`. Together these turn D24's
pipeline into actual continuous deployment: a push to `main` reaches the VPS within minutes, gated
by the re-run quality gates and the full-stack smoke test.

**Why the volume had to go.** It had no producer — nothing in the repository ever populated it, so
`docker compose up` served the API and a 404 for the UI. Worse, a named volume keeps whatever it is
first given, so even a one-off manual population would have pinned browsers to a stale bundle
forever. Baking the dist into the image makes a deploy atomic: nginx serves the old bundle or the
new one, never a mix. The smoke test now asserts `/` returns the SPA, which is the check that would
have caught the empty volume.

**Why polling, not a webhook.** A webhook requires the Jenkins controller to accept connections
from the internet; polling lets it stay bound to localhost on the same VPS it deploys to, reachable
only over an SSH tunnel. Two minutes of latency is irrelevant for this project; an exposed CI
controller on a security product's host is not.

**Why `branch 'main'` was wrong.** That condition only evaluates in a multibranch pipeline, where
`BRANCH_NAME` is set. In the plain pipeline job this deployment uses, `checkout scm` sets
`GIT_BRANCH` to `origin/main` and `branch` would have skipped the deploy stage silently on every
build — an auto-deploy pipeline that never deploys.

**Consequences.** The Jenkins agent no longer needs Node. `docs/DEPLOYMENT.md` §4 now documents the
container-aware TLS procedure (`certbot certonly --standalone` plus an untracked
`docker-compose.override.yml`), replacing `certbot --nginx`, which cannot rewrite a config inside a
container. First-run caveat: declarative triggers register on a job's first build, so the job must
be run manually once after creation.

---

## 2026-08-29 — D39. The assistant can run a language-model agent — feature-flagged off, tools limited to the platform's own API

**Decision.** `platform/assistant.py` adds an agent mode to the dashboard's assistant panel: a
Claude model (default `claude-opus-5`, Anthropic Python SDK, streaming) whose only capabilities
are nine tools wrapping this platform's own service layer — queue queries, alert drill-down,
triage transitions, notes, runs, chains, stats, and a client-side page-navigation request. The
browser receives everything as server-sent events: text deltas, a summarized reasoning stream,
every tool call with its input and result, per-exchange token usage. The panel's local
regex-over-loaded-data answerer remains the fallback and the default.

**Why not Claude Code, Codex or an agent gateway as the backend.** Those harnesses' power *is*
shell and filesystem access; mounted behind a web login on a security product they are remote code
execution by design. The embeddable form of the same idea is the Messages API with a closed tool
set: the loop lives in our process, and the model can do exactly what the signed-in analyst can do
— tools execute under the caller's ``User`` through the same functions the HTTP routes use, so
RBAC and the triage state machine bind the model identically.

**Why off by default.** Two reasons, either sufficient. The report describes the platform without
a generative assistant, and the deployed default must match the reported system. And enabling it
sends alert metadata to an external API — a data-boundary change that must be a deliberate
per-deployment decision (`GRAPHGUARD_ASSISTANT_ENABLED` + `ANTHROPIC_API_KEY`), not a default.

**Boundary discipline.** This is not a detector and must never become one (DESIGN.md §14): it
consumes detection results and produces no scores. D17 holds — the analytics packages are
untouched; the `anthropic` dependency is an optional extra (`graphguard[assistant]`) imported
lazily, so the CLI-reproducible analytics path gains no LLM dependency. Prompt-injection stance:
telemetry contains attacker-chosen strings by definition, so the system prompt pins all tool
output as untrusted quoted data, and the tool loop is capped at eight model turns.

**Verified.** Ten tests cover the gating (401/404/422), tool dispatch and error-as-data behaviour,
the scripted agent loop (tool execution, usage accounting, tool-result replay), and the
dead-client path. The full suite is 339 green; the live stream was exercised end-to-end in the
browser up to the missing-credential error event, which renders in the panel instead of killing
the stream.

**Update, same day.** The assistant is now provider-pluggable: `GRAPHGUARD_ASSISTANT_PROVIDER`
selects the native Anthropic driver or an OpenAI-compatible chat-completions driver
(`GRAPHGUARD_ASSISTANT_BASE_URL` pointed at OpenAI or OpenRouter, key in `OPENAI_API_KEY`). Both
drivers emit the identical event stream, so the dashboard is provider-blind; the one visible
difference is that the chat-completions surface exposes no reasoning summaries, so that stream is
absent on the OpenAI path. The tool set, RBAC binding, turn cap and prompt-injection stance are
shared code. Two more scripted-fake tests cover the OpenAI dialect (fragmented tool-call streaming,
`role: "tool"` replay); the suite is 341 green.

**Update, 2026-08-31.** The report now carries one deliberate mention: a closing paragraph in
Chapter 4's dashboard subsection ("The assistant answers from the run, and can optionally act")
describing both modes exactly as built — agent mode disabled by default, inactive in every reported
experiment, closed toolset under the analyst's role, excluded from validation because a hosted
model is not reproducible from a seed. This supersedes the "report does not mention it" stance
above: the author judged a brief honest mention cheaper than a jury asking why a visible feature is
undocumented. The general-conclusion rewrite of 2026-08-31 dropped the earlier future-work bullet,
so the Chapter 4 paragraph is the report's single mention; do not remove it as a D39 violation.

---

## 2026-08-31 — D41. Stored configuration revisions load fault-tolerantly; the settings form is generated from the schema

**The bug this starts from.** Opening the settings page returned a 500, and so did dispatching a
run. Revision 4 — saved 2026-08-15, still marked active — carried
`correlation.explanation.subgraph_radius`, a field removed from `GraphGuardConfig` in `fa1208b`.
Because the model sets `extra="forbid"` (D4: a typo must not leave a detector silently on its
defaults), the stored payload stopped validating, and every caller of
`config_store.effective_config` raised. The two rules were individually right and collided: a
revision is a *frozen payload*, and the schema it was frozen against moves.

**Decision.** Loading a stored revision is fault-tolerant; saving one stays strict.
`config_store.effective_state` returns the configuration in force *plus its provenance*, and on a
validation failure it falls back to `config/default.yaml` and reports which revision is stale and
which keys are at fault. `GET /api/v1/config` carries that report; the settings page shows it with
the action that resolves it. Activating an unparseable revision is refused with a 409 rather than
"succeeding" into a silent fallback, and each row in the revision history states whether it can
still be used.

**Why not fail closed.** Refusing to serve would protect a stale artifact by taking down detection
and the whole administrative surface — the state this was found in. Falling back to a
known-good file and saying so loudly keeps the platform running while making the problem
actionable. A regression test in `tests/test_platform_admin.py` reproduces the exact drift.

**Decision.** The detection form is generated from `GET /api/v1/config/schema`, which serves
`GraphGuardConfig.model_json_schema()`. This supersedes the reasoning recorded in the old settings
page — that a hand-maintained form would silently fall behind a model that grows as detectors are
added, so the surface had to be a raw JSON textarea. The premise is right; the raw editor is not
the only conclusion available. Generating from the schema the server validates against makes drift
impossible rather than merely discouraged: a new detector parameter appears in the form the moment
it exists on the model. The JSON editor is kept as a second tab, because a generated form is a poor
way to restructure a nested object and pasting a known-good configuration is a real workflow.

**Scope of the generator.** It renders what this model uses — nested objects, `$ref` into `$defs`,
`T | None`, bounded scalars, enums, booleans, scalar arrays — and falls through to a JSON field for
any subtree it cannot render, so an unsupported construct costs one raw editor rather than an
unreachable parameter. No validation is duplicated in the browser; the server remains the only
place the rules live.

---

## 2026-08-31 — D42. Three chart-series tokens, validated rather than chosen by eye

**Decision.** `--color-series-1..3` join the token set in all three scopes (`@theme`, the light
override, `.panel-brand`). They are for charts plotting two or three *comparable* series and are
distinct from the entity hues, which name a node type, and from severity, which names a state — a
chart that borrowed either would make "series 2" read as "high severity".

**Why only three.** The set was run through colour-vision and contrast checks against this theme's
surfaces rather than picked visually: worst adjacent pair ΔE 9.4 under deuteranopia and 20.9 with
normal vision on dark, 9.2 and 24.0 on light, all three above 3:1 against the panel. A fourth slot
failed — the next hue landed ΔE 1.9 from the blue under protanopia — so a chart needing a fourth
series needs small multiples instead. The light aqua sits at 2.82:1, just under the contrast bar,
which is why every series using it is also directly labelled rather than identified by colour alone.

**Consequence for the new figures.** The run-detail metric charts are single-hue ranked bars with a
reference rule (0.50 for ROC-AUC, the positive rate for average precision) because they encode
magnitude within one measure; only the precision/recall-at-k chart needs two series, and its two
lines differ by dash pattern as well as hue.

---

## 2026-09-01 — D43. Relations are selectable on the map, and toned by *inherited* risk

**Decision.** The network map's edges are now first-class: hoverable, clickable, and opened in a
relation inspector that names the two entities, the action type, how many interactions the edge
represents, how many of those failed, and the period they span. A relation filter narrows the map
to one interaction type — `used_from` being the one that crosses telemetry layers.

**A real bug found on the way.** Every link on the wire carried `weight: 1`. `build_network_graph`
was counting `edge_index` *columns*, and the builder has already collapsed parallel edges by the
time it assembles the tensor, so the count was structurally always one. The map's "a heavier
relation is a thicker line" rule was therefore encoding a constant. Weight now comes from the
events themselves and spans 1 to ~2,900, and the outcome breakdown per edge is computed alongside
it. The analytics packages are untouched: this is presentation metadata assembled in `platform/`,
per D17.

**The claim boundary, which is the whole reason this entry exists.** No detector in this system
scores an edge. Node-outlier, abnormal-subgraph and community-drift all emit per-*entity* signals.
Painting a line red is read by any viewer as "this interaction was malicious", and inventing a
per-edge anomaly score in the browser to justify that would be exactly the kind of unsupported
claim the pre-defence audit already caught five of.

So an edge's tone is **inherited and labelled as inherited**: the higher of its two endpoints'
risks. Every surface that draws it says so — the legend, the inspector, the ranked list and the
help article. The one thing measured *per edge* is the failure ratio, and it is presented in its
own panel rather than folded into the tone, because a blocked request is a fact about an event
while an alert is a detector's opinion about an entity.

**Why reach is a second axis.** In a representative run 45 of 304 entities carry an alert, and
because those are the high-degree ones, **254 of 903 edges touch one** — over a quarter of the
graph. Toning all of them by risk produced a red hairball in which the **nine** edges with *both*
ends flagged were invisible. Edges are therefore drawn in three tiers: both ends flagged (full
weight), one end flagged (a tint), neither (faint mesh). This is DESIGN.md §11.2's
pulse-the-alert-not-the-world discipline applied to edges, and it is what makes the map read as
"green mesh, red threads" rather than as a warning light.

**Accessibility.** A green-to-red ramp is the worst possible pairing to put in front of a
colour-blind analyst, so risk drives line *width* and *opacity* as well as hue, the legend keys
vary in thickness as well as colour, and the same edges are reachable from a ranked text list
beside the canvas — which also solves the ordinary problem that a one-pixel line in a projected
3-D layout is hard to hit.

---

## 2026-09-01 — D45. A public landing page in front of the sign-in form

**Decision.** `/` is now a marketing landing page for anyone without a session, and the sign-in form
moves to `/login`. The unauthenticated app renders a small router rather than the form directly;
anything else redirects to `/`, so a stale bookmark into the console lands on the pitch and not on a
bare password box. The page carries the brand: the mark (`components/Logo.tsx`, also now the
favicon and the sidebar mark — the favicon had been an unrelated template glyph and the
sidebar a `Network` icon labelled "placeholder"), the poster headline *See
Threats. Map Risk. Stop Drift.*, and an animated WebGL2 ray field behind the hero.

**Why.** The console had no front door. A visitor who was not already an analyst met a password box
with no statement of what the product is, which is the wrong artefact to put in a defence
demonstration or in front of anyone the project is being shown to.

**What it says, and the constraint on it.** Every figure quoted is a measured result from the
evaluation harness at seed 1337, and the page concedes the cell the graph loses: the flat volume
rule takes the stolen-key scenario 0.875 to 0.864. The limitations section states synthetic-only
validation, unsupervised operation, and that scores inform an analyst rather than blocking anything.
The marketing surface and the report have to agree, or the jury finds the gap.

**The mark (2026-09-02).** The supplied artwork is a four-pointed star cut out of a pinwheel
frame. It was not redrawn by eye: the source raster was traced at sub-pixel precision and the
geometry fitted, which turned out to be exact enough to reconstruct rather than approximate.
The whole glyph is *one* radius — all eight curves, four on the outer silhouette and four
around the inner star, are circular arcs of the same radius (~1.007x the half width), each
fitting its traced contour to within 0.16 px on a 291 px render. It has 4-fold **rotational**
symmetry and **no mirror symmetry** (0.965 for each of the 90/180/270 rotations against 0.74
for every reflection), so it must never be flipped — mirroring it yields a different logo.
The four straight edges lie on the bounding square's sides, each running from just beside the
side's midpoint to 42% along; that offset is consistent across all four blades and snapping it
to the midpoint measurably worsens the fit, so it is kept. The path was then refined by
rendering and maximising overlap against the source coverage field, settling at IoU 0.9625 —
the ceiling for this perimeter-to-area ratio at that resolution, with the residual a balanced
half-pixel hairline. It ships as a 298-character path in `currentColor`, so it takes white on
the navy sidebar and landing page and ink on a light workspace. Two caveats worth knowing:
the glyph bleeds to all four edges of its box with no built-in padding, so the lockup draws it
at 0.78x to sit at ~1.5x the wordmark's cap height rather than 1.9x; and the four webs joining
the star to the frame are 7.6% of the glyph width, which is a soft hairline below ~20 px at
DPR 1. That thinness is a property of the artwork, not of the trace.

**Implementation notes.** The scroll behaviour follows agenticbeaver.com: past 10 px the bar's max
width *contracts* 1240 → 800, gains a glass fill and tightens its paddings, all on one 300 ms
`cubic-bezier(0.25,0.1,0.25,1)`. The reference drives that and its active-tab pill with
framer-motion; this uses a CSS `max-width` transition and a DOM-measured pill instead, because a
40 kB animation runtime for two effects is not a trade worth making. Smooth anchor scrolling is
`scroll-behavior: smooth` plus `scroll-margin-top`, not a scroll-hijacking library, so it degrades
correctly under `prefers-reduced-motion`.

**The hero background is React Bits' `ColorBends`** (`components/backgrounds/ColorBends.tsx`), the
effect that site runs behind its own hero. Its registry entry depends on `three`, and this port does
not: three is not doing 3-D work there — an `OrthographicCamera(-1,1,1,-1)` over a
`PlaneGeometry(2,2)` and a `ShaderMaterial` is a screen-filling quad with a fragment shader, and
~150 kB gzipped of scene graph to draw two triangles is not a trade worth making. **The fragment
shader is upstream's, byte for byte** (verified by diff), running on a WebGL1 context precisely
because the source is already GLSL ES 1.00 and therefore needs no translation that could drift.
An earlier pass used Meshh's `siderays`; that component and its runtime were deleted rather than
left as dead vendored code. **No new npm dependency was added for any of this.**

**Two props deviate from the reference, both measured rather than guessed.** React Bits' editor
shows `bandWidth={0.14}`, which renders as a flat wash on the published component —
`w = 1 - exp(-bw/exp(bw*m))` spans only 0.08–0.13 there, leaving the bands no contrast; their hero
runs a newer build that scales the prop differently, and 6 (the registry default) is what reproduces
the reference structure. Intensity is 0.9 rather than 1.3 because their headline sits in a quiet
column while this one is centred on the brightest part of the field. The colour is a single stop,
`#2f6bf0`, not a ramp: the shader accumulates stops additively, so three stops rendered ~3× the
reference brightness and clipped the band cores to white.

**Contrast was verified against the running shader, not assumed.** Sixteen frames across the full
animation cycle, measured over glyph pixels only: the accent-blue "Map Risk." bottomed out at 1.6:1
before the fix. Lightening that blue was rejected — it is the poster's — in favour of a radial scrim
under the copy, which keeps the palette exact and the corners at full brightness. Worst-frame
results now: headline lines 4.7:1 / 7.9:1 / 11.4:1 minimum against a 3:1 floor, hero lede 8.3:1 and
both buttons above 4.5:1, with zero sub-threshold pixels in any frame.

**The page is dark in both themes** and scoped by a `.landing` class that ignores `[data-theme]`,
because the artwork it is built around is composed for a dark field; a light variant would need a
second set of artwork. While the route is mounted it also flags the document root, so the body and
the UA-drawn surfaces stop painting the light canvas underneath it.

---

## 2026-09-01 — D46. The assistant gets a conversation surface of its own

**Decision.** `/assistant` is rebuilt as a conversation page rather than a wider copy of the
slide-over rail. Three changes carry it: a welcome state (product mark inside a lit orb, a heading,
and the mode statement) with the composer sitting *under* it; a multi-line composer where Enter
sends and Shift+Enter breaks the line, replacing the single-line `<input>`; and a page variant of
the shared transcript that drops the bubble around assistant replies, raises them to full-strength
ink and marks each with a small avatar. The slide-over panel is unchanged — it and the page still
render one `ChatMessages`, now switched by a `variant` prop that is layout only.

**Why.** The two surfaces have different jobs. The rail is a glance beside the work; the page is
where an analyst sits down with a run, and the questions worth asking an agent — "investigate X,
then check whether Y happened on the same address" — do not fit on one line of an `<input>` and
cannot be read back while being typed. The old page inherited the rail's 11 px dim type at three
times the column width, which is below the size the rest of the console uses for primary content.

**The orb, and why it is artwork rather than a spinner.** An agent turn opens with a tool call and
can run for tens of seconds; a static "Thinking…" is indistinguishable from a stalled stream. The
orb is a CSS-only rotating inset-shadow disc whose label animates letter by letter, so progress is
legible without polling anything, and it is the *same* element that marks the assistant in the
welcome state — the waiting state is recognisably the thing that was already on screen. It keeps
its own blues in both themes, like `.panel-brand`: it is lit artwork, and a light repaint would
have to invert the light source, which is a different drawing rather than a colour swap. Its label
is announced once through a polite live region, and the keyframes live in `styles.css` so the
existing `prefers-reduced-motion` rule cuts them; the ring carries a static box-shadow for exactly
that case, or the waiting state would vanish for the users who most need it legible.

**Two things not to undo.** The mode is stated in words — *Grounded* (answers read from the loaded
run, nothing generated) or *Agent · model* (acting as the analyst through the platform API) —
because which one is answering changes how much of the reply needs checking, and a coloured dot
does not say that. And the welcome block centres with `my-auto` rather than `items-center`:
cross-axis centring inside a scroll container makes the overflow above the child unreachable, so on
a short window the orb and heading would scroll off the top with no way back.

**No new dependency.** The spotlight on the suggestion cards writes `--gg-x`/`--gg-y` straight to
the element on pointer move, so hovering the grid costs no React render, and the drifting glow
behind the welcome state is one keyframe over a `color-mix` radial gradient.
