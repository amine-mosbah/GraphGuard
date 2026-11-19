# ROADMAP.md — 12-week timeline

> The original 12-week plan and what became of it. Status legend:
> ☐ not started · ◐ in progress · ☑ done · ⊘ deferred (with reason)

**Scope tiers (DESIGN.md §6):** Core = AI adapter + graph + 3 detectors + dashboard + synthetic
validation · Layer 2 = HTTP/gateway adapter + cross-layer demo · Stretch = network/auth adapter,
streaming.

---

## Week-by-week

| Wk | Focus | Deliverable | Tier | Status |
|---|---|---|---|---|
| 1 | Onboarding, schema review | Repo scaffold, canonical trace schema, config, design notes committed | Core | ☑ |
| 2–3 | Ingestion + preprocessing | AI-telemetry adapter (OTel `gen_ai.*`); synthetic benign generator; normalisation | Core | ☑ |
| 4–5 | Graph modelling + construction | Graph builder, node/edge registry, time-windowed snapshots | Core | ☑ |
| 6–7 | Anomaly detection | Node-outlier (PyGOD + classical baseline) → community drift → abnormal-subgraph | Core | ☑ |
| 8 | Validation + tuning | Attack scenarios injected; eval harness (P/R/AUC); log-vs-graph comparison | Core | ☑ |
| 9–10 | Dashboard | Graph explorer + timeline + triage queue + live status map | Core | ☑ |
| 11 | Integration + testing | Cross-layer correlation demo (Layer 2 if on track); end-to-end tests | Layer 2 | ☑ |
| 12 | Reporting | Technical report: methodology, results, limitations, deployment recommendations | — | ☑ |

## Platform tier (added 2026-08-03, D17–D25)

Not in the original 12-week plan: the project is built as a deployable single-tenant service, not
only as a research prototype. The dependency rule is one-way — `platform/` imports the analytics
packages, never the reverse — so the evaluation harness still runs with no infrastructure.

| # | Deliverable | Status |
|---|---|---|
| P1 | Deployment settings, kept separate from analytical config so secrets cannot reach saved runs | ☑ |
| P2 | PostgreSQL persistence, SQLAlchemy 2.0 + Alembic, portable to SQLite for tests | ☑ |
| P3 | JWT auth: argon2id, short access + rotating revocable refresh, analyst/admin RBAC | ☑ |
| P4 | Celery-backed runs, persisted alerts and chains, pre-aggregated timeline | ☑ |
| P5 | Analyst triage workflow with audit trail and incident export | ☑ |
| P6 | Docker Compose stack (nginx, api, worker, postgres, redis) | ◐ image builds and smoke-tests in CI; the full stack has not been run on a server |
| P7 | GitHub Actions: four gates + Postgres + migration drift + image build | ☑ |
| P8 | Jenkins deployment pipeline to the VPS | ☐ |
| P9 | Prometheus self-instrumentation; Grafana dashboards | ◐ `/metrics` done; Grafana after the analyst dashboard |
| P10 | React + TypeScript + Vite + Tailwind dashboard, 3-D force layout on canvas (D26) | ☑ done |

**Committed scope:** Core features 1–4 + interpretable explanations.
**Stretch:** full dashboard polish + streaming mode.

---

## Milestone 0 — the vertical slice (COMPLETE, 2026-07-24)

Decided in `DECISIONS.md` D5: build one runnable end-to-end path before widening. This compresses
the *skeleton* of Weeks 1–8 into a single working pipeline, then depth is added per week above.

| # | Step | Status |
|---|---|---|
| 1 | Repo scaffold, pinned 3.12 env, config, docs | ☑ |
| 2 | Canonical `TraceEvent` schema + `docs/SCHEMA.md` | ☑ |
| 3 | Synthetic benign generator (personas, diurnal shaping) | ☑ |
| 4 | Stolen-key scenario + ground-truth labelling | ☑ |
| 5 | Graph builder → PyG `HeteroData` + snapshots | ☑ |
| 6 | Node-outlier detector (PyGOD + egonet baseline) with explanations | ☑ |
| 7 | Eval harness + log-vs-graph comparison figure | ☑ |

**Milestone 0 result (2026-07-24):** the slice runs end to end via `graphguard evaluate`. Headline:
graph node-outlier holds AP ≈ 0.86 / P@10 = 1.00 at *every* attack loudness (1.5×–8×) while the
best flat-log baseline collapses from 0.87 to 0.12 as the attacker quiets down. DOMINANT underper-
forms the classical egonet baseline untuned — tracked for Weeks 6–7. See `docs/THREAT_MODEL.md`.

