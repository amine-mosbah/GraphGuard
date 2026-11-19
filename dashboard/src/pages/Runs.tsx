/**
 * Detection runs — the list, and the detail behind each one.
 *
 * A run was previously five columns: a name, a status, an event count, a duration and a finish
 * time. That answers "did it complete", which is an operations question, and says nothing about
 * the only question a run exists to answer — what did it find, under which parameters, and how
 * well did each detector do. The evaluation harness has been writing per-detector ROC-AUC and
 * average precision into every run since the beginning; none of it was ever displayed.
 *
 * The detail view is therefore where the project's own evidence lives, per run, rather than only
 * in the figures generated for the report. That matters beyond tidiness: a reader who changes a
 * threshold on the settings page and starts a run can see what it did to the numbers, which is
 * the loop the whole configuration surface exists to support.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronRight,
  Clock,
  Database,
  FileJson,
  Gauge,
  PlayCircle,
  ScrollText,
  ShieldAlert,
  Waypoints,
} from "lucide-react";
import { api } from "../lib/api";
import { useInterval } from "../lib/use-interval";
import type {
  AttackChain,
  DetectorMetrics,
  RunDetail,
  RunSummary,
  TimelineBucket,
} from "../lib/types";
import {
  Button,
  EmptyState,
  PageHeader,
  Panel,
  PanelHead,
  Segmented,
  SkeletonPanel,
  Stat,
  StatePill,
} from "../components/ui";
import { ErrorState } from "../components/feedback";
import { AtKChart, RankedBars, VolumeChart } from "../components/charts";

/** Detection runs, with the control to start another. */
export function Runs({
  runs,
  loading,
  onRefresh,
}: {
  runs: RunSummary[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * Poll only while something is actually in flight, then stop.
   *
   * A run takes about eight seconds on the Celery worker. Before this the page showed
   * "queued" and never changed, so the only way to see a run finish was to navigate
   * away and back — the page was reporting stale state indefinitely.
   */
  const pending = runs.some((run) => run.status === "queued" || run.status === "running");
  useInterval(onRefresh, pending ? 3000 : null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/v1/runs", { name: "manual run" });
      onRefresh();
    } catch (cause) {
      // Only administrators may start a run; an analyst gets a 403 and should be told
      // why rather than watching a button do nothing.
      setError(cause instanceof Error ? cause.message : "Could not start a run");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <PageHeader title="Detection runs" subtitle="Loading run history…">
        <SkeletonPanel rows={6} />
      </PageHeader>
    );
  }

  const succeeded = runs.filter((run) => run.status === "succeeded");

  return (
    <PageHeader
      title="Detection runs"
      subtitle="Each run analyses the telemetry with one configuration and writes its alerts, chains, graph and evaluation metrics. Open one to see what it found and how each detector scored."
      actions={
        <>
          {pending && (
            <span className="inline-flex items-center gap-1.5 rounded-chip bg-[var(--color-accent)]/12 px-2.5 py-1 text-xs font-medium text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/25">
              <span className="size-1.5 animate-pulse rounded-chip bg-current" aria-hidden />
              Live
            </span>
          )}
          <Button onClick={start} loading={busy}>
            <PlayCircle size={16} />
            {busy ? "Queuing" : "Start a run"}
          </Button>
        </>
      }
    >
      {error && (
        <div className="rounded-xl bg-[var(--color-critical)]/12 px-4 py-3 text-xs text-[var(--color-critical)] ring-1 ring-[var(--color-critical)]/25">
          {error}
        </div>
      )}

      {runs.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Runs recorded" value={String(runs.length)} icon={<ScrollText size={16} />} />
          <Stat
            label="Succeeded"
            value={`${succeeded.length} of ${runs.length}`}
            icon={<Gauge size={16} />}
            hint={
              runs.some((run) => run.status === "failed")
                ? "A failed run keeps its error; open it to read what went wrong."
                : "No failures recorded."
            }
          />
          <Stat
            label="Events in the latest run"
            value={(succeeded[0]?.event_count ?? 0).toLocaleString()}
            icon={<Database size={16} />}
            hint="Normalised trace events across every enabled telemetry layer."
          />
          <Stat
            label="Typical duration"
            value={medianDuration(succeeded)}
            icon={<Clock size={16} />}
            hint="Long enough that runs execute on a worker rather than in the request."
          />
        </div>
      )}

      <Panel className="overflow-hidden">
        {runs.length === 0 ? (
          <EmptyState
            icon={<PlayCircle size={26} />}
            title="No runs yet"
            body="Start one to populate the console."
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--color-line)] text-left text-[11px] tracking-wide text-[var(--color-ink-faint)] uppercase">
                <th className="px-5 py-3 font-medium">Run</th>
                <th className="px-3 py-3 font-medium">Status</th>
                <th className="px-3 py-3 text-right font-medium">Events</th>
                <th className="px-3 py-3 text-right font-medium">Duration</th>
                <th className="px-5 py-3 font-medium">Finished</th>
                <th className="w-10 px-3 py-3" aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.id}
                  onClick={() => navigate(`/runs/${run.id}`)}
                  className="cursor-pointer border-b border-[var(--color-line)]/50 transition-colors last:border-0 hover:bg-[var(--color-raise-subtle)]"
                >
                  <td className="px-5 py-3">
                    <Link
                      to={`/runs/${run.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="font-medium hover:text-[var(--color-accent)]"
                    >
                      {run.name}
                    </Link>
                    <div className="mt-0.5 font-mono text-[10px] text-[var(--color-ink-faint)]">
                      {run.id.slice(0, 12)}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <StatePill state={run.status} />
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-xs tabular-nums">
                    {run.event_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-xs tabular-nums">
                    {run.duration_seconds !== null ? `${run.duration_seconds}s` : "—"}
                  </td>
                  <td className="px-5 py-3 text-xs text-[var(--color-ink-faint)]">
                    {run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-3 py-3 text-[var(--color-ink-faint)]">
                    <ChevronRight size={15} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </PageHeader>
  );
}

function medianDuration(runs: RunSummary[]): string {
  const values = runs
    .map((run) => run.duration_seconds)
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);
  if (values.length === 0) return "—";
  return `${values[Math.floor(values.length / 2)]!.toFixed(1)}s`;
}

type Tab = "findings" | "evaluation" | "config";

/** One run, in full. */
export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [chains, setChains] = useState<AttackChain[]>([]);
  const [timeline, setTimeline] = useState<TimelineBucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("findings");

  const load = useCallback(async () => {
    if (!runId) return;
    setError(null);
    try {
      const detail = await api.get<RunDetail>(`/api/v1/runs/${runId}`);
      setRun(detail);
      // Only a finished run has anything to draw; asking for the timeline of a queued one
      // returns an empty list and two extra requests.
      if (detail.status === "succeeded") {
        const [chainResult, timelineResult] = await Promise.all([
          api.get<{ chains: AttackChain[] }>(`/api/v1/runs/${runId}/chains`),
          api.get<{ buckets: TimelineBucket[] }>(`/api/v1/runs/${runId}/timeline`),
        ]);
        setChains(chainResult.chains);
        setTimeline(timelineResult.buckets);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load this run");
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  // A run opened while it is still executing should finish in front of the analyst.
  useInterval(load, run && (run.status === "queued" || run.status === "running") ? 3000 : null);

  if (loading) {
    return (
      <PageHeader title="Detection run" subtitle="Loading…">
        <SkeletonPanel rows={4} />
        <SkeletonPanel rows={8} />
      </PageHeader>
    );
  }
  if (error || !run) {
    return (
      <ErrorState
        title="Could not open this run"
        message={error ?? "The run does not exist."}
        onRetry={() => {
          setLoading(true);
          void load();
        }}
      />
    );
  }

  return (
    <PageHeader
      title={run.name}
      subtitle={
        <span className="font-mono text-xs text-[var(--color-ink-faint)]">
          {run.id} · started{" "}
          {run.started_at ? new Date(run.started_at).toLocaleString() : "not yet"}
        </span>
      }
      actions={
        <>
          <Button variant="ghost" size="sm" to="/runs">
            <ArrowLeft size={14} /> All runs
          </Button>
          <StatePill state={run.status} />
        </>
      }
    >
      {run.error && (
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-critical)]/12 px-4 py-3 ring-1 ring-[var(--color-critical)]/25">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-[var(--color-critical)]" />
          <div className="min-w-0">
            <div className="text-xs font-medium text-[var(--color-critical)]">
              This run failed and produced no findings.
            </div>
            <pre className="mt-1.5 overflow-x-auto font-mono text-[11px] whitespace-pre-wrap text-[var(--color-ink-dim)]">
              {run.error}
            </pre>
          </div>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Events analysed"
          value={run.event_count.toLocaleString()}
          icon={<Database size={16} />}
          hint={`Aggregated into ${run.window_count} time windows.`}
        />
        <Stat
          label="Alerts written"
          value={run.alert_count.toLocaleString()}
          icon={<ShieldAlert size={16} />}
          accent
          hint={describeStates(run)}
        />
        <Stat
          label="Attack chains"
          value={String(run.chain_count)}
          icon={<Waypoints size={16} />}
          hint={
            run.chain_count === 0
              ? "None — which is the correct result when no breach succeeded."
              : "Cross-layer paths from an external address to an internal identity."
          }
        />
        <Stat
          label="Wall clock"
          value={run.duration_seconds !== null ? `${run.duration_seconds}s` : "—"}
          icon={<Clock size={16} />}
          hint={
            run.finished_at
              ? `Finished ${new Date(run.finished_at).toLocaleString()}`
              : "Still running."
          }
        />
      </div>

      <Segmented
        ariaLabel="Which part of the run to show"
        value={tab}
        onChange={setTab}
        options={[
          { value: "findings", label: "Findings" },
          { value: "evaluation", label: "Evaluation" },
          { value: "config", label: "Configuration" },
        ]}
      />

      {tab === "findings" && <Findings run={run} chains={chains} timeline={timeline} />}
      {tab === "evaluation" && <Evaluation run={run} />}
      {tab === "config" && <Configuration run={run} />}
    </PageHeader>
  );
}

function describeStates(run: RunDetail): string {
  const entries = Object.entries(run.alerts_by_state).filter(([, count]) => count > 0);
  if (entries.length === 0) return "Nothing was flagged.";
  return entries.map(([state, count]) => `${count} ${state.replace("_", " ")}`).join(" · ");
}

function Findings({
  run,
  chains,
  timeline,
}: {
  run: RunDetail;
  chains: AttackChain[];
  timeline: TimelineBucket[];
}) {
  const detectorRows = run.alerts_by_detector.map((row) => ({
    label: row.detector,
    value: row.count,
    display: String(row.count),
    emphasis: true,
  }));
  const busiest = Math.max(1, ...run.alerts_by_detector.map((row) => row.count));

  return (
    <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
      <Panel>
        <PanelHead
          title="Telemetry over the run"
          subtitle="Event volume per bucket, stacked by the layer it came from"
        />
        <div className="px-5 pb-5">
          <VolumeChart buckets={timeline} />
        </div>
      </Panel>

      <Panel>
        <PanelHead
          title="Alerts by detector"
          subtitle="Which detector produced how much of the queue"
        />
        <div className="px-5 pb-5">
          {detectorRows.length === 0 ? (
            <p className="text-xs text-[var(--color-ink-faint)]">
              This run produced no alerts. With a threshold this high that is a finding rather than
              a fault — the configuration tab records what it was set to.
            </p>
          ) : (
            <>
              <RankedBars rows={detectorRows} max={busiest} />
              <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
                Volume, not quality. A detector producing most of the queue is not the best one —
                the evaluation tab is where that question is answered.
              </p>
            </>
          )}
        </div>
      </Panel>

      <Panel className="xl:col-span-2">
        <PanelHead
          title="Cross-layer chains"
          subtitle={
            chains.length === 0
              ? "None found in this run"
              : `${chains.length} path${chains.length === 1 ? "" : "s"} from an external address to an internal identity`
          }
          action={
            chains.length > 0 ? (
              <Button variant="ghost" size="sm" to="/chains">
                <Waypoints size={14} /> Open the chain view
              </Button>
            ) : undefined
          }
        />
        <div className="px-5 pb-5">
          {chains.length === 0 ? (
            <p className="text-xs leading-relaxed text-[var(--color-ink-faint)]">
              The correlator reports a chain only when both halves are present: an external burst
              and a subsequent successful credential use from the same address. Its absence is a
              measured result — the no-breach control scenario correctly yields nothing.
            </p>
          ) : (
            <ul className="space-y-2">
              {chains.map((chain) => (
                <li
                  key={chain.id}
                  className="flex items-center gap-3 rounded-xl bg-[var(--color-surface-2)] px-3.5 py-3 text-xs"
                >
                  <Waypoints size={15} className="shrink-0 text-[var(--color-critical)]" />
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {chain.source_ip} → {chain.internal_entity_id}
                  </span>
                  <span className="shrink-0 text-[var(--color-ink-faint)]">
                    {chain.steps.length} steps · confidence {chain.confidence.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </div>
  );
}

/**
 * The evaluation the run recorded about itself.
 *
 * Pooled scores first, because that is the ranking a reader wants; the per-scenario split is
 * behind a selector because it is a matrix, and a matrix rendered by default is a wall. The
 * point of keeping both is that they disagree in an informative way: a detector can lead its own
 * attack mode and be below chance on someone else's, and the report reports both.
 */
function Evaluation({ run }: { run: RunDetail }) {
  const [scenario, setScenario] = useState<string>("pooled");

  const scenarios = useMemo(() => {
    const found = new Set<string>();
    for (const perScenario of Object.values(run.metrics?.per_scenario ?? {})) {
      for (const key of Object.keys(perScenario)) found.add(key);
    }
    return [...found].sort();
  }, [run.metrics]);

  const rows = useMemo<DetectorMetrics[]>(() => {
    if (!run.metrics) return [];
    if (scenario === "pooled") return Object.values(run.metrics.pooled);
    return Object.values(run.metrics.per_scenario)
      .map((byScenario) => byScenario[scenario])
      .filter((value): value is DetectorMetrics => Boolean(value));
  }, [run.metrics, scenario]);

  const [focus, setFocus] = useState<string | null>(null);
  const focused = rows.find((row) => row.detector === focus) ?? rows[0] ?? null;

  if (!run.metrics || rows.length === 0) {
    return (
      <Panel>
        <EmptyState
          icon={<Gauge size={26} />}
          title="No evaluation recorded"
          body="Metrics are computed when the simulator's ground-truth labels are present. A run over unlabelled telemetry produces alerts but nothing to score them against."
        />
      </Panel>
    );
  }

  const auc = rows
    .filter((row) => row.roc_auc !== null)
    .map((row) => ({
      label: row.detector,
      value: row.roc_auc ?? 0,
      emphasis: !row.detector.startsWith("baseline:"),
    }))
    .sort((a, b) => b.value - a.value);

  const ap = rows
    .filter((row) => row.average_precision !== null)
    .map((row) => ({
      label: row.detector,
      value: row.average_precision ?? 0,
      emphasis: !row.detector.startsWith("baseline:"),
    }))
    .sort((a, b) => b.value - a.value);

  const positiveRate =
    rows[0] && rows[0].n_observations > 0 ? rows[0].n_positives / rows[0].n_observations : 0;

  return (
    <div className="space-y-5">
      {scenarios.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-[var(--color-ink-faint)]">Scored against</span>
          <select
            value={scenario}
            onChange={(event) => setScenario(event.target.value)}
            className="rounded-control border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs focus:border-[var(--color-accent)]/40 focus:outline-none"
          >
            <option value="pooled">Every labelled entity (pooled)</option>
            {scenarios.map((name) => (
              <option key={name} value={name}>
                {name.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel>
          <PanelHead
            title="ROC-AUC by detector"
            subtitle="Ranking quality across every threshold"
          />
          <div className="px-5 pb-5">
            <RankedBars rows={auc} reference={0.5} referenceLabel="0.50 — a coin toss" />
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
              Solid bars are graph detectors, faint ones the flat-log baselines they are being
              compared against.
            </p>
          </div>
        </Panel>

        <Panel>
          <PanelHead
            title="Average precision"
            subtitle="How much of the top of the queue is real"
          />
          <div className="px-5 pb-5">
            <RankedBars
              rows={ap}
              reference={positiveRate}
              referenceLabel={`${positiveRate.toFixed(3)} — the positive rate, what random ranking scores`}
            />
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
              The more honest of the two measures when positives are rare, which they are here:{" "}
              {rows[0]?.n_positives.toLocaleString()} of{" "}
              {rows[0]?.n_observations.toLocaleString()} observations.
            </p>
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHead
          title="Behaviour at the top of the queue"
          subtitle="An analyst works down a ranked list, so what matters is the first few dozen rows"
          action={
            <select
              value={focused?.detector ?? ""}
              onChange={(event) => setFocus(event.target.value)}
              className="rounded-control border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-1.5 text-xs focus:border-[var(--color-accent)]/40 focus:outline-none"
            >
              {rows.map((row) => (
                <option key={row.detector} value={row.detector}>
                  {row.detector}
                </option>
              ))}
            </select>
          }
        />
        {focused && (
          <div className="grid gap-5 px-5 pb-5 lg:grid-cols-[1fr_260px]">
            <AtKChart precision={focused.precision_at_k} recall={focused.recall_at_k} />
            <dl className="space-y-3 text-xs">
              <Metric label="ROC-AUC" value={focused.roc_auc?.toFixed(3) ?? "n/a"} />
              <Metric
                label="Average precision"
                value={focused.average_precision?.toFixed(3) ?? "n/a"}
              />
              <Metric
                label="First true positive at rank"
                value={focused.first_hit_rank !== null ? `#${focused.first_hit_rank}` : "never"}
                hint="How far an analyst reads before the first real finding."
              />
              <Metric
                label="Labelled positives"
                value={`${focused.n_positives.toLocaleString()} of ${focused.n_observations.toLocaleString()}`}
              />
            </dl>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[var(--color-ink-faint)]">{label}</dt>
      <dd className="mt-0.5 text-base font-semibold tabular-nums text-[var(--color-ink)]">
        {value}
      </dd>
      {hint && <p className="mt-0.5 text-[11px] text-[var(--color-ink-faint)]">{hint}</p>}
    </div>
  );
}

/**
 * The configuration this run actually used.
 *
 * Stamped onto the run at dispatch rather than read from the current settings, which is the
 * whole point: an alert means "unusual relative to these thresholds", and reading it a month
 * later against whatever the thresholds have since become would be a different claim.
 */
function Configuration({ run }: { run: RunDetail }) {
  const text = JSON.stringify(run.config ?? {}, null, 2);
  return (
    <Panel>
      <PanelHead
        title="Configuration used by this run"
        subtitle="A snapshot taken at dispatch, not the current settings"
        action={
          <Button
            variant="ghost"
            size="sm"
            href={`data:application/json;charset=utf-8,${encodeURIComponent(text)}`}
            title="Download this run's configuration"
          >
            <FileJson size={14} /> Download
          </Button>
        }
      />
      <div className="px-5 pb-5">
        <pre className="max-h-[520px] overflow-auto rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-ink-dim)]">
          {text}
        </pre>
      </div>
    </Panel>
  );
}
