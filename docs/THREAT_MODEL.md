# THREAT_MODEL.md — attacker classes, scenarios, framework mapping

> Defines *what GraphGuard is trying to catch* and, just as importantly, *what it is not*.
> Every simulator scenario in `src/graphguard/simulator/scenarios/` must appear in the table in §3
> with a ground-truth label, a detector it is expected to trigger, and a framework tag.

---

## 1. Assets and adversary model

**Protected assets.** The AI platform's model-serving capacity and quota, the confidentiality of
tenant data reachable through it, the integrity of the identity system (API keys, user accounts),
and the platform's guardrails/detectors themselves.

**What the defender observes.** Telemetry only — API-call logs, session traces, detector decisions,
and (Layer 2/Stretch) HTTP gateway and network/auth logs. GraphGuard sits **beside** the platform,
never in the request path.

**Adversary capabilities assumed.** The adversary can obtain valid credentials (phishing, leaked
key, insider), create accounts at low cost, control multiple source IPs, and adapt across attempts
using observed feedback (blocks and refusals are signal to them). The adversary does **not** have
write access to the telemetry pipeline — i.e. we assume logs are not forged. Telemetry integrity is
an explicit out-of-scope assumption.

**Attacker classes.**

| Class | Description | Primary telemetry layer |
|---|---|---|
| **A. External outsider** | No valid identity. Probes, credential-stuffs, scrapes, abuses endpoints. | `http` (Layer 2) |
| **B. Compromised identity** | Holds a *valid* credential taken from a legitimate user. Behaves unlike the credential's owner. | `ai` (Core) |
| **C. Coordinated ring** | Many cheap identities driven by one actor, acting in unison. | `ai` (Core) |
| **D. Insider / lateral** | Legitimate identity probing resources beyond its normal scope. | `ai` + `network` (Stretch) |
| **E. Hybrid (A→B→D)** | Outsider who breaches, becomes an insider, then moves laterally. **The realistic modern case and the showcase demo (DESIGN.md §7).** | cross-layer |
| **F. Adaptive jailbreaker** | Iteratively reframes prompts against detector-only defences until one passes. Traces supplied by the **Phase 1** project. | `ai` |

---

## 2. Explicit non-goals

GraphGuard does **not**:

- Prevent or block anything. It scores and explains; a human decides. A high anomaly score is
  **not** proof of malice — legitimate power users score high, and this is reported honestly.
- Perform content-level analysis (prompt semantics, judge scoring, refusal classification as a
  *detector*). That is Phase 1's layer. GraphGuard consumes Phase 1's *decisions* as edge
  attributes, but reasons **relationally**.
- Compete with a NIDS/SIEM (Zeek, Suricata, Wazuh). Non-AI telemetry layers exist as **context
  enrichment around the AI-platform core**, not as a general network monitor.
- Assume real production telemetry will ever arrive. Synthetic generation is the validation
  backbone, by design.

---

## 3. Scenario catalogue

Each scenario is a labelled generator producing `TraceEvent`s with `label` set to the scenario
name; benign traffic carries `label="benign"`. In "production" mode `label` is `null`.

| Scenario | Class | Behavioural signature | Expected detector | Framework tag | Tier | Status |
|---|---|---|---|---|---|---|
| `stolen_key` | B | A valid `api_key` abruptly diverges from its own history: volume spike, off-hours shift, models it never used, new source IP | node-outlier | OWASP LLM10 *Unbounded Consumption*; ATLAS *Valid Accounts* | Core | ☑ |
| `coordinated_ring` | C | ~20 freshly created accounts hit one model with near-identical timing and shape, in sync | abnormal-subgraph | ATLAS *Resource Development*; OWASP LLM10 | Core | ☑ |
| `slow_group_drift` | B/D | A whole cohort's behaviour shifts gradually across snapshots; no single member is an outlier | community drift | ATLAS *Discovery* | Core | ☑ |
| `insider_probe` | D | A legitimate identity fans out to resources/projects outside its usual neighbourhood | node-outlier + drift | ATLAS *Lateral Movement* | Core | ☐ |
| `credential_stuffing` | A | One IP, high-rate failed logins against the auth endpoint | node-outlier (http layer) | ATLAS *Credential Access* | Layer 2 | ☑ (no-breach control) |
| `external_to_internal` | E | Credential stuffing from IP *X*, then a **valid** key logging in from *X*, then that account drifting | cross-layer path search | ATLAS *Initial Access* → *Lateral Movement* | Layer 2 | ☑ |
| `adaptive_jailbreak` | F | A session with a run of blocks then a success — detector-evasion by reframing. Would replay labelled traces from Phase 1 rather than synthetic ones. | node-outlier + abnormal-subgraph | OWASP LLM01 *Prompt Injection*; ATLAS *Jailbreak* | Stretch | ☐ planned, not implemented |

