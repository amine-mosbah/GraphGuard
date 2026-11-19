/**
 * Charts.
 *
 * Recharts writes our colours straight through to SVG presentation attributes, and those
 * are parsed as CSS declarations — so `fill="var(--color-accent)"` resolves like any
 * other custom property. That is why nothing here reads computed styles: the charts flip
 * with the theme for free, which is exactly what the old hand-rolled donut failed to do
 * when its segment colours were hard-coded hexes at the call site.
 *
 * Layer hues reuse the existing accent/low/medium tokens rather than introducing three
 * more. Every one of them is already declared in all three scopes (@theme, the light
 * override and .panel-brand), so the invariant at the top of styles.css costs nothing.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TimelineBucket } from "../lib/types";

export interface BehaviourProfilePoint {
  label: string;
  value: number;
}

/**
 * A compact profile of bounded behaviour shares.
 *
 * Only 0–1 features belong here. Counts and unbounded ratios deliberately stay out:
 * putting them on the same radar would imply a shared scale that the data does not have.
 */
export function BehaviourProfileChart({ data }: { data: BehaviourProfilePoint[] }) {
  const reducedMotion = usePrefersReducedMotion();
  const rows = data.map((point) => ({ ...point, percent: Math.round(point.value * 100) }));

  if (rows.length < 3) {
    return (
      <div className="grid h-[190px] place-items-center text-xs text-[var(--color-ink-faint)]">
        Not enough bounded features for a profile.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={190} initialDimension={{ width: 320, height: 190 }}>
      <RadarChart data={rows} outerRadius="68%" margin={{ top: 12, right: 34, bottom: 8, left: 34 }}>
        <PolarGrid stroke="var(--color-line)" radialLines={false} />
        <PolarAngleAxis
          dataKey="label"
          tick={{ fill: "var(--color-ink-faint)", fontSize: 10 }}
          tickLine={false}
        />
        <Tooltip
          formatter={(value) => [`${Number(value).toFixed(0)}%`, "Observed share"]}
          contentStyle={{
            background: "var(--color-surface-2)",
            border: "1px solid var(--color-line)",
            borderRadius: 10,
            color: "var(--color-ink)",
            fontSize: 12,
          }}
          cursor={false}
          isAnimationActive={false}
        />
        <Radar
          dataKey="percent"
          stroke="var(--color-accent)"
          fill="var(--color-accent)"
          fillOpacity={0.2}
          strokeWidth={2}
          dot={{ fill: "var(--color-surface)", stroke: "var(--color-accent)", strokeWidth: 2, r: 3 }}
          isAnimationActive={!reducedMotion}
        />
      </RadarChart>
    </ResponsiveContainer>
  );
}

/**
 * Recharts animates in JavaScript, so the global `prefers-reduced-motion` rule in
 * styles.css — which only clamps CSS animation and transition durations — does not
 * reach it. docs/DESIGN.md §11.2 requires honouring the setting, so it is checked here.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/**
 * Round an axis maximum up to a value a human would have chosen.
 *
 * Left to itself an axis lands on 1,247 events and asks the analyst to do arithmetic.
 * Snapping the step to 1, 2, 5 or 10 times a power of ten gives four-to-five ticks at
 * round numbers, whatever the magnitude of the run.
 */
function niceScale(max: number): { top: number; ticks: number[] } {
  if (max <= 0) return { top: 4, ticks: [0, 1, 2, 3, 4] };
  const rough = max / 4;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const normalised = rough / magnitude;
  const nice = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10;
  const step = Math.max(nice * magnitude, 1);
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = 0; value <= top + step / 2; value += step) ticks.push(value);
  return { top, ticks };
}

/**
 * How many x labels to skip so they do not collide.
 *
 * Returned as a Recharts `interval`, which counts ticks *skipped* between rendered ones,
 * hence the −1.
 */
function labelInterval(count: number): number {
  if (count <= 7) return 0;
  if (count <= 14) return 1;
  if (count <= 21) return 2;
  return Math.ceil(count / 7) - 1;
}

const LAYER_META: { key: string; label: string; color: string }[] = [
  { key: "ai", label: "AI platform", color: "var(--color-accent)" },
  { key: "http", label: "HTTP gateway", color: "var(--color-low)" },
  { key: "network", label: "Network", color: "var(--color-medium)" },
];

interface Column {
  at: string;
  total: number;
  errors: number;
  refusals: number;
  layers: Record<string, number>;
}

type ChartRow = Column & Record<string, number | string | Record<string, number>>;

/**
 * Label a bucket at the resolution the run actually spans.
 *
 * An hourly clock time is the obvious choice and the wrong one for a thirteen-day run:
 * every rendered tick lands on the same hour and the axis reads "01:00, 01:00, 01:00".
 * Past a day, the date is the part that distinguishes one column from another.
 */
