/**
 * Cross-layer attack chains.
 *
 * This page carries the project's central claim, so it is built around the figure rather than
 * around a list. Previously it printed each chain's steps as a bulleted timeline, which states
 * the same facts and shows none of the structure: that the path *starts in one telemetry layer
 * and ends in another* is the entire finding, and a vertical list of four sentences renders
 * that crossing as a change of noun halfway down.
 *
 * The narrative the correlator writes is kept alongside the figure rather than replaced by it.
 * The figure is the evidence; the sentence is what an analyst pastes into an incident report.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Clock,
  Layers,
  ListTree,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import type { AttackChain } from "../lib/types";
import {
  Button,
  EmptyState,
  PageHeader,
  Panel,
  PanelHead,
  Segmented,
  SkeletonPanel,
  Stat,
  TypeChip,
} from "../components/ui";
import {
  ChainDwellBars,
  ChainSwimlane,
  ConfidenceMeter,
  formatDuration,
  formatGap,
} from "../components/chain-visuals";

/**
 * Figure or text.
 *
 * The "Steps" view is not a fallback for when the figure fails — it is a requirement. A finding
 * that can only be read in colour, and cannot be pasted into an incident report, is an analyst's
 * problem rather than a feature.
 */
type View = "figure" | "steps";

export function Chains({ chains, loading }: { chains: AttackChain[]; loading: boolean }) {
  const [view, setView] = useState<View>("figure");

  const summary = useMemo(() => {
    if (chains.length === 0) return null;
    const dwells = chains.map((chain) => {
      const steps = chain.steps;
      const start = steps[0]?.at;
      const end = steps[steps.length - 1]?.at;
      return start && end ? new Date(end).getTime() - new Date(start).getTime() : 0;
    });
    const corroborated = chains.filter((chain) =>
      chain.steps.some((step) => step.stage === "internal abuse"),
    ).length;
    return {
      longest: Math.max(...dwells),
      corroborated,
      entities: new Set(chains.map((chain) => chain.internal_entity_id)).size,
    };
  }, [chains]);

  if (loading) {
    return (
      <PageHeader title="Attack chains" subtitle="Searching for cross-layer paths…">
        <SkeletonPanel rows={4} />
      </PageHeader>
    );
  }

  return (
    <PageHeader
      title="Attack chains"
      subtitle={
        <>
          A chain is a <em>path</em>, not a score: an external burst against the gateway followed
          by a successful credential use from the same address, and then that credential behaving
          unlike itself. Each event alone is unremarkable, which is exactly why no single-layer
          detector produces this.
        </>
      }
      actions={
        chains.length > 0 ? (
          <Segmented
            ariaLabel="How to show each chain"
            value={view}
            onChange={setView}
            options={[
              { value: "figure", label: "Figure" },
              { value: "steps", label: "Steps" },
            ]}
          />
        ) : undefined
      }
    >
      {chains.length === 0 ? (
        <Panel>
          <EmptyState
            icon={<Waypoints size={28} />}
            title="No chains in this run"
            body="The correlator reports a chain only when both halves are present. Its absence here is a measured result, not a gap — the control scenario, a brute-force attempt that never succeeds, correctly yields nothing."
          />
        </Panel>
      ) : (
        <>
          {summary && (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Stat
                label="Chains found"
                value={String(chains.length)}
                icon={<Waypoints size={16} />}
                accent
                hint="Every one spans both telemetry layers."
              />
              <Stat
                label="Internal identities reached"
                value={String(summary.entities)}
                icon={<Layers size={16} />}
                hint="Credentials or accounts an external address ended up driving."
              />
              <Stat
                label="Corroborated independently"
                value={`${summary.corroborated} of ${chains.length}`}
                icon={<ShieldCheck size={16} />}
                hint="A detector flagged the internal identity without being told about the chain."
              />
              <Stat
                label="Longest span"
                value={summary.longest > 0 ? formatDuration(summary.longest) : "—"}
                icon={<Clock size={16} />}
                hint="First external event to last internal one — the window an attacker had."
              />
            </div>
          )}

          <div className="space-y-5">
            {chains.map((chain) => (
              <ChainCard key={chain.id} chain={chain} view={view} />
            ))}
          </div>
        </>
      )}
    </PageHeader>
  );
}