### Why `adaptive_jailbreak` would matter as validation

Phase 1's empirical finding is that detector-only defences **delay** an adaptive attack rather than
stop it: the attacker uses each block as feedback and reframes until a prompt passes. That produces
a distinctive *temporal shape* — blocked, blocked, succeeded — which is invisible to a per-prompt
detector by construction, because the detector resets each round. If GraphGuard flagged exactly the
"blocked-but-still-successful" sessions, it would demonstrate the graph layer catching what the
content-level detector structurally cannot. It would be the cleanest available validation of the
core thesis, because it would use real labelled traces. **This scenario is not implemented in this
repository; every reported result comes from the five synthetic scenarios above it.**

---

## 4. Evaluation contract

- Ground truth comes from the simulator's `label` field; no manual annotation.
- Metrics: precision, recall, F1, ROC-AUC, average precision, plus precision@k (BOND-style).
### Known limitation — attack loudness

With the default `stolen_key` parameters (`volume_multiplier: 8.0`), attack events are roughly
**17% of all telemetry**, because the randomly-drawn victims happened to be high-rate keys. Node
contamination is the number the detector actually faces — 3 compromised of 69 keys, ≈4.3%, matching
the configured `contamination: 0.05` — but the *volume* signal is loud, and results obtained at this
setting must not be presented as evidence against a stealthy adversary.

**Measured (2026-07-24, seed 1337, average precision on api_key (node, window) pairs):**

| method | 1.5× | 2× | 4× | 8× |
|---|---|---|---|---|
| baseline: volume threshold | 0.124 | 0.281 | 0.621 | **0.874** |
| baseline: error-rate threshold | 0.199 | 0.313 | 0.471 | 0.520 |
| baseline: off-hours rule | 0.052 | 0.069 | 0.084 | 0.086 |
| **graph: node-outlier (egonet)** | **0.863** | **0.863** | **0.867** | 0.864 |
| graph: node-outlier (DOMINANT) | 0.123 | 0.154 | 0.217 | 0.277 |

Two honest readings, both required in the report:

1. **The thesis holds, quantified.** At 8× the volume rule wins — a loud attack does not need a
   graph. As the attacker gets quieter the flat-log baselines collapse (volume AP 0.87 → 0.12)
   while the graph detector is *flat at ≈0.86 across all loudness levels*, with P@10 = 1.00
   throughout. What survives at 1.5× is the relational signal — models and IPs that are new *for
   this key* — which no flat rule can express. This table is the "invisible in logs, obvious in
   the graph" figure.
2. **DOMINANT currently loses to the classical egonet baseline everywhere.** Untuned, out of the
   box. This gets reported as-is (BOND-style honesty) and revisited in Weeks 6–7: candidate causes
   are the tiny per-window graph (~240 nodes), feature scaling, and contamination mismatch.

### Known limitation — evading unison detection

Abnormal-subgraph detection keys on ring members being near-identical across behaviour, targets
and minute-level timing *simultaneously*. An adversary who accepts looser coordination — jittering
schedules by hours instead of seconds, varying per-member volume, spreading across models —
dilutes the similarity below the clustering threshold and evades it (asserted directly in
`tests/test_coordinated_ring.py`). Detection also weakens for very small rings, since the method
rests on *mutual* corroboration: measured mean recall is ≈0.96 at 25 accounts, ≈0.94 at 18, and
becomes unreliable at 8. False positives were zero at every size tested.

Both properties are to be reported rather than tuned away. The mitigating point is that evasion
costs the attacker the coordination efficiency the ring exists for, and that the rings which
matter operationally — dozens of accounts — are the easiest to catch.

### Known limitation — drift is indistinguishable from legitimate change

Community drift measures *coherent group movement*. A team genuinely migrating to a new model, a
product launch, or a daylight-saving shift all produce exactly that, and the detector will and
should flag them. Population-wide seasonal movement (the weekend) is subtracted before scoring, so
what remains is movement specific to a *subset* — but a subset changing for a legitimate reason is
still a detection. This detector surfaces "this group is not where it was, and it moved together"
with the evidence; whether that is an incident is the analyst's judgement, by design
(DESIGN.md §14). Its precision is therefore structurally lower than the other two detectors', and
the report must present it as a triage aid rather than a verdict.

### Baseline comparison

- Every scenario is additionally scored against **flat-log baselines** (volume threshold, error-rate
  threshold, off-hours rule) to substantiate the "invisible in logs, obvious in the graph" claim.
  A scenario the baselines also catch is reported as such — the comparison is only meaningful if
  it is honest about the cases where a simple rule suffices.
