import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, FileDown, ShieldAlert } from "lucide-react";
import { api } from "../lib/api";
import type { AlertDetail, AlertState, AlertSummary, Reason } from "../lib/types";
import {
  Button,
  Chip,
  EmptyState,
  FilterChip,
  PageHeader,
  Panel,
  PanelHead,
  Select,
  SeverityBadge,
  SkeletonPanel,
  Spinner,
  StatePill,
  Table,
  Td,
  Textarea,
  Th,
  TypeChip,
  severityOf,
} from "../components/ui";
import type { Severity, SortDirection } from "../components/ui";

const STATE_FILTERS: { value: AlertState | "open" | "all"; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "investigating", label: "Investigating" },
  { value: "true_positive", label: "True positive" },
  { value: "false_positive", label: "False positive" },
];

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

const PAGE_SIZE = 25;

type SortKey = "entity" | "detector" | "window" | "score" | "state";

/**
 * Triage queue.
 *
 * Filters, sort and page live in the URL rather than in component state. That is the
 * one architectural borrowing worth making here: an analyst can send a colleague the
 * link to exactly the view they are looking at, and the browser's back button behaves.
 *
 * Paging is over the rows already in memory. The console fetches one run's alerts once
 * and shares them with the queue, the search box, the notification list and the
 * assistant, so re-fetching per page would mean either duplicating that state or
 * breaking the other four consumers. `total` is threaded through so the page can say
 * plainly when the API had more rows than were fetched, rather than quietly showing a
 * truncated queue as if it were the whole thing.
 */
