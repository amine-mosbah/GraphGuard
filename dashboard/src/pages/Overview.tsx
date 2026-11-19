import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowUpRight,
  ChevronDown,
  ShieldAlert,
  Waypoints,
  Workflow,
} from "lucide-react";
import type {
  AlertSummary,
  AttackChain,
  PlatformStats,
  RunSummary,
  TimelineBucket,
} from "../lib/types";
import { StateDonut, VolumeChart } from "../components/charts";
import {
  Button,
  EmptyState,
  PageHeader,
  Panel,
  PanelHead,
  Segmented,
  SeverityBadge,
  SkeletonPanel,
  SkeletonStats,
  Stat,
  StatePill,
  severityOf,
} from "../components/ui";

type Range = "24h" | "7d" | "all";

export function Overview({
  stats,
  alerts,
  chains,
  timeline,
  run,
  loading,
}: {
  stats: PlatformStats | null;
  alerts: AlertSummary[];
  chains: AttackChain[];
  timeline: TimelineBucket[];
  run: RunSummary | null;
  loading: boolean;
}) {
  const [range, setRange] = useState<Range>("all");

  /* Client-side, because the whole run is already in memory and a range control that
     round-trips to the API would be slower and no more truthful.

     Declared above the loading branch: an early return before a hook would make the
     hook order conditional. */
  const scoped = useMemo(() => {
    if (range === "all" || timeline.length === 0) return timeline;
    const hours = range === "24h" ? 24 : 24 * 7;
    const last = timeline[timeline.length - 1];
    if (!last) return timeline;
    const cutoff = new Date(last.at).getTime() - hours * 3600_000;
    return timeline.filter((bucket) => new Date(bucket.at).getTime() >= cutoff);
  }, [timeline, range]);

  if (loading) {
    return (
      <PageHeader title="Overview" subtitle="Loading the current run…">
        <SkeletonStats />
        <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
          <SkeletonPanel rows={5} />
          <SkeletonPanel rows={5} />
        </div>
      </PageHeader>
    );
  }

  const open = alerts.filter((a) => a.state === "new" || a.state === "investigating");
  const critical = open.filter((a) => a.window_score >= 0.9);
  const top = [...open].sort((a, b) => b.score - a.score).slice(0, 6);
  const confirmed = alerts.filter((a) => a.state === "true_positive").length;


  const donut = stats
    ? [
        { label: "New", value: stats.alerts_by_state.new ?? 0, color: "var(--color-accent)" },
        {
          label: "Investigating",
          value: stats.alerts_by_state.investigating ?? 0,
          color: "var(--color-medium)",
        },
        {
          label: "True positive",
          value: stats.alerts_by_state.true_positive ?? 0,
          color: "var(--color-critical)",
        },
        {
          label: "False positive",
          value: stats.alerts_by_state.false_positive ?? 0,
          color: "var(--color-ink-faint)",
        },
        { label: "Exported", value: stats.alerts_by_state.exported ?? 0, color: "var(--color-ok)" },
      ].filter((segment) => segment.value > 0)
    : [];

  return (
    <PageHeader
      title="Overview"
      subtitle={
        run
          ? `${run.name} · ${run.event_count.toLocaleString()} events analysed in ${run.duration_seconds ?? "—"}s`
          : "No completed detection run yet."
      }
      actions={
        <Button to="/alerts">
          <ShieldAlert size={16} />
          Open triage queue
        </Button>
      }
    >

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Open alerts"
          value={String(open.length)}
          accent
          icon={<ShieldAlert size={16} />}
          delta={confirmed > 0 ? `${confirmed} confirmed` : undefined}
          /* A queue that has been worked down is good news, so a falling count reads as
             "up". Only this caller knows the metric's polarity, which is why Stat does
             not try to infer it from the number. */
          trend={open.length === 0 ? "up" : critical.length > 0 ? "down" : "neutral"}
          hint={`${critical.length} at critical severity`}
        />
        <Stat
          label="Events analysed"
          value={(run?.event_count ?? 0).toLocaleString()}
          icon={<Activity size={16} />}
          hint="in the most recent run"
        />
        <Stat
          label="Attack chains"
          value={String(chains.length)}
          icon={<Waypoints size={16} />}
          hint="external address linked to an internal identity"
        />
        <Stat
          label="Detection runs"
          value={String(stats?.runs ?? 0)}
          icon={<Workflow size={16} />}
          hint={run?.finished_at ? `last ${new Date(run.finished_at).toLocaleString()}` : "—"}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <Panel>
          <PanelHead
            title="Telemetry volume"
            subtitle="Event counts over the run, stacked by telemetry layer"
            action={
              <Segmented
                ariaLabel="Time range"
                value={range}
                onChange={setRange}
                options={[
                  { value: "24h", label: "24h" },
                  { value: "7d", label: "7d" },
                  { value: "all", label: "All" },
                ]}
              />
            }
          />
          <div className="px-5 pb-5">
            <VolumeChart buckets={scoped} />
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Triage state" subtitle="Where the queue stands" />
          <div className="px-5 pb-6">
            {donut.length > 0 ? (
              <StateDonut
                segments={donut}
                centreValue={String(stats?.open_alerts ?? 0)}
                centreLabel="open"
              />
            ) : (
              <EmptyState
                icon={<ShieldAlert size={26} />}
                title="No alerts yet"
                body="Trigger a detection run to populate the queue."
              />
            )}
          </div>
        </Panel>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <Panel>
          <PanelHead
            title="Highest-scoring open alerts"
            subtitle="Ranked across every window by raw detector score"
            action={
              <Link
                to="/alerts"
                className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
              >
                All alerts <ArrowUpRight size={13} />
              </Link>
            }
          />
          {top.length === 0 ? (
            <EmptyState
              icon={<ShieldAlert size={26} />}
              title="Queue is clear"
              body="Every alert produced by this run has been triaged."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-y border-[var(--color-line)] text-left text-[11px] tracking-wide text-[var(--color-ink-faint)] uppercase">
                    <th className="px-5 py-2.5 font-medium">Entity</th>
                    <th className="px-3 py-2.5 font-medium">Detector</th>
                    <th className="px-3 py-2.5 font-medium">Severity</th>
                    <th className="px-3 py-2.5 text-right font-medium">Score</th>
                    <th className="px-5 py-2.5 font-medium">State</th>
                  </tr>
                </thead>
                <tbody>
                  {top.map((alert) => (
                    <tr
                      key={alert.id}
                      className="border-b border-[var(--color-line)]/60 last:border-0 hover:bg-[var(--color-raise-subtle)]"
                    >
                      <td className="px-5 py-3">
                        <Link to={`/alerts/${alert.id}`} className="group block">
                          <span className="block font-mono text-xs text-[var(--color-ink)] group-hover:text-[var(--color-accent)]">
                            {alert.entity_id}
                          </span>
                          <span className="block text-[11px] text-[var(--color-ink-faint)]">
                            {alert.entity_type} · window {alert.window_index}
                          </span>
                        </Link>
                      </td>
                      <td className="px-3 py-3 text-xs text-[var(--color-ink-dim)]">
                        {alert.detector}
                      </td>
                      <td className="px-3 py-3">
                        <SeverityBadge severity={severityOf(alert.window_score)} />
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-xs tabular-nums">
                        {alert.window_score.toFixed(3)}
                      </td>
                      <td className="px-5 py-3">
                        <StatePill state={alert.state} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel>
          <PanelHead
            title="Cross-layer chains"
            subtitle="Paths linking an external event to an internal identity"
          />
          <div className="px-5 pb-5">
            {chains.length === 0 ? (
              <EmptyState
                icon={<Waypoints size={26} />}
                title="No chains found"
                body="A chain is reported only when an external burst is followed by a successful credential use from the same address. Its absence is a result, not a gap."
              />
            ) : (
              <ul className="space-y-3">
                {chains.slice(0, 4).map((chain) => (
                  <li key={chain.id} className="rounded-xl bg-[var(--color-surface-2)] p-3.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-[var(--color-critical)]">
                        {chain.source_ip}
                      </span>
                      <span className="text-[10px] text-[var(--color-ink-faint)] tabular-nums">
                        confidence {chain.confidence.toFixed(2)}
                      </span>
                    </div>
                    {/* A drawn connector, not a "↓" glyph: the chain is a path, and a
                        typographic arrow renders at whatever weight the font feels like. */}
                    <div className="my-1 flex items-center gap-1.5" aria-hidden="true">
                      <span className="ml-[3px] h-4 w-px bg-[var(--color-line)]" />
                      <ChevronDown size={11} className="-ml-[9px] text-[var(--color-ink-faint)]" />
                    </div>
                    <div className="font-mono text-xs text-[var(--color-accent)]">
                      {chain.internal_entity_id}
                    </div>
                    <div className="mt-2 text-[11px] text-[var(--color-ink-faint)]">
                      {chain.steps.length} steps
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>
    </PageHeader>
  );
}
