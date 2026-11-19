/**
 * Shapes returned by the GraphGuard API.
 *
 * These mirror the pydantic models the backend serialises. They are hand-written for
 * now; once the OpenAPI schema settles, generating them from it removes the chance of
 * the two drifting — which is the whole reason FastAPI was chosen (DECISIONS.md D18).
 */

export type AlertState =
  | "new"
  | "investigating"
  | "true_positive"
  | "false_positive"
  | "exported";

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface RunSummary {
  id: string;
  name: string;
  status: RunStatus;
  event_count: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}

export interface AlertSummary {
  id: string;
  run_id: string;
  detector: string;
  entity_type: string;
  entity_id: string;
  window_index: number;
  window_start: string;
  window_end: string;
  score: number;
  window_score: number;
  state: AlertState;
  ground_truth: string | null;
}

export interface Reason {
  feature: string;
  value: number;
  peer_median: number;
  deviation: number;
  text: string;
}

export interface AlertDetail extends AlertSummary {
  explanation: string;
  reasons: Reason[];
  evidence: { type: string; id: string }[];
  tags: string[];
  notes: { id: string; body: string; author_id: string | null; created_at: string }[];
  history: {
    from_state: AlertState | null;
    to_state: AlertState;
    actor_id: string | null;
    rationale: string;
    at: string;
  }[];
  allowed_transitions: AlertState[];
}

export interface AlertPage {
  total: number;
  limit: number;
  offset: number;
  alerts: AlertSummary[];
}

export interface TimelineBucket {
  at: string;
  total: number;
  by_layer: Record<string, number>;
  errors: number;
  refusals: number;
}

export interface ChainStep {
  stage: string;
  entity_type: string;
  entity_id: string;
  at: string;
  detail: string;
}

export interface AttackChain {
  id: string;
  source_ip: string;
  internal_entity_type: string;
  internal_entity_id: string;
  confidence: number;
  steps: ChainStep[];
  narrative: string;
}

export interface PlatformStats {
  runs: number;
  alerts: number;
  alerts_by_state: Record<AlertState, number>;
  open_alerts: number;
  chains: number;
  last_run: RunSummary | null;
}

/** A node in the network map. */
export interface GraphNode {
  id: string;
  type: string;
  label: string;
  /** Highest normalised alert score touching this entity in the current run, 0 if none. */
  risk: number;
  alerts: number;
  /** Contextual attributes shown in the inspector — office, address, owner, and so on. */
  attributes: Record<string, string | number>;
}

export interface GraphLink {
  source: string;
  target: string;
  /** The action that produced it — call, error, refusal, login, request, owns, used_from. */
  relation: string;
  /** Events on this pair in the snapshot window. Previously always 1; see runs.py. */
  weight: number;
  /** How many of those were blocked, refused or errored. A fact, not a detector's opinion. */
  failures: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface NetworkGraph {
  nodes: GraphNode[];
  links: GraphLink[];
}

/**
 * Full detail for one run.
 *
 * The list view answers "did it finish"; this answers "what did it find, under which
 * parameters, and how well did each detector do" — which is the question a run is for.
 */
export interface RunDetail extends RunSummary {
  alert_count: number;
  chain_count: number;
  window_count: number;
  alerts_by_detector: { detector: string; count: number; top_score: number }[];
  alerts_by_state: Partial<Record<AlertState, number>>;
  metrics: RunMetrics | null;
  config: Record<string, unknown> | null;
}

/** One detector's score against one label set, as the evaluation harness computes it. */
export interface DetectorMetrics {
  detector: string;
  scenario: string | null;
  n_observations: number;
  n_positives: number;
  roc_auc: number | null;
  average_precision: number | null;
  precision_at_k: Record<string, number>;
  recall_at_k: Record<string, number>;
  first_hit_rank: number | null;
}

/**
 * `pooled` scores every detector against every labelled entity at once; `per_scenario`
 * splits the same observations by attack. Both are kept because they answer different
 * questions — the second is where "each detector leads on the mode it targets" is visible,
 * and the first is where a detector that is only ever mediocre has nowhere to hide.
 */
export interface RunMetrics {
  pooled: Record<string, DetectorMetrics>;
  per_scenario: Record<string, Record<string, DetectorMetrics>>;
}

/** A saved configuration revision. */
export interface ConfigVersionSummary {
  id: string;
  revision: number;
  note: string;
  is_active: boolean;
  /** False once the schema has moved on and the stored payload no longer validates. */
  parses: boolean;
  created_at: string;
}

export interface ConfigResponse {
  config: Record<string, unknown>;
  active_version: ConfigVersionSummary | null;
  source: string;
  /** Set when the revision marked active can no longer be parsed; the file is in use instead. */
  stale_version: ConfigVersionSummary | null;
  stale_reason: string;
}

/** What this deployment is running. Deliberately carries no secret or connection string. */
export interface SystemInfo {
  version: string;
  environment: string;
  database: string;
  assistant_enabled: boolean;
  assistant_provider: string | null;
  assistant_model: string | null;
  access_token_ttl_minutes: number;
  refresh_token_ttl_days: number;
  detectors: string[];
}
