# DESIGN.md — scope, concepts and conventions

> The design reference for GraphGuard. Code comments cite it by section number (`docs/DESIGN.md §3.2`).
> `ARCHITECTURE.md` expands §8, `SCHEMA.md` expands §9, `THREAT_MODEL.md` defines the scenarios, and
> `DECISIONS.md` records why each choice was made.

---

## 1. The problem

An AI platform emits large volumes of telemetry: API-call logs, session traces, HTTP requests,
authentication events. Malicious use — a stolen API key, a ring of coordinated fake accounts, an
external attacker who breaches and then pivots inward — is almost invisible line by line, because
each individual event looks benign. The suspicious part is the **pattern of relationships over
time**.

Keyword or SIEM rules and per-prompt content detectors miss this for three reasons:

1. They evaluate events **in isolation**, losing relational context.
2. They **reset per session or request**, so multi-round and cross-session behaviour is invisible.
3. They cannot express "this account stopped behaving like itself", "these twenty accounts move in
   unison", or "this external address became this internal identity".

## 2. The idea

Model the telemetry as a **graph**. Once the data is a graph, relationships are first-class and
computable: neighbourhoods, cluster density, temporal drift. Graph anomaly detection runs on that
structure.

The primary value of the graph is **computational** — relational anomalies become detectable. That
the same structure is naturally drawable, and powers the analyst dashboard, is the secondary
benefit. The graph is a data structure first and a picture second.

## 3. Core concepts

### 3.1 Heterogeneous temporal graph

- **Heterogeneous:** several node types and edge types in one graph. Nodes include `user`,
  `api_key`, `model`, `ip` and `endpoint`; edges include `call`, `error`, `login` and `request`.
  The registry in `src/graphguard/graph/model.py` is authoritative.
- **Temporal:** every edge carries a timestamp. The builder produces **time-windowed snapshots**,
  which is what makes drift and change detectable.

### 3.2 The three detectors

Written once, run on any telemetry layer.

1. **Node-outlier scoring** — a per-node score of how far a node deviates from comparable peers or
   from its own past ("impostor scoring"). Targets a **stolen API key**: new models, odd hours, a
   volume spike. Two implementations: a classical egonet baseline and PyGOD's DOMINANT.
2. **Community drift** — the shape of a *group* changing across snapshots. Targets slow compromise
   and misconfiguration, where **no single member** looks unusual.
3. **Abnormal-subgraph detection** — a small cluster acting in suspicious **unison**. Targets
   **coordinated abuse**: one actor driving many accounts.

A high score is not proof of malice; a legitimate power user can score high. Scores inform a human.

### 3.3 What the analyst sees

Not a bare flag. A **ranked list of alerts**; opening one shows the relevant **subgraph**, the
**timeline** of events, and a **plain-language reason**. Alerts carry MITRE ATLAS and OWASP LLM
Top 10 tags per detector family. The analyst then decides: false alarm or incident.

### 3.4 One-line mental model

Detectors score **individuals** (outliers), **group change** (drift) and **group coordination**
(subgraphs). The dashboard turns those scores into explained, ranked alerts for a human.

## 4. What is new, and what is not

- **Provenance-based intrusion detection** (KAIROS, Unicorn, ThreaTrace and others) has mature graph
  anomaly detection, but over operating-system audit logs.
- **LLM observability tools** (Langfuse, LangSmith, Helicone, MLflow) hold the right telemetry but
  inspect flat traces.
- **UEBA and fraud research** applies graph methods to enterprise and financial entities.
- **Commercial products** now build graphs over AI deployments for posture and reachability
  analysis, so "nobody builds a graph over AI security data" would be false.

GraphGuard's contribution is narrower: **unsupervised behavioural anomaly detection** over a
heterogeneous temporal graph of AI-platform telemetry, with an analyst-facing layer, validated
against flat-log baselines. `RELATED_WORK.md` has the literature map.

The name "GraphGuard" is a working prototype name and collides with unrelated published work; see
`RELATED_WORK.md`.

## 5. Features

1. **Pluggable telemetry adapters** on a canonical schema (OpenTelemetry GenAI `gen_ai.*` for the AI
   layer; access logs for the HTTP layer).
2. **Unified heterogeneous temporal graph** builder with time-windowed snapshots.
3. **Three-detector suite** with interpretable scoring, benchmarked against baselines.
4. **Cross-layer attack-chain correlation** (§7).
5. **Analyst dashboard**: overview, triage queue, network map, attack chains, investigations.
6. **Synthetic attack simulator and evaluation harness**: benign traffic plus labelled attack
   scenarios, per-scenario metrics, and the log-versus-graph comparison.

## 6. Scope tiers

- **Core:** AI-telemetry adapter, graph engine, the three detectors, dashboard, synthetic
  validation.
- **Layer 2:** HTTP/gateway adapter, external-attack scenarios, and the cross-layer correlation.
- **Stretch (not built):** an internal network/auth adapter, and a streaming mode.

Validation is **synthetic-first, always**: the design never assumes that production telemetry will
be available. Extra telemetry layers are context enrichment around the AI-platform core, not an
attempt at a general network monitor.

