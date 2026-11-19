/**
 * A case as a timeline.
 *
 * A case is an assertion that several findings are one incident. Listed as a set of rows, that
 * assertion has to be taken on trust; placed on a shared time axis, it either holds together or
 * visibly does not — an alert three weeks away from everything else in the case is a question
 * the analyst should be asked, and a list will never ask it.
 *
 * Three things the first version got wrong, all of which made it unreadable:
 *
 * 1. **One row per chain *step*.** A four-step chain became four rows, and a case holding the
 *    same path from two runs became eight — so the figure read as "external brute force, external
 *    brute force, breach, breach, …" with no explanation for the repetition. A chain is one piece
 *    of evidence, so it is now one row: a span from its first step to its last, with a tick at
 *    each step. Twelve rows became six.
 * 2. **Every alert row was labelled with the entity id.** Every alert in a case is usually about
 *    the *same* entity — that is what makes it a case — so the labels were four identical strings
 *    and nothing said what distinguished the rows. They now carry the detector and the window,
 *    which is what actually differs, and the run when a case spans more than one.
 * 3. **No axis.** The only time information was a start and an end caption under the plot, so a
 *    bar's position meant nothing without measuring it by eye. There are now dated gridlines.
 */

import { useMemo } from "react";
import type { CaseMember } from "../pages/Investigations";
import { useElementWidth } from "../lib/use-measure";

interface Row {
  /** What distinguishes this row from the others. */
  label: string;
  /** Detector, window, confidence — whatever qualifies the row. */
  sub: string;
  title: string;
  start: number;
  end: number;
  /** Instants inside the span. Chain steps; empty for an alert window. */
  marks: { at: number; label: string }[];
  kind: "alert" | "chain";
}

const DAY = 86_400_000;
const HOUR = 3_600_000;

/**
 * Gridline spacing, chosen so the axis lands on times a human would have picked.
 *
 * Ordered coarsest-last; the first step that yields at most eight lines wins. Without this the
 * axis either has two labels (useless) or forty (unreadable), depending entirely on how long the
 * incident happened to run.
 */
const STEPS = [HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY];

