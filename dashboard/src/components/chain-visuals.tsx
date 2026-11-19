/**
 * Figures for cross-layer attack chains.
 *
 * A chain is the one finding in this system that a table cannot carry. An alert is an entity
 * and a score, which a row states perfectly well. A chain is a *shape*: two telemetry layers,
 * a crossing between them, and the time that elapsed at each step — and the argument the
 * project makes is precisely that the crossing exists and is only visible because both layers
 * are vertices in one graph. Rendering that as a list of four bullet points throws away the
 * part that is the evidence.
 *
 * So the swimlane below is not decoration. The lane boundary *is* the claim, and the pivot is
 * drawn as the moment the path crosses it.
 *
 * Hand-written SVG rather than a chart library: none of these are a chart type Recharts has —
 * a two-lane path over a time axis is closer to a Gantt than to anything in its catalogue, and
 * the layout rules here (collision-avoiding node spacing, alternating label sides) would be
 * fought rather than helped by a cartesian grid abstraction.
 */

import { Fragment } from "react";
import type { AttackChain, ChainStep } from "../lib/types";
import { useElementWidth } from "../lib/use-measure";
import { useNodeTypeMeta } from "./ui";

/**
 * Which telemetry layer a step belongs to.
 *
 * Derived from the entity type rather than carried on the step, because that is what the
 * correlator actually keys on: an `ip` is a gateway-layer vertex, everything else in a chain
 * is an identity inside the platform. Keeping the derivation in one place means the lane a
 * step lands in and the colour it is drawn with cannot disagree.
 */
function laneOf(step: ChainStep): 0 | 1 {
  return step.entity_type === "ip" || step.entity_type === "host" ? 0 : 1;
}

const LANES = [
  { label: "External", sub: "HTTP gateway telemetry" },
  { label: "Internal", sub: "AI platform telemetry" },
];

/** "6.1 h", "12 min", "3 d" — an elapsed time at the resolution a human reads it at. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const minutes = ms / 60_000;
  if (minutes < 1) return "<1 min";
  if (minutes < 90) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

/** The gap between two steps. */
export function formatGap(fromIso: string, toIso: string): string {
  return formatDuration(new Date(toIso).getTime() - new Date(fromIso).getTime());
}

/**
 * The chain as a path across two telemetry layers.
 *
 * Steps are placed at even intervals in time order, not proportionally to the clock. That is a
 * deliberate division of labour between this figure and the dwell bars beside it: real chains
 * are wildly non-uniform in time — this project's has three steps inside nine hours and a fourth
 * four days later — so proportional placement collapses the first three into one blob and leaves
 * two-thirds of the figure empty. Even spacing makes the *sequence and the layer crossing*
 * legible, which is what this figure is for; the elapsed time is printed on every connector and
 * drawn to scale in `ChainDwellBars`, which is what that one is for. Neither claims the other's
 * job, and nothing here is to scale by implication.
 */