function ChainCard({ chain, view }: { chain: AttackChain; view: View }) {
  const corroborated = chain.steps.some((step) => step.stage === "internal abuse");

  return (
    <Panel>
      <PanelHead
        title={`${chain.source_ip} → ${chain.internal_entity_id}`}
        subtitle={`${chain.steps.length} steps · ${chain.internal_entity_type.replace("_", " ")}`}
        action={
          <div className="flex items-center gap-2">
            <TypeChip type="ip" />
            <ArrowRight size={13} className="text-[var(--color-ink-faint)]" />
            <TypeChip type={chain.internal_entity_type} />
          </div>
        }
      />

      {view === "figure" ? (
        <>
          <div className="px-5">
            <ChainSwimlane chain={chain} />
          </div>
          <div className="grid gap-5 px-5 pt-4 pb-5 lg:grid-cols-[1fr_260px]">
            <div>
              {chain.narrative && (
                <p className="rounded-card bg-[var(--color-surface-2)] px-4 py-3 text-xs leading-relaxed whitespace-pre-line text-[var(--color-ink-dim)]">
                  {chain.narrative}
                </p>
              )}
              <div className="mt-4">
                <h3 className="mb-2.5 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                  Where the time went
                </h3>
                <ChainDwellBars chain={chain} />
              </div>
            </div>
            <div className="space-y-4">
              <ConfidenceMeter value={chain.confidence} corroborated={corroborated} />
              <Button
                variant="ghost"
                size="sm"
                to={`/network?focus=${encodeURIComponent(`${chain.internal_entity_type}:${chain.internal_entity_id}`)}`}
                className="w-full"
              >
                <ListTree size={14} /> Open on the network map
              </Button>
            </div>
          </div>
        </>
      ) : (
        <StepTable chain={chain} />
      )}
    </Panel>
  );
}

/** The same chain as text — copyable, printable, and readable without colour. */
function StepTable({ chain }: { chain: AttackChain }) {
  return (
    <div className="overflow-x-auto px-5 pb-5">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-line)] text-left text-[11px] tracking-wide text-[var(--color-ink-faint)] uppercase">
            <th className="py-2.5 pr-3 font-medium">Stage</th>
            <th className="px-3 py-2.5 font-medium">Entity</th>
            <th className="px-3 py-2.5 font-medium">When</th>
            <th className="px-3 py-2.5 text-right font-medium">Since previous</th>
            <th className="py-2.5 pl-3 font-medium">What was observed</th>
          </tr>
        </thead>
        <tbody>
          {chain.steps.map((step, index) => {
            const previous = chain.steps[index - 1];
            return (
              <tr key={index} className="border-b border-[var(--color-line)]/50 last:border-0">
                <td className="py-2.5 pr-3 font-medium">{step.stage}</td>
                <td className="px-3 py-2.5">
                  <span className="font-mono text-xs">{step.entity_id}</span>
                  <span className="ml-2 text-[10px] text-[var(--color-ink-faint)]">
                    {step.entity_type}
                  </span>
                </td>
                <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-ink-dim)]">
                  {new Date(step.at).toLocaleString()}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-[11px] tabular-nums text-[var(--color-ink-faint)]">
                  {previous ? formatGap(previous.at, step.at) : "—"}
                </td>
                <td className="py-2.5 pl-3 text-xs text-[var(--color-ink-dim)]">{step.detail}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-[11px] text-[var(--color-ink-faint)]">
        Confidence {chain.confidence.toFixed(2)}.{" "}
        <Link to="/help#chains" className="text-[var(--color-accent)] hover:underline">
          What confidence means
        </Link>
        .
      </p>
    </div>
  );
}