function gridlines(first: number, last: number): { at: number; label: string }[] {
  const span = Math.max(last - first, 1);
  const step = STEPS.find((candidate) => span / candidate <= 8) ?? STEPS[STEPS.length - 1]!;

  // Align to a real boundary rather than to the first event: a line at "Mar 9, 00:00" is a date
  // the analyst can reason about, one at "Mar 9, 00:00:35" is the timestamp of an event that
  // happens to be first.
  const anchor = new Date(first);
  if (step >= DAY) anchor.setHours(0, 0, 0, 0);
  else anchor.setMinutes(0, 0, 0);

  const format = (at: number) =>
    step >= DAY
      ? new Date(at).toLocaleDateString([], { month: "short", day: "numeric" })
      : new Date(at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" });

  // The plot origin always gets a tick. Aligning to a boundary means the first *aligned* line
  // usually falls after the earliest event — the case starting at 00:00:35 put its first label
  // on the following midnight — leaving the whole left-hand region of the plot undated.
  const out: { at: number; label: string }[] = [{ at: first, label: format(first) }];
  for (let at = anchor.getTime(); at <= last; at += step) {
    // Skip a boundary that would sit on top of the origin tick's label.
    if (at < first || (at - first) / span < 0.08) continue;
    out.push({ at, label: format(at) });
  }
  return out;
}

export function CaseTimeline({ members }: { members: CaseMember[] }) {
  const [ref, width] = useElementWidth<HTMLDivElement>();

  const rows = useMemo<Row[]>(() => {
    // Only worth naming the run when the case draws on more than one; otherwise it is a constant
    // repeated on every row. When it *is* shown, it is the thing that explains why two rows look
    // identical — the same finding, re-derived by a later run.
    const runs = new Set(
      members.flatMap((member) =>
        member.alert ? [member.alert.run_id] : member.chain ? [member.chain.run_id] : [],
      ),
    );
    const showRun = runs.size > 1;
    const short = (runId: string) => `run ${runId.slice(0, 6)}`;

    const out: Row[] = [];
    for (const member of members) {
      if (member.alert) {
        const alert = member.alert;
        out.push({
          label: `window ${alert.window_index} · ${alert.entity_id}`,
          sub: [alert.detector, showRun ? short(alert.run_id) : ""].filter(Boolean).join(" · "),
          title: `${alert.detector} scored ${alert.entity_id} in window ${alert.window_index}: ${new Date(
            alert.window_start,
          ).toLocaleString()} to ${new Date(alert.window_end).toLocaleString()}`,
          start: new Date(alert.window_start).getTime(),
          end: new Date(alert.window_end).getTime(),
          marks: [],
          kind: "alert",
        });
      }
      if (member.chain) {
        const chain = member.chain;
        const marks = chain.steps
          .map((step) => ({ at: new Date(step.at).getTime(), label: step.stage }))
          .filter((mark) => Number.isFinite(mark.at))
          .sort((a, b) => a.at - b.at);
        if (marks.length === 0) continue;
        out.push({
          label: `${chain.source_ip} → ${chain.internal_entity_id}`,
          // Confidence is deliberately not repeated here: it is on the evidence row below and on
          // the chains page, and including it truncated the run tag away — which is the one piece
          // of text that explains why two rows look identical.
          sub: [`${chain.steps.length} steps`, showRun ? short(chain.run_id) : ""]
            .filter(Boolean)
            .join(" · "),
          title: chain.steps
            .map((step) => `${new Date(step.at).toLocaleString()} — ${step.stage}`)
            .join("\n"),
          start: marks[0]!.at,
          end: marks[marks.length - 1]!.at,
          marks,
          kind: "chain",
        });
      }
    }
    return out.filter((row) => Number.isFinite(row.start)).sort((a, b) => a.start - b.start);
  }, [members]);

  if (rows.length === 0) {
    return (
      <p className="text-xs text-[var(--color-ink-faint)]">
        Attach an alert or a chain and its timing appears here.
      </p>
    );
  }

  const first = Math.min(...rows.map((row) => row.start));
  const last = Math.max(...rows.map((row) => row.end));
  const span = Math.max(last - first, 1);

  const labelWidth = 208;
  const padRight = 20;
  const headerHeight = 24;
  const rowHeight = 34;
  const plot = Math.max(width - labelWidth - padRight, 80);
  const height = headerHeight + rows.length * rowHeight + 6;

  const x = (at: number) => labelWidth + ((at - first) / span) * plot;
  const lines = gridlines(first, last);

  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <>
          <svg width={width} height={height} role="img" aria-label={ariaFor(rows, first, last)}>
            {lines.map((line, index) => (
              <g key={line.at}>
                <line
                  x1={x(line.at)}
                  x2={x(line.at)}
                  y1={headerHeight - 6}
                  y2={height - 6}
                  stroke="var(--color-line)"
                  strokeDasharray="2 4"
                />
                {/* The first label would hang off the left edge of the plot and the last off the
                    right, so the outermost two anchor inwards instead of centring. */}
                <text
                  x={x(line.at)}
                  y={12}
                  fontSize={10}
                  fill="var(--color-ink-faint)"
                  textAnchor={
                    index === 0 ? "start" : index === lines.length - 1 ? "end" : "middle"
                  }
                >
                  {line.label}
                </text>
              </g>
            ))}

            {rows.map((row, index) => {
              const y = headerHeight + index * rowHeight + rowHeight / 2;
              const left = x(row.start);
              // A chain whose steps all fall in one minute, or an alert with a zero-length
              // window, would otherwise render as nothing at all.
              const barWidth = Math.max(x(row.end) - left, 4);
              return (
                <g key={index}>
                  <title>{row.title}</title>
                  <text x={0} y={y - 2} fontSize={10.5} fill="var(--color-ink-dim)">
                    {truncate(row.label, labelWidth - 10)}
                  </text>
                  <text x={0} y={y + 10} fontSize={9.5} fill="var(--color-ink-faint)">
                    {truncate(row.sub, labelWidth - 10)}
                  </text>

                  {row.kind === "alert" ? (
                    <rect
                      x={left}
                      y={y - 4.5}
                      width={barWidth}
                      height={9}
                      rx={4}
                      fill="var(--color-series-1)"
                    />
                  ) : (
                    <>
                      <line
                        x1={left}
                        x2={left + barWidth}
                        y1={y}
                        y2={y}
                        stroke="var(--color-critical)"
                        strokeWidth={2}
                        strokeOpacity={0.45}
                      />
                      {row.marks.map((mark, markIndex) => (
                        <circle
                          key={markIndex}
                          cx={x(mark.at)}
                          cy={y}
                          r={4}
                          fill="var(--color-critical)"
                          stroke="var(--color-surface)"
                          strokeWidth={1.5}
                        >
                          <title>
                            {`${mark.label} — ${new Date(mark.at).toLocaleString()}`}
                          </title>
                        </circle>
                      ))}
                    </>
                  )}
                </g>
              );
            })}
          </svg>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10.5px] text-[var(--color-ink-faint)]">
            <span className="inline-flex items-center gap-1.5">
              <span
                className="h-2 w-5 rounded-chip"
                style={{ background: "var(--color-series-1)" }}
                aria-hidden
              />
              Alert — the window it was scored over
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span
                className="size-2 rounded-chip"
                style={{ background: "var(--color-critical)" }}
                aria-hidden
              />
              Chain — one row per path, a tick at each step
            </span>
            <span className="ml-auto">Hover a row for its detail.</span>
          </div>
        </>
      )}
    </div>
  );
}

function ariaFor(rows: Row[], first: number, last: number): string {
  return `Case timeline, ${rows.length} pieces of evidence between ${new Date(
    first,
  ).toLocaleString()} and ${new Date(last).toLocaleString()}.`;
}

/**
 * Clip a label to what fits.
 *
 * SVG text neither wraps nor clips, so an over-long label runs straight across the plot. Estimated
 * from an average glyph advance rather than measured — a hidden canvas per label is not worth it,
 * and being a character conservative costs nothing.
 */
function truncate(text: string, pixels: number): string {
  const limit = Math.max(6, Math.floor(pixels / 5.6));
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}