function bucketLabel(iso: string, spanMs: number): string {
  const date = new Date(iso);
  if (spanMs > 3 * 86_400_000) return date.toLocaleDateString([], { month: "short", day: "numeric" });
  if (spanMs > 86_400_000)
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" });
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TimelineTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload?: Column }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  const present = LAYER_META.filter((layer) => (row.layers[layer.key] ?? 0) > 0);
  return (
    <div className="panel elev-3 px-3 py-2.5 text-xs">
      <div className="font-medium text-[var(--color-ink)]">{new Date(row.at).toLocaleString()}</div>
      <div className="mt-2 space-y-1">
        {present.map((layer) => (
          <div key={layer.key} className="flex items-center gap-2">
            {/* A 3px tick rather than a dot: it reads as a slice of the bar it labels. */}
            <span
              className="h-2.5 w-[3px] rounded-chip"
              style={{ background: layer.color }}
              aria-hidden
            />
            <span className="text-[var(--color-ink-dim)]">{layer.label}</span>
            <span className="ml-auto tabular-nums text-[var(--color-ink)]">
              {row.layers[layer.key]?.toLocaleString()}
            </span>
          </div>
        ))}
        <div className="flex items-center gap-2 border-t border-[var(--color-line)] pt-1">
          <span className="text-[var(--color-ink-dim)]">Errors</span>
          <span className="ml-auto tabular-nums text-[var(--color-critical)]">
            {row.errors.toLocaleString()}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[var(--color-ink-dim)]">Refusals</span>
          <span className="ml-auto tabular-nums text-[var(--color-high)]">
            {row.refusals.toLocaleString()}
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Event volume over the run, stacked by telemetry layer.
 *
 * The layer split is the point: the report's claim is that one graph spans the AI and
 * gateway layers, and this is where that stops being an assertion and becomes something
 * the reader can see. The backend has always sent `by_layer`; the previous chart threw
 * it away during downsampling.
 */
export function VolumeChart({ buckets }: { buckets: TimelineBucket[] }) {
  const reducedMotion = usePrefersReducedMotion();

  const columns = useMemo<Column[]>(() => {
    if (buckets.length === 0) return [];
    // Downsample to at most 60 columns by summing a stride, so a long run stays legible.
    const stride = Math.max(1, Math.ceil(buckets.length / 60));
    const out: Column[] = [];
    for (let i = 0; i < buckets.length; i += stride) {
      const slice = buckets.slice(i, i + stride);
      const layers: Record<string, number> = {};
      let total = 0;
      let errors = 0;
      let refusals = 0;
      for (const bucket of slice) {
        total += bucket.total;
        errors += bucket.errors;
        refusals += bucket.refusals;
        for (const [layer, count] of Object.entries(bucket.by_layer ?? {})) {
          layers[layer] = (layers[layer] ?? 0) + count;
        }
      }
      out.push({ at: slice[0]?.at ?? "", total, errors, refusals, layers });
    }
    return out;
  }, [buckets]);

  const activeLayers = useMemo(
    () => LAYER_META.filter((layer) => columns.some((c) => (c.layers[layer.key] ?? 0) > 0)),
    [columns],
  );

  const rows = useMemo<ChartRow[]>(() => {
    const first = columns[0];
    const last = columns[columns.length - 1];
    const span =
      first && last ? new Date(last.at).getTime() - new Date(first.at).getTime() : 0;
    return columns.map((column) => {
      const row: ChartRow = { ...column, label: bucketLabel(column.at, span) };
      for (const layer of LAYER_META) row[layer.key] = column.layers[layer.key] ?? 0;
      return row;
    });
  }, [columns]);

  const scale = useMemo(
    () => niceScale(Math.max(0, ...columns.map((c) => c.total))),
    [columns],
  );

  if (columns.length === 0) {
    return (
      <div className="grid h-[260px] place-items-center text-xs text-[var(--color-ink-faint)]">
        No telemetry in this run.
      </div>
    );
  }

  const axisTick = { fill: "var(--color-ink-faint)", fontSize: 11 };

  return (
    <div>
      {/* initialDimension avoids the zero-size first paint, which otherwise flashes an
          empty box before the container measures itself. */}
      <ResponsiveContainer width="100%" height={230} initialDimension={{ width: 640, height: 230 }}>
        <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -12 }} barCategoryGap="18%">
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" vertical={false} />
          <XAxis
            dataKey="label"
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            interval={labelInterval(rows.length)}
            minTickGap={4}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            width={52}
            domain={[0, scale.top]}
            ticks={scale.ticks}
            allowDecimals={false}
          />
          <Tooltip
            content={<TimelineTooltip />}
            cursor={{ fill: "var(--color-raise)" }}
            isAnimationActive={false}
          />
          {activeLayers.map((layer, index) => (
            <Bar
              key={layer.key}
              dataKey={layer.key}
              stackId="layers"
              name={layer.label}
              fill={layer.color}
              radius={index === activeLayers.length - 1 ? [3, 3, 0, 0] : undefined}
              isAnimationActive={!reducedMotion}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-[var(--color-ink-faint)]">
        <span className="tabular-nums">{new Date(columns[0]!.at).toLocaleDateString()}</span>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {activeLayers.map((layer) => (
            <Key key={layer.key} color={layer.color} label={layer.label} />
          ))}
        </div>
        <span className="ml-auto tabular-nums">
          {new Date(columns[columns.length - 1]!.at).toLocaleDateString()}
        </span>
      </div>
    </div>
  );
}

function Key({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="size-2 rounded-[3px]" style={{ background: color }} aria-hidden />
      {label}
    </span>
  );
}

export interface DonutSegment {
  label: string;
  value: number;
  /** Any CSS colour, including a var(). */
  color: string;
}

/**
 * Alert states as a ring.
 *
 * The value goes in the hole — which is the whole reason a donut is not a pie. The
 * previous version rendered it in the right-hand column and left the middle empty.
 */
export function StateDonut({
  segments,
  centreValue,
  centreLabel,
}: {
  segments: DonutSegment[];
  centreValue: string;
  centreLabel: string;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);

  if (total === 0) {
    return (
      <div className="grid h-[150px] place-items-center text-xs text-[var(--color-ink-faint)]">
        No alerts to summarise.
      </div>
    );
  }

  return (
    <div className="flex items-center gap-6">
      <div className="relative size-[150px] shrink-0">
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 150, height: 150 }}>
          <PieChart>
            <Pie
              data={segments}
              dataKey="value"
              nameKey="label"
              innerRadius={52}
              outerRadius={72}
              paddingAngle={1.5}
              startAngle={90}
              endAngle={-270}
              stroke="none"
              isAnimationActive={!reducedMotion}
            >
              {segments.map((segment) => (
                <Cell key={segment.label} fill={segment.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
          <div>
            <div className="text-2xl leading-none font-semibold tabular-nums">{centreValue}</div>
            <div className="mt-1 text-[10px] tracking-wide text-[var(--color-ink-faint)] uppercase">
              {centreLabel}
            </div>
          </div>
        </div>
      </div>

      <ul className="min-w-0 flex-1 space-y-2 text-xs">
        {segments.map((segment) => (
          <li key={segment.label} className="flex items-center gap-2.5">
            <span
              className="size-2.5 shrink-0 rounded-[4px]"
              style={{ background: segment.color }}
              aria-hidden
            />
            <span className="truncate text-[var(--color-ink-dim)]">{segment.label}</span>
            <span className="ml-auto shrink-0 tabular-nums text-[var(--color-ink)]">
              {segment.value.toLocaleString()}
            </span>
            <span className="w-9 shrink-0 text-right tabular-nums text-[var(--color-ink-faint)]">
              {Math.round((segment.value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface RankedBar {
  label: string;
  value: number;
  /** Rendered at the end of the bar. Falls back to the value at two decimals. */
  display?: string;
  /** Marks the bars that are the point of the chart — a graph detector against a baseline. */
  emphasis?: boolean;
}

/**
 * A ranked horizontal bar chart for a bounded score.
 *
 * Horizontal rather than vertical because the categories are detector names — `abnormal_subgraph:
 * similarity` is not a thing that fits under a vertical bar without rotating the label 45° and
 * making the reader tilt their head.
 *
 * One hue, not seven. This encodes magnitude within a single measure, so giving each detector its
 * own colour would spend the categorical palette on information the position already carries, and
 * would imply the colours mean something when the ranking is the whole message. The two bars that
 * are being compared are separated by weight instead, which survives greyscale and colour-blindness.
 *
 * `reference` draws the line the score has to beat to mean anything — 0.5 for ROC-AUC, the
 * positive rate for average precision. Without it a reader has no way to know that 0.48 is worse
 * than a coin toss, and the honest reporting of the below-chance cells depends on that being
 * visible rather than stated in a caption.
 */
export function RankedBars({
  rows,
  reference,
  referenceLabel,
  max = 1,
}: {
  rows: RankedBar[];
  reference?: number;
  referenceLabel?: string;
  max?: number;
}) {
  if (rows.length === 0) {
    return (
      <div className="grid h-24 place-items-center text-xs text-[var(--color-ink-faint)]">
        Nothing to compare.
      </div>
    );
  }

  return (
    <div>
      <ul className="space-y-2.5">
        {rows.map((row) => {
          const percent = Math.max(0, Math.min(row.value / max, 1)) * 100;
          const below = reference !== undefined && row.value < reference;
          return (
            <li key={row.label} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3">
              <span
                className={`truncate font-mono text-[11px] ${
                  row.emphasis ? "text-[var(--color-ink)]" : "text-[var(--color-ink-dim)]"
                }`}
                title={row.label}
              >
                {row.label}
              </span>
              <span
                className={`tabular-nums text-xs ${
                  below ? "text-[var(--color-critical)]" : "text-[var(--color-ink)]"
                }`}
              >
                {row.display ?? row.value.toFixed(3)}
              </span>
              <span className="relative col-span-2 mt-1 block h-2 rounded-chip bg-[var(--color-raise)]">
                <span
                  className="block h-full rounded-chip"
                  style={{
                    width: `${Math.max(percent, 1)}%`,
                    background: below
                      ? "var(--color-critical)"
                      : row.emphasis
                        ? "var(--color-series-1)"
                        : "var(--color-ink-faint)",
                    opacity: row.emphasis || below ? 1 : 0.5,
                  }}
                />
                {reference !== undefined && (
                  <span
                    aria-hidden
                    className="absolute inset-y-[-3px] w-px bg-[var(--color-ink-faint)]"
                    style={{ left: `${(reference / max) * 100}%` }}
                  />
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {reference !== undefined && (
        <p className="mt-3 text-[11px] text-[var(--color-ink-faint)]">
          The vertical rule is {referenceLabel ?? `${reference.toFixed(2)}`}. Anything to its left
          is worse than that, and is drawn in the critical tone rather than quietly ranked last.
        </p>
      )}
    </div>
  );
}

/**
 * Precision and recall at each cut-off, as two lines.
 *
 * Both are proportions on the same 0–1 scale, so they share one axis — a second y-axis for the
 * second series would be the classic way to make two unrelated scales look comparable, and is
 * never correct.
 *
 * What the shape says: precision falling as k grows while recall barely moves is a detector whose
 * true positives are all near the top of the queue, which is exactly what an analyst working a
 * ranked list wants and is invisible in a single AUC number.
 */
export function AtKChart({
  precision,
  recall,
}: {
  precision: Record<string, number>;
  recall: Record<string, number>;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const ks = Object.keys(precision)
    .map(Number)
    .filter((k) => Number.isFinite(k))
    .sort((a, b) => a - b);

  if (ks.length === 0) {
    return (
      <div className="grid h-[180px] place-items-center text-xs text-[var(--color-ink-faint)]">
        No cut-offs recorded for this detector.
      </div>
    );
  }

  const rows = ks.map((k) => ({
    k: `k=${k}`,
    precision: precision[String(k)] ?? 0,
    recall: recall[String(k)] ?? 0,
  }));
  const axisTick = { fill: "var(--color-ink-faint)", fontSize: 11 };

  return (
    <div>
      <ResponsiveContainer width="100%" height={180} initialDimension={{ width: 420, height: 180 }}>
        {/* No negative left margin here: the ticks are "0.25"-style decimals and a pulled-in
            axis clipped their first glyph. The y width below is what reserves room for them. */}
        <LineChart data={rows} margin={{ top: 8, right: 14, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" vertical={false} />
          <XAxis dataKey="k" axisLine={false} tickLine={false} tick={axisTick} />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            width={46}
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tickFormatter={(value: number) => value.toFixed(2)}
          />
          <Tooltip
            formatter={(value, name) => [Number(value).toFixed(3), String(name)]}
            contentStyle={{
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-line)",
              borderRadius: 10,
              color: "var(--color-ink)",
              fontSize: 12,
            }}
            cursor={{ stroke: "var(--color-line)" }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="precision"
            name="Precision"
            stroke="var(--color-series-1)"
            strokeWidth={2}
            dot={{ r: 4, strokeWidth: 2, stroke: "var(--color-surface)" }}
            isAnimationActive={!reducedMotion}
          />
          <Line
            type="monotone"
            dataKey="recall"
            name="Recall"
            stroke="var(--color-series-2)"
            strokeWidth={2}
            strokeDasharray="5 3"
            dot={{ r: 4, strokeWidth: 2, stroke: "var(--color-surface)" }}
            isAnimationActive={!reducedMotion}
          />
        </LineChart>
      </ResponsiveContainer>
      {/* The dash pattern carries the same distinction as the hue, so the two lines stay
          separable in greyscale and for a colour-blind reader. */}
      <div className="mt-1 flex items-center gap-4 px-1 text-[11px] text-[var(--color-ink-faint)]">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4" style={{ background: "var(--color-series-1)" }} aria-hidden />
          Precision — of the top k, how many were real
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-0.5 w-4"
            style={{
              backgroundImage:
                "repeating-linear-gradient(90deg, var(--color-series-2) 0 5px, transparent 5px 8px)",
            }}
            aria-hidden
          />
          Recall — of everything real, how much the top k caught
        </span>
      </div>
    </div>
  );
}