export function ChainSwimlane({ chain }: { chain: AttackChain }) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const palette = useNodeTypeMeta();
  const steps = chain.steps;

  const height = 236;
  // A left gutter for the lane names. Putting them above the lanes collided with the stage
  // labels, which sit above their markers for the same reason axis labels always do.
  const gutter = 104;
  const padRight = 16;
  const laneY = [86, 168];
  const plot = Math.max(width - gutter - padRight, 120);
  const slot = plot / Math.max(steps.length, 1);

  if (width === 0) return <div ref={ref} style={{ height }} />;

  const points = steps.map((step, index) => ({
    step,
    x: gutter + slot * (index + 0.5),
    y: laneY[laneOf(step)] ?? laneY[0]!,
    lane: laneOf(step),
    meta: palette[step.entity_type] ?? {
      color: "var(--color-ink-faint)",
      letter: "?",
      label: step.entity_type,
    },
  }));

  // The pivot: the first step that lands in a different lane from the one before it. This is
  // the crossing the whole finding rests on, so it gets the emphasis.
  const pivotIndex = points.findIndex(
    (point, index) => index > 0 && point.lane !== points[index - 1]!.lane,
  );

  return (
    <div ref={ref} className="w-full">
      <svg width={width} height={height} role="img" aria-label={ariaFor(chain)}>
        {LANES.map((lane, index) => {
          const y = laneY[index] ?? 0;
          return (
            <Fragment key={lane.label}>
              <rect
                x={gutter - 12}
                y={y - 34}
                width={plot + 24}
                height={68}
                rx={12}
                fill={index === 0 ? "var(--color-raise-subtle)" : "transparent"}
              />
              <text x={0} y={y - 4} fontSize={11} fontWeight={600} fill="var(--color-ink-dim)">
                {lane.label}
              </text>
              <text x={0} y={y + 11} fontSize={9.5} fill="var(--color-ink-faint)">
                {lane.sub}
              </text>
            </Fragment>
          );
        })}

        {/* The boundary between the two layers. Dashed, because a chain crossing it is the
            claim being made — a solid rule would read as chrome. */}
        <line
          x1={gutter - 12}
          x2={gutter + plot + 12}
          y1={(laneY[0]! + laneY[1]!) / 2}
          y2={(laneY[0]! + laneY[1]!) / 2}
          stroke="var(--color-line)"
          strokeDasharray="2 5"
        />

        {points.slice(1).map((point, index) => {
          const from = points[index]!;
          const crossing = index + 1 === pivotIndex;
          const midX = (from.x + point.x) / 2;
          return (
            <g key={`edge-${index}`}>
              <path
                d={`M ${from.x} ${from.y} C ${midX} ${from.y}, ${midX} ${point.y}, ${point.x} ${point.y}`}
                fill="none"
                stroke={crossing ? "var(--color-critical)" : "var(--color-ink-faint)"}
                strokeWidth={crossing ? 2 : 1.5}
                strokeOpacity={crossing ? 1 : 0.55}
              />
              {/* A surface-coloured halo, painted under the glyphs. The crossing connector is a
                  curve, so its label lands *on* the stroke rather than beside it; without this
                  the two read as one smudge. */}
              <text
                x={midX}
                y={(from.y + point.y) / 2 - 7}
                fontSize={10}
                fontWeight={crossing ? 600 : 400}
                textAnchor="middle"
                fill={crossing ? "var(--color-critical)" : "var(--color-ink-faint)"}
                stroke="var(--color-surface)"
                strokeWidth={3.5}
                paintOrder="stroke"
                strokeLinejoin="round"
              >
                {formatGap(from.step.at, point.step.at)}
              </text>
            </g>
          );
        })}

        {points.map((point, index) => (
          <g key={`node-${index}`}>
            {/* A 2px surface ring, so a marker that lands on the connector still reads as a
                separate mark rather than a bulge in the line. */}
            <circle
              cx={point.x}
              cy={point.y}
              r={13}
              fill={point.meta.color}
              stroke="var(--color-surface)"
              strokeWidth={2}
            />
            <text
              x={point.x}
              y={point.y + 4}
              fontSize={11}
              fontWeight={700}
              textAnchor="middle"
              fill="var(--color-surface)"
            >
              {point.meta.letter}
            </text>
            <text
              x={point.x}
              y={point.y - 22}
              fontSize={10.5}
              fontWeight={600}
              textAnchor="middle"
              fill="var(--color-ink)"
            >
              {truncate(point.step.stage, slot)}
            </text>
            <text
              x={point.x}
              y={point.y + 28}
              fontSize={9.5}
              textAnchor="middle"
              fill="var(--color-ink-faint)"
            >
              {new Date(point.step.at).toLocaleString([], {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </text>
            <text
              x={point.x}
              y={point.y + 40}
              fontSize={9.5}
              textAnchor="middle"
              fill="var(--color-ink-faint)"
            >
              {truncate(point.step.entity_id, slot)}
            </text>
          </g>
        ))}
      </svg>

      <p className="mt-1 px-1 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
        Steps run left to right in the order they happened; the spacing is even, and the elapsed
        time printed on each connector is the real gap.
        {pivotIndex > 0 && (
          <>
            {" "}
            The red connector is the layer crossing — the point at which an external address
            becomes an internal identity.
          </>
        )}
      </p>
    </div>
  );
}

/**
 * Clip a label to what fits in its slot.
 *
 * SVG text does not wrap and does not clip, so an over-long stage name silently runs across its
 * neighbour's. Estimated from an average glyph advance rather than measured: measuring would mean
 * a hidden canvas per label, and being a character or two conservative costs nothing here.
 */
function truncate(text: string, slotWidth: number): string {
  const limit = Math.max(6, Math.floor((slotWidth - 12) / 5.8));
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function ariaFor(chain: AttackChain): string {
  return `Attack chain from ${chain.source_ip} to ${chain.internal_entity_id}, ${chain.steps
    .map((step) => step.stage)
    .join(", then ")}.`;
}

/**
 * Elapsed time between steps, as a proportional bar.
 *
 * Answers the question the swimlane deliberately gives up in order to stay readable: where
 * did the time actually go? On this project's chain almost all of it is between the pivot and
 * the internal detector firing — which is the dwell time an analyst cares about, and the number
 * that would justify running detection more often.
 */
export function ChainDwellBars({ chain }: { chain: AttackChain }) {
  const gaps = chain.steps.slice(1).map((step, index) => {
    const from = chain.steps[index]!;
    return {
      from: from.stage,
      to: step.stage,
      ms: Math.max(new Date(step.at).getTime() - new Date(from.at).getTime(), 0),
      label: formatGap(from.at, step.at),
    };
  });
  const longest = Math.max(1, ...gaps.map((gap) => gap.ms));

  if (gaps.length === 0) return null;

  return (
    <ul className="space-y-2.5">
      {gaps.map((gap, index) => (
        <li key={index} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
          <span className="truncate text-[11px] text-[var(--color-ink-dim)]">
            {gap.from} → {gap.to}
          </span>
          <span className="tabular-nums text-[11px] text-[var(--color-ink)]">{gap.label}</span>
          <span className="col-span-2 h-1.5 rounded-chip bg-[var(--color-raise)]">
            <span
              className="block h-full rounded-chip bg-[var(--color-series-1)]"
              style={{ width: `${Math.max((gap.ms / longest) * 100, 2)}%` }}
            />
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Confidence as a meter.
 *
 * Deliberately not a gauge or a dial: a chain's confidence is a single bounded number, and a
 * bar against a full-width track answers "how close to certain" in one glance without implying
 * a scale that has regions. The corroboration note underneath is what the number is made of.
 */
export function ConfidenceMeter({
  value,
  corroborated,
}: {
  value: number;
  corroborated: boolean;
}) {
  const percent = Math.round(value * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-[var(--color-ink-dim)]">Correlator confidence</span>
        <span className="text-lg font-semibold tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div
        className="mt-2 h-2 rounded-chip bg-[var(--color-raise)]"
        role="meter"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Correlator confidence"
      >
        <div
          className="h-full rounded-chip bg-[var(--color-series-1)]"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
        {corroborated
          ? "A detector flagged the internal identity independently of the correlation, so the two halves agree without having been told to."
          : "The path is present but no internal detector flagged the identity independently, so this rests on the join alone."}
      </p>
    </div>
  );
}
