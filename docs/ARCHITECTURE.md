# ARCHITECTURE.md

> Expanded from DESIGN.md §8: the pipeline, the contracts between its stages, and the module map.

---

## 1. Pipeline

```
raw telemetry (per source)
        │
        ▼
[ ingestion adapters ]  ── normalize → canonical TraceEvent stream
        │
        ▼
[ graph builder ]  ── nodes/edges + time-windowed snapshots (heterogeneous temporal graph)
        │
        ├──────────────┬───────────────────┐
        ▼              ▼                   ▼
[ node-outlier ]  [ community drift ]  [ abnormal-subgraph ]     ← detector suite (PyGOD-backed)
        │              │                   │
        └──────────────┴───────────────────┘
        ▼
[ correlation + scoring ]  ── merge signals, cross-layer path search, rank, explain, tag
        │
        ▼
[ API (FastAPI) ]  ── serves graph slices, alerts, explanations
        │
        ▼
[ dashboard ]  ── graph explorer + timeline + triage queue + live status map

[ simulator ]   ── generates benign + labelled attack telemetry into the adapters
[ eval harness ]── runs simulator → pipeline → metrics, log-vs-graph comparison
```

## 2. Contracts between stages

These three rules are what keep the system extensible to new telemetry layers without touching
downstream code. They are enforced by module boundaries, not convention.

1. **Adapters emit only canonical `TraceEvent`s.** Nothing downstream ever parses a raw log
   format. Adding a telemetry layer means adding an adapter, not editing the graph builder or the
   detectors.
2. **Only the graph builder knows graph internals.** Detectors consume a documented interface
   (PyG `HeteroData` plus a snapshot list), never raw structures.
3. **Every detector returns explainable signals** — score *plus* contributing nodes/edges *plus* a
   human-readable reason. Explanation is a first-class output, not a post-hoc add-on.

## 3. Why one graph instead of one pipeline per telemetry layer

The layers share vertices. A `source_ip` observed in an HTTP request and a `source_ip` observed on
an AI-platform API call are the **same node**. That single fact is what turns two unrelated
incidents into one traversable **path** (DESIGN.md §7) and is why the detectors are written once
and run on any layer.

## 4. Module map

| Module | Role | Input | Output |
|---|---|---|---|
| `schema/` | Canonical `TraceEvent` definition + (de)serialisation | — | typed events |
| `ingestion/` | `Adapter` ABC + per-source adapters | raw records | `TraceEvent` stream |
| `graph/` | Node/edge type registry, builder, snapshots | `TraceEvent` stream | `HeteroData` + snapshots |
| `detection/` | `Detector` ABC + three detectors | graph/snapshots | explainable signals |
| `correlation/` | Merge, rank, cross-layer path search, explain, tag | signals | ranked alerts |
| `api/` | FastAPI service | alerts + graph | JSON for the dashboard |
| `platform/` | Persistence, auth and RBAC, Celery-run pipeline jobs, triage workflow, `/api/v1`, metrics. Imports the analytics packages; they never import it (D17) | HTTP requests | persisted runs, alerts, chains |
| `simulator/` | Benign generator + labelled scenarios | config | `TraceEvent` stream + labels |
| `eval/` | Metrics + log-vs-graph comparison | scores + labels | metrics, figures |

## 5. Runtime flows

- **F1 — ingestion → graph update**
- **F2 — detection run → scored signal → ranked alert → explanation**
- **F3 — analyst opens an alert → API serves subgraph + timeline + why**
- **F4 — cross-layer correlation: external event linked to internal identity**

## 6. Deployment

The analytics packages need no infrastructure: `graphguard evaluate` runs from the CLI with no
database, no Redis and no accounts. The deployable service adds a FastAPI process, a Celery worker,
PostgreSQL and Redis behind nginx (`docker-compose.yml`); the graph itself stays in memory (PyG)
and no graph database is used (`DECISIONS.md` D19). See `DEPLOYMENT.md`.
