# RELATED_WORK.md — literature map

> Expanded from DESIGN.md §12. Five research families plus the name-collision citations.
> This is a map of the literature, not a full bibliography; the thesis cited in the README carries
> the verified reference list.

---

## The gap this project targets

| Family | Has | Lacks |
|---|---|---|
| Provenance-based IDS | mature graph anomaly detection, attack reconstruction | targets **OS audit logs**, not AI-platform telemetry |
| LLM observability | the **right telemetry** (users, sessions, prompts, models, tools) | flat trace inspection, **zero graph analytics** |
| UEBA / fraud | graph-based coordinated-abuse detection | **enterprise/financial** entities, not AI platforms |

**→ Nobody has published a system that models AI-platform telemetry as a heterogeneous temporal
graph, runs graph anomaly detection on it, and adds an analyst-facing visual layer — extensible to
external (HTTP/API) and internal (network/auth) telemetry with cross-layer correlation.**

---

## Family 1 — Provenance-based IDS (closest ancestors)

Same `logs → graph → anomaly → investigate` pattern, applied to OS audit logs.

- **KAIROS** (IEEE S&P 2024) — *read first.* TGNN dynamic embeddings, attack-summary
  reconstruction, open source.
- **Unicorn** — graph sketching; handles concept drift via gradual forgetting.
- **StreamSpot** — streaming-edge clustering.
- **HOLMES** — rule-based APT correlation.
- **ThreaTrace**, **ShadeWatcher** — node-level detection. *ShadeWatcher is partly proprietary —
  caution if benchmarking against it.*
- **ProGrapher**, **METANOIA** (lifelong PIDS, concept drift), **TFLAG**.
- **PROVEX** — explainability for SOC-analyst trust; directly relevant to our interpretable-output
  requirement.

**Datasets:** DARPA Transparent Computing E3/E5; DARPA OpTC (node/edge ground-truth labels, free) —
our fallback validation data if synthetic-only is challenged.

## Family 2 — Graph anomaly detection algorithms and tooling (Weeks 6–7)

- **PyGOD** — *use it.* 10+ detectors including DOMINANT and GAD-NR; PyG-based; scikit-learn-style
  `fit`/`predict`. We wrap, never reimplement.
- **BOND** (NeurIPS 2022) — benchmark methodology for unsupervised node outlier detection,
  14 methods. Our evaluation follows its reporting style.
- Ma et al., deep graph anomaly detection survey (IEEE TKDE 2021).
- Dynamic-graph anomaly detection survey (2024).
- Ranshous et al. (2015) — splitting / disappearing / flickering communities.
- **Community drift specifics:** CBN (community-boundary change); Safdari & De Bacco (community
  structure as the normal model); Zambon et al. (concept drift in graph streams, TNNLS).

## Family 3 — AI/LLM telemetry (**our novelty lives here**)

- **OpenTelemetry GenAI semantic conventions** (`gen_ai.*`) — our ingestion standard; makes
  GraphGuard deployable behind any OTLP collector.
- **Observability platforms:** Langfuse (OTel backend), LangSmith, Helicone, MLflow — none do graph
  analytics. **This is the gap statement.**
- **LLM security monitoring:** NeuralTrust AI Gateway (behavioural alerting, prompt-chain replay).
- **Prompt-level detectors** (*complementary, content-level*): PIShield, PromptGuard,
  AttentionTracker, TaskTracker, Palisade.
- **AgentSight** (eBPF) — notes that intent-side tools are blind to system actions while
  action-side tools lack intent. Our graph bridges exactly that gap.
- **Standards:** OWASP Top 10 for LLM Applications; MITRE ATLAS.

## Family 4 — UEBA / insider threat / coordinated abuse

Nodes-as-users, but in enterprise and financial settings.

- Hybrid Transformer+GNN UEBA on the **CERT Insider Threat Dataset**.
- **LANL authentication dataset**.
- Microsoft Sentinel UEBA — behavioural baselines, risk scores, investigation graphs.
- Alipay heterogeneous GNN — device/activity aggregation; tens of thousands of malicious accounts
  per day.
- **FraudSquad** — LLM-generated coordinated abuse: temporal synchronicity + linguistic similarity.

## Family 5 — Visual analytics for security (Weeks 9–10)

- **SAGE dashboard** — Graph Explorer + Timeline + prioritisation; user-study validated. We borrow
  the three-panel pattern.
- **KAVAS** — STIX visual analytics.
- Forensic Visualisation Toolkit.
- Industry: interactive graph+timeline is a standard SOC component (Cambridge Intelligence /
  KeyLines, Linkurious).

---

## Name-collision citations (must disambiguate)

"GraphGuard" is already used by unrelated work. They are listed here so the disambiguation is
explicit:

- **GraphGuard** (NDSS 2024) — detects training-data misuse in GNNs via membership inference plus
  training-graph-free unlearning. *Different problem entirely.*
- **GraphGuard** (Reynisson, Schreyer & Borth, 2024, arXiv:2407.12440) — contrastive self-supervised
  learning for credit-card fraud detection.
- **GUARD-CAN** (adjacent) — GCN + autoencoder + GRU anomaly detection for in-vehicle CAN networks.

## Upstream companion project

**Phase 1: Adaptive Jailbreak & Detector-Only Defense Baselines** — PAIR and TAP against a target
LLM under detector-only defences (PromptGuard, ShieldGemma), on AdvBench / StrongREJECT /
JBB-Behaviors. Key finding: **detector-only defences delay an adaptive
attack, they do not stop it.** GraphGuard is designed to consume its labelled session traces and never to run the
attacks (DESIGN.md §10); that ingestion path is not implemented in this repository.