## 7. Cross-layer attack-chain correlation

The showcase scenario:

1. An external address hammers the authentication endpoint (credential stuffing, HTTP layer).
2. Later, a **valid** API key is used from that same address.
3. That credential then behaves unlike itself (AI layer).

Each event alone is unremarkable. Together they form a chain from external breach to internal
abuse. Because both layers share the address as an identity, the correlator can join them: it
reconstructs the chain **at event resolution**, joining the two layers' events on the shared
address rather than traversing a snapshot graph (`DECISIONS.md` D35). A brute-force-without-breach
scenario is the control and must yield no chain.

## 8. Architecture

### 8.1 Component layout

Ingestion adapters → graph builder → detector suite → correlation → API → dashboard, with the
simulator feeding the adapters and the evaluation harness measuring the result. The diagram and
module map are in `ARCHITECTURE.md`.

### 8.2 Repository structure

```
config/            runtime configuration
docs/              design, architecture, schema, decisions, threat model, related work
src/graphguard/
  schema/          canonical TraceEvent
  ingestion/       adapters: raw telemetry -> TraceEvent
  graph/           node/edge registry, builder, snapshots
  detection/       node-outlier, community drift, abnormal-subgraph
  correlation/     cross-layer chain reconstruction, framework tags
  simulator/       benign traffic and labelled attack scenarios
  eval/            metrics, baselines, figures
  api/             FastAPI application
  platform/        persistence, auth, jobs, triage workflow
dashboard/         React frontend
migrations/        Alembic migrations
tests/             pytest suite
```

### 8.3 Data-flow contract

- Adapters emit **only** canonical `TraceEvent`s. Nothing downstream parses a raw log format.
- The graph builder is the **only** component that knows graph internals; detectors consume a
  documented interface (PyG `HeteroData` plus a snapshot list).
- Every detector returns **explainable signals** — score, contributing nodes and edges, and a
  human-readable reason — never a bare number.
- `platform/` imports the analytics packages; they never import it. The evaluation therefore runs
  from the CLI with no database, no Redis and no accounts.

## 9. Canonical telemetry schema

Every component speaks one event type, `TraceEvent`: who (`actor_id`, `actor_type`), what
(`target_id`, `target_type`), the action and outcome, a timestamp, the telemetry layer, a
`source_ip` that lets layers share vertices, free-form attributes, and a ground-truth `label` used
only in evaluation. Node identity is `(type, id)`. The full specification is in `SCHEMA.md`.

## 10. Relationship to content-level jailbreak detection

Per-prompt detectors work at the **content** level: is this prompt an attack? GraphGuard works at
the **behavioural and relational** level: how does this account behave across sessions, and how
does it relate to others? The two are complementary. An adaptive attacker who reframes a request
until a detector passes it leaves a temporal shape that a per-prompt detector cannot see, because
it resets each round.

The schema is designed so that labelled session traces from such a study can be replayed through
the AI-telemetry adapter as a scenario. **That ingestion is not implemented in this repository.**
The boundary is deliberate either way: GraphGuard does not run attacks, and does not score prompt
content.

## 11. Dashboard

### 11.1 Layout

Coordinated views: a triage queue of ranked alerts with drill-down, a network map, a timeline, and
an attack-chain view. Selecting an alert highlights it across them.

### 11.2 Network map and motion discipline

Node status is shown by colour **and** by a shape or icon cue, never by colour alone.

- **Pulse the alert, not the world.** Only flagged nodes animate; the healthy bulk is rendered
  quietly.
- Animate only `transform` and `opacity`.
- Honour `prefers-reduced-motion`.

### 11.3 Technology

FastAPI backend; React, TypeScript, Vite and Tailwind frontend; the network map is a
`d3-force-3d` layout drawn on canvas (`DECISIONS.md` D26). The graph is held in memory with
PyTorch Geometric; no graph database is used (D19).

## 12. Related work

See `RELATED_WORK.md`: provenance-based intrusion detection, graph anomaly detection algorithms and
tooling, AI/LLM telemetry and observability, UEBA and coordinated-abuse detection, and visual
analytics for security.

## 13. Coding conventions and stack

- **Python 3.12**, type hints throughout, `pydantic` for the schema and configuration.
- Detectors wrap **PyGOD** and **PyTorch Geometric**; classical baselines use NetworkX, NumPy and
  scikit-learn. Published detectors are wrapped, not reimplemented.
- **FastAPI** for the API; **pytest** for tests; **ruff**, **black** and **mypy** as gates.
- Every module docstring states its role and its inputs and outputs in terms of the schema.
- **Explainability is mandatory output** from every detector.
- **Reproducibility:** fixed random seeds in the simulator and evaluation; each run saves its
  configuration beside its results.

## 14. Guardrails

Things this project deliberately does not do:

- Become a general network intrusion detection system or SIEM.
- Run jailbreak attacks itself.
- Score prompt content or use an LLM judge as a detector; the detectors stay relational.
- Animate the whole network map.
- Present the graph as "visualization"; it is a data structure first.
- Auto-block on an anomaly score.
- Depend on production telemetry ever being available.