export function Alerts({
  alerts,
  loading,
  total,
}: {
  alerts: AlertSummary[];
  loading: boolean;
  total?: number;
}) {
  const [params, setParams] = useSearchParams();

  const filter = (params.get("state") ?? "open") as AlertState | "open" | "all";
  const detector = params.get("detector");
  const severity = params.get("severity") as Severity | null;
  const sortKey = (params.get("sort") ?? "score") as SortKey;
  const direction = (params.get("dir") ?? "desc") as SortDirection;
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);

  /** Writing a param to null drops it, so a default never appears in the URL. */
  const setParam = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      // Any change to the filters invalidates the page number.
      if (!("page" in patch)) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const detectors = useMemo(
    () => [...new Set(alerts.map((alert) => alert.detector))].sort(),
    [alerts],
  );

  const filtered = useMemo(() => {
    let next = alerts;
    if (filter === "open") {
      next = next.filter((a) => a.state === "new" || a.state === "investigating");
    } else if (filter !== "all") {
      next = next.filter((a) => a.state === filter);
    }
    if (detector) next = next.filter((a) => a.detector === detector);
    if (severity) next = next.filter((a) => severityOf(a.window_score) === severity);
    return next;
  }, [alerts, filter, detector, severity]);

  const sorted = useMemo(() => {
    const sign = direction === "asc" ? 1 : -1;
    const value = (alert: AlertSummary): string | number => {
      switch (sortKey) {
        case "entity":
          return alert.entity_id;
        case "detector":
          return alert.detector;
        case "window":
          return alert.window_index;
        case "state":
          return alert.state;
        default:
          return alert.window_score;
      }
    };
    return [...filtered].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      if (typeof left === "string" && typeof right === "string") {
        return sign * left.localeCompare(right);
      }
      return sign * (Number(left) - Number(right));
    });
  }, [filtered, sortKey, direction]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const rows = sorted.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  const onSort = useCallback(
    (key: string) => {
      // First click on a new column sorts descending: for a queue ranked by score,
      // "worst first" is what the analyst wants without a second click.
      if (key === sortKey) setParam({ dir: direction === "asc" ? "desc" : "asc" });
      else setParam({ sort: key, dir: "desc" });
    },
    [sortKey, direction, setParam],
  );

  /* Heterogeneous filters flattened into one list, each carrying its own removal, so
     the bar renders without knowing what any individual filter means. */
  const chips: { key: string; label: string; value: string; remove: () => void }[] = [
    ...(filter !== "open"
      ? [
          {
            key: "state",
            label: "State",
            value: STATE_FILTERS.find((f) => f.value === filter)?.label ?? filter,
            remove: () => setParam({ state: null }),
          },
        ]
      : []),
    ...(detector
      ? [
          {
            key: "detector",
            label: "Detector",
            value: detector,
            remove: () => setParam({ detector: null }),
          },
        ]
      : []),
    ...(severity
      ? [
          {
            key: "severity",
            label: "Severity",
            value: severity,
            remove: () => setParam({ severity: null }),
          },
        ]
      : []),
  ];

  if (loading) {
    return (
      <PageHeader title="Triage queue" subtitle="Loading the ranked alert queue…">
        <SkeletonPanel rows={8} />
      </PageHeader>
    );
  }

  const truncated = typeof total === "number" && total > alerts.length;

  return (
    <PageHeader
      title="Triage queue"
      subtitle="Ranked across every window by raw detector score. A high score means the entity is unlike its peers — evidence to weigh, never a verdict."
    >
      <div className="flex flex-wrap items-center gap-2">
        {STATE_FILTERS.map((option) => (
          <Chip
            key={option.value}
            selected={filter === option.value}
            onClick={() => setParam({ state: option.value === "open" ? null : option.value })}
          >
            {option.label}
          </Chip>
        ))}
        <span className="mx-2 h-4 w-px bg-[var(--color-line)]" />
        <Select
          aria-label="Filter by detector"
          value={detector ?? ""}
          onChange={(event) => setParam({ detector: event.target.value || null })}
          className="w-auto py-1.5 text-xs"
        >
          <option value="">All detectors</option>
          {detectors.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by severity"
          value={severity ?? ""}
          onChange={(event) => setParam({ severity: event.target.value || null })}
          className="w-auto py-1.5 text-xs"
        >
          <option value="">All severities</option>
          {SEVERITIES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
        <span className="ml-auto text-xs text-[var(--color-ink-faint)] tabular-nums">
          {sorted.length} of {alerts.length}
        </span>
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {chips.map((chip) => (
            <FilterChip
              key={chip.key}
              label={chip.label}
              value={chip.value}
              onRemove={chip.remove}
            />
          ))}
          <button
            onClick={() => setParam({ state: null, detector: null, severity: null })}
            className="text-xs text-[var(--color-ink-faint)] transition-colors hover:text-[var(--color-ink)]"
          >
            Clear all
          </button>
        </div>
      )}

      {truncated && (
        <p className="text-xs text-[var(--color-medium)]">
          Showing the {alerts.length.toLocaleString()} highest-scoring alerts of{" "}
          {total!.toLocaleString()} in this run.
        </p>
      )}

      <Panel className="overflow-hidden">
        {rows.length === 0 ? (
          <EmptyState
            icon={<ShieldAlert size={26} />}
            title="Nothing matches"
            body="No alerts match the current filters."
            action={
              chips.length > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setParam({ state: null, detector: null, severity: null })}
                >
                  Clear the filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr className="border-b border-[var(--color-line)]">
                  <Th sortKey="entity" activeKey={sortKey} direction={direction} onSort={onSort}>
                    Entity
                  </Th>
                  <Th>Type</Th>
                  <Th sortKey="detector" activeKey={sortKey} direction={direction} onSort={onSort}>
                    Detector
                  </Th>
                  <Th sortKey="window" activeKey={sortKey} direction={direction} onSort={onSort}>
                    Window
                  </Th>
                  <Th>Severity</Th>
                  <Th
                    sortKey="score"
                    activeKey={sortKey}
                    direction={direction}
                    onSort={onSort}
                    align="right"
                  >
                    Score
                  </Th>
                  <Th sortKey="state" activeKey={sortKey} direction={direction} onSort={onSort}>
                    State
                  </Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((alert) => (
                  <tr
                    key={alert.id}
                    className="border-b border-[var(--color-line)]/50 transition-colors last:border-0 hover:bg-[var(--color-raise-subtle)]"
                  >
                    <Td>
                      <Link
                        to={`/alerts/${alert.id}`}
                        className="font-mono text-xs text-[var(--color-ink)] hover:text-[var(--color-accent)]"
                      >
                        {alert.entity_id}
                      </Link>
                    </Td>
                    <Td>
                      <TypeChip type={alert.entity_type} />
                    </Td>
                    <Td className="text-xs text-[var(--color-ink-dim)]">{alert.detector}</Td>
                    <Td className="text-xs text-[var(--color-ink-faint)] tabular-nums">
                      {alert.window_index}
                    </Td>
                    <Td>
                      <SeverityBadge severity={severityOf(alert.window_score)} />
                    </Td>
                    <Td align="right" className="font-mono text-xs tabular-nums">
                      {alert.window_score.toFixed(3)}
                    </Td>
                    <Td>
                      <StatePill state={alert.state} />
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>

            {pageCount > 1 && (
              <div className="flex items-center justify-between gap-4 border-t border-[var(--color-line)] px-5 py-3">
                <span className="text-xs text-[var(--color-ink-faint)] tabular-nums">
                  {(current - 1) * PAGE_SIZE + 1}–{Math.min(current * PAGE_SIZE, sorted.length)} of{" "}
                  {sorted.length.toLocaleString()}
                </span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={current <= 1}
                    onClick={() => setParam({ page: String(current - 1) })}
                  >
                    Previous
                  </Button>
                  <span className="text-xs text-[var(--color-ink-dim)] tabular-nums">
                    {current} / {pageCount}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={current >= pageCount}
                    onClick={() => setParam({ page: String(current + 1) })}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Panel>
    </PageHeader>
  );
}

const TRANSITION_LABEL: Record<AlertState, string> = {
  new: "Reopen as new",
  investigating: "Start investigating",
  true_positive: "Confirm — true positive",
  false_positive: "Dismiss — false positive",
  exported: "Mark exported",
};

export function AlertDetailPage({ onChanged }: { onChanged: () => void }) {
  const { alertId } = useParams<{ alertId: string }>();
  const navigate = useNavigate();
  const [alert, setAlert] = useState<AlertDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!alertId) return;
    let cancelled = false;
    setLoading(true);
    api
      .get<AlertDetail>(`/api/v1/alerts/${alertId}`)
      .then((next) => !cancelled && setAlert(next))
      .catch(
        (cause) => !cancelled && setError(cause instanceof Error ? cause.message : "Failed to load"),
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [alertId]);

  async function move(state: AlertState) {
    if (!alertId) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.post<AlertDetail>(`/api/v1/alerts/${alertId}/transition`, {
        state,
        rationale,
      });
      setAlert(next);
      setRationale("");
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Transition failed");
    } finally {
      setBusy(false);
    }
  }

  /** The one write interaction besides triage, and the endpoint has existed all along. */
  async function addNote() {
    if (!alertId || !note.trim()) return;
    setBusy(true);
    setError(null);
    try {
      // The endpoint returns the created note, not the alert, so the detail is
      // re-read rather than patched locally — it keeps one source of truth for the
      // note list and its server-assigned ids and timestamps.
      await api.post<unknown>(`/api/v1/alerts/${alertId}/notes`, { body: note.trim() });
      setAlert(await api.get<AlertDetail>(`/api/v1/alerts/${alertId}`));
      setNote("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the note");
    } finally {
      setBusy(false);
    }
  }

  async function exportIncident() {
    if (!alertId) return;
    setBusy(true);
    try {
      const document_ = await api.post<unknown>(`/api/v1/alerts/${alertId}/export`, {});
      const blob = new Blob([JSON.stringify(document_, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `graphguard-incident-${alertId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setAlert(await api.get<AlertDetail>(`/api/v1/alerts/${alertId}`));
      onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Spinner label="Loading alert" />;
  if (!alert) {
    return (
      <div className="p-6">
        <Panel>
          <EmptyState icon={<ShieldAlert size={26} />} title="Alert not found" body={error ?? ""} />
        </Panel>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-6">
      <button
        onClick={() => navigate("/alerts")}
        className="inline-flex items-center gap-2 text-sm text-[var(--color-ink-dim)] hover:text-[var(--color-ink)]"
      >
        <ArrowLeft size={15} /> Back to queue
      </button>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-xl font-semibold">{alert.entity_id}</h1>
            <SeverityBadge severity={severityOf(alert.window_score)} />
            <StatePill state={alert.state} />
          </div>
          <p className="mt-1.5 text-sm text-[var(--color-ink-dim)]">
            {alert.detector} · window {alert.window_index} ·{" "}
            {formatWindow(alert.window_start, alert.window_end)}
          </p>
          {alert.tags.length > 0 && (
            /* ATLAS / OWASP LLM classifications the correlator attached. They were
               already on the wire and never rendered. */
            <div className="mt-2 flex flex-wrap gap-1.5">
              {alert.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-chip bg-[var(--color-accent)]/12 px-2 py-0.5 font-mono text-[10px] text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/25"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        {alert.state === "true_positive" && (
          <Button onClick={exportIncident} disabled={busy}>
            <FileDown size={15} /> Export incident
          </Button>
        )}
      </header>

      {error && (
        <div className="rounded-xl bg-[var(--color-critical)]/12 px-4 py-3 text-xs text-[var(--color-critical)] ring-1 ring-[var(--color-critical)]/25">
          {error}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[1.5fr_1fr]">
        <div className="space-y-5">
          <Panel>
            <PanelHead title="Why this fired" subtitle="Contributing factors, in human units" />
            <div className="px-5 pb-5">
              <p className="mb-4 rounded-xl bg-[var(--color-surface-2)] p-4 text-xs leading-relaxed whitespace-pre-line text-[var(--color-ink-dim)]">
                {alert.explanation}
              </p>
              {alert.reasons.length > 0 && (
                <ul className="space-y-3.5">
                  {alert.reasons.map((reason) => (
                    <ReasonBar key={reason.feature} reason={reason} />
                  ))}
                </ul>
              )}
              <p className="mt-4 border-t border-[var(--color-line)] pt-3 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
                The upper bar is the measured value and the lower one the median across
                comparable entities in the same window; the gap between them is what the
                detector reacted to. They describe what is unusual, not how the model computed
                its score.
              </p>
            </div>
          </Panel>

          <Panel>
            <PanelHead
              title="Evidence subgraph"
              subtitle="The neighbours that gave this node its context"
            />
            <div className="space-y-3 px-5 pb-5">
              {alert.evidence.length === 0 ? (
                <span className="text-xs text-[var(--color-ink-faint)]">
                  No neighbours recorded.
                </span>
              ) : (
                /* Grouped by entity type. An undifferentiated wrap of forty identifiers
                   tells the analyst nothing about the shape of the neighbourhood; the
                   counts per type are the first thing worth reading. */
                [...groupBy(alert.evidence).entries()].map(([type, ids]) => (
                  <div key={type}>
                    <div className="mb-1.5 flex items-center gap-2">
                      <TypeChip type={type} />
                      <span className="text-[11px] text-[var(--color-ink-faint)] tabular-nums">
                        {ids.length}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {ids.map((id) => (
                        <span
                          key={id}
                          className="rounded-field bg-[var(--color-surface-2)] px-2 py-1 font-mono text-[11px] text-[var(--color-ink-dim)]"
                        >
                          {id}
                        </span>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>

        <div className="space-y-5">
          <Panel>
            <PanelHead title="Triage" subtitle="Record a decision" />
            <div className="space-y-3 px-5 pb-5">
              <textarea
                value={rationale}
                onChange={(event) => setRationale(event.target.value)}
                placeholder="Rationale (optional, kept in the audit trail)"
                rows={3}
                className="w-full resize-none rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2.5 text-xs focus:border-[var(--color-accent)]/40 focus:outline-none"
              />
              <div className="flex flex-wrap gap-2">
                {alert.allowed_transitions.map((state) => (
                  <Button
                    key={state}
                    size="sm"
                    variant={state === "true_positive" ? "danger" : "ghost"}
                    onClick={() => move(state)}
                    disabled={busy}
                  >
                    {TRANSITION_LABEL[state]}
                  </Button>
                ))}
              </div>
            </div>
          </Panel>

          <Panel>
            <PanelHead title="Audit trail" subtitle="Every decision, and who made it" />
            <div className="px-5 pb-5">
              {alert.history.length === 0 ? (
                <p className="text-xs text-[var(--color-ink-faint)]">
                  No decisions recorded yet.
                </p>
              ) : (
                <ol className="space-y-3">
                  {alert.history.map((entry, index) => (
                    <li key={index} className="relative flex gap-3 pb-1 text-xs last:pb-0">
                      {/* A rail joining consecutive decisions, so the trail reads as one
                          sequence rather than as unrelated bullets. */}
                      {index < alert.history.length - 1 && (
                        <span
                          aria-hidden
                          className="absolute top-3 bottom-0 left-[3px] w-px bg-[var(--color-line)]"
                        />
                      )}
                      <span className="relative mt-1 size-2 shrink-0 rounded-chip bg-[var(--color-accent)]" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[var(--color-ink-dim)]">
                          {entry.from_state ? `${entry.from_state} → ` : ""}
                          <span className="font-medium text-[var(--color-ink)]">
                            {entry.to_state}
                          </span>
                        </span>
                        {entry.rationale && (
                          <span className="mt-0.5 block text-[var(--color-ink-faint)] italic">
                            “{entry.rationale}”
                          </span>
                        )}
                        <span className="mt-0.5 block text-[10px] text-[var(--color-ink-faint)]">
                          {new Date(entry.at).toLocaleString()}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </Panel>

          <Panel>
            <PanelHead
              title="Notes"
              subtitle="Working context that is not a state change"
            />
            <div className="space-y-3 px-5 pb-5">
              {alert.notes.length > 0 && (
                <ul className="space-y-2.5">
                  {alert.notes.map((note) => (
                    <li
                      key={note.id}
                      className="rounded-card bg-[var(--color-surface-2)] px-3 py-2.5 text-xs"
                    >
                      <p className="leading-relaxed whitespace-pre-line text-[var(--color-ink-dim)]">
                        {note.body}
                      </p>
                      <p className="mt-1.5 text-[10px] text-[var(--color-ink-faint)]">
                        {new Date(note.created_at).toLocaleString()}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              <Textarea
                rows={2}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="What you checked, and what it showed…"
                className="text-xs"
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={addNote}
                disabled={!note.trim()}
                loading={busy}
              >
                Add note
              </Button>
            </div>
          </Panel>

          {alert.ground_truth !== null && (
            <Panel>
              <div className="px-5 py-4">
                <div className="text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                  Ground truth
                </div>
                <div className="mt-1.5 font-mono text-sm text-[var(--color-ok)]">
                  {alert.ground_truth}
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
                  Shown because this run used a labelled validation dataset. In production
                  telemetry this field is empty.
                </p>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Render a detection window as a range.
 *
 * Windows are a day wide by default, so abbreviating the end to a bare time produced
 * "12:00 AM – 12:00 AM", which reads as a zero-length window rather than a full day.
 * The date is repeated whenever the two ends fall on different days.
 */
/**
 * One contributing factor, drawn against its peer median.
 *
 * This replaces a bar whose width was `Math.abs(deviation) * 12` — a magic constant with
 * no axis, no unit and no reference point, in the one panel an examiner is most likely
 * to interrogate. The backend has always sent `peer_median`; plotting the measured value
 * against it on a shared scale says the same thing truthfully, and the notch gives the
 * reader something to compare the bar to.
 *
 * The scale is per-factor because the factors are in different units — an error rate and
 * a counterparty count cannot share an axis.
 */
/** Evidence nodes bucketed by entity type, preserving first-seen order. */
function groupBy(evidence: { type: string; id: string }[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const node of evidence) {
    const bucket = out.get(node.type);
    if (bucket) bucket.push(node.id);
    else out.set(node.type, [node.id]);
  }
  return out;
}

function ReasonBar({ reason }: { reason: Reason }) {
  // A shared axis per factor, because the factors are in different units — an error
  // rate and a counterparty count cannot share one. The headroom keeps the longer of
  // the two bars off the right edge.
  const top = Math.max(reason.value, reason.peer_median) * 1.15 || 1;
  const valuePct = Math.min(100, (reason.value / top) * 100);
  const medianPct = Math.min(100, (reason.peer_median / top) * 100);
  // Above its peers is the anomalous direction for every feature the detectors emit;
  // below is shown neutrally rather than dressed up as a finding.
  const above = reason.value >= reason.peer_median;

  return (
    <li>
      <div className="mb-1.5 flex items-baseline justify-between gap-3 text-xs">
        <span className="text-[var(--color-ink-dim)]">{reason.feature}</span>
        <span className="font-mono tabular-nums text-[var(--color-ink)]">
          {reason.value.toLocaleString()}
          <span className="text-[var(--color-ink-faint)]">
            {" "}
            vs {reason.peer_median.toLocaleString()} median
          </span>
        </span>
      </div>
      {/* Two bars on one axis rather than a bar and a tick.
          Scaling each factor to its own maximum means the measured value always fills
          nearly the whole track, so bar *length* carries no signal and every factor
          ends up looking equally extreme. Drawing the peer median as a second bar puts
          the comparison back where the eye already looks — the difference in length
          between two bars — without changing what either one represents. */}
      <div className="space-y-1">
        <div className="h-2 rounded-chip bg-[var(--color-line)]">
          <div
            className="h-full rounded-chip"
            style={{
              width: `${valuePct}%`,
              background: above ? "var(--color-accent)" : "var(--color-ink-faint)",
            }}
          />
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1 flex-1 rounded-chip bg-[var(--color-line)]">
            <div
              className="h-full rounded-chip bg-[var(--color-ink-faint)]"
              style={{ width: `${medianPct}%` }}
            />
          </div>
          <span className="shrink-0 text-[10px] tracking-wide text-[var(--color-ink-faint)] uppercase">
            peer median
          </span>
        </div>
      </div>
    </li>
  );
}

function formatWindow(start: string, end: string): string {
  const from = new Date(start);
  const to = new Date(end);
  const sameDay = from.toDateString() === to.toDateString();
  return sameDay
    ? `${from.toLocaleString()} – ${to.toLocaleTimeString()}`
    : `${from.toLocaleString()} – ${to.toLocaleString()}`;
}
