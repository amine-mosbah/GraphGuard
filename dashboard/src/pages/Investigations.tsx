/**
 * Case management.
 *
 * The unit an analyst actually reasons about. One compromised credential produces a dozen alerts
 * across windows and detectors; triaging each alone loses the fact that they are one incident —
 * which would reproduce, in the interface, exactly the relational blindness this project argues
 * against.
 *
 * The page previously listed a case's evidence as a row of entity names, which is the least
 * informative view of it possible: the attachment reasons the analyst wrote were fetched and
 * dropped, the alert explanations were fetched and dropped, and the timestamps — the thing that
 * turns a bag of alerts into an account of what happened — were never sent at all. A case is a
 * narrative over time, so it is now drawn as one, with the evidence underneath it and the
 * analyst's own reasoning kept attached to each piece.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Download,
  FileSearch,
  FolderPlus,
  Link2,
  Plus,
  Sparkles,
  Trash2,
  Waypoints,
} from "lucide-react";
import { api } from "../lib/api";
import type { AlertSummary, AttackChain } from "../lib/types";
import {
  Button,
  EmptyState,
  PageHeader,
  Panel,
  PanelHead,
  SeverityBadge,
  SkeletonPanel,
  severityOf,
} from "../components/ui";
import { CaseTimeline } from "../components/case-timeline";

type CaseStatus = "open" | "contained" | "closed";

interface CaseSummary {
  id: string;
  title: string;
  summary: string;
  status: CaseStatus;
  alert_count: number;
  chain_count: number;
  created_at: string;
  updated_at: string;
}

export interface CaseAlert {
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
  state: string;
  explanation: string;
  tags: string[];
}

export interface CaseChain {
  id: string;
  run_id: string;
  source_ip: string;
  internal_entity_type: string;
  internal_entity_id: string;
  confidence: number;
  steps: { stage: string; entity_type: string; entity_id: string; at: string; detail: string }[];
  narrative: string;
}

export interface CaseMember {
  id: string;
  kind: "alert" | "chain" | "missing";
  rationale: string;
  added_at: string;
  alert?: CaseAlert;
  chain?: CaseChain;
}

interface CaseDetail extends CaseSummary {
  members: CaseMember[];
}

/** Sentence case for display. The wire format is a lowercase enum; a triage queue
    should not show the analyst the database's spelling. */
const STATUS_LABEL: Record<CaseStatus, string> = {
  open: "Open",
  contained: "Contained",
  closed: "Closed",
};

const STATUS_STYLE: Record<CaseStatus, string> = {
  open: "text-[var(--color-high)] bg-[var(--color-high)]/12 ring-[var(--color-high)]/30",
  contained: "text-[var(--color-accent)] bg-[var(--color-accent)]/12 ring-[var(--color-accent)]/30",
  closed: "text-[var(--color-ink-faint)] bg-[var(--color-raise)] ring-[var(--color-hairline)]",
};

export function Investigations({
  alerts,
  chains,
}: {
  alerts: AlertSummary[];
  chains: AttackChain[];
}) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await api.get<{ investigations: CaseSummary[] }>("/api/v1/investigations");
      setCases(body.investigations);
      // Keep the open case in step with the list, so a status change or an attachment made
      // here is reflected without the analyst having to re-select it.
      setSelected((current) =>
        current && !body.investigations.some((item) => item.id === current.id) ? null : current,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load cases");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Open the most recently touched case on arrival.
   *
   * The page used to land on "No case selected" beside a list of cases that all had evidence in
   * them, so the first impression of a populated page was an empty one.
   *
   * Guarded by a ref rather than by `selected`: keying it on the selection would reopen the
   * first case every time the analyst cleared one.
   */
  const openedOnce = useRef(false);
  useEffect(() => {
    if (openedOnce.current || cases.length === 0) return;
    openedOnce.current = true;
    void open(cases[0]!.id);
  }, [cases]);

  async function open(id: string) {
    setError(null);
    try {
      setSelected(await api.get<CaseDetail>(`/api/v1/investigations/${id}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open the case");
    }
  }

  async function create() {
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<CaseDetail>("/api/v1/investigations", { title, summary });
      setTitle("");
      setSummary("");
      setCreating(false);
      setSelected(created);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not open the case");
    } finally {
      setBusy(false);
    }
  }

  async function attach(payload: { alert_id?: string; chain_id?: string; rationale?: string }) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      setSelected(
        await api.post<CaseDetail>(`/api/v1/investigations/${selected.id}/members`, payload),
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not attach that evidence");
    } finally {
      setBusy(false);
    }
  }

  async function detach(memberId: string) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      setSelected(
        await api.delete<CaseDetail>(`/api/v1/investigations/${selected.id}/members/${memberId}`),
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove that evidence");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: CaseStatus) {
    if (!selected) return;
    setBusy(true);
    try {
      setSelected(
        await api.post<CaseDetail>(`/api/v1/investigations/${selected.id}/status`, { status }),
      );
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function exportCase() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const document = await api.get<unknown>(
        `/api/v1/investigations/${selected.id}/export`,
      );
      download(`${slug(selected.title)}.json`, JSON.stringify(document, null, 2));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not export the case");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <PageHeader title="Investigations" subtitle="Loading cases…">
        <div className="grid gap-5 xl:grid-cols-[340px_1fr]">
          <SkeletonPanel rows={5} />
          <SkeletonPanel rows={6} />
        </div>
      </PageHeader>
    );
  }

  return (
    <PageHeader
      title="Investigations"
      subtitle="Group the alerts and chains that belong to one incident. A compromised credential surfaces as many separate alerts; the case is what makes them one finding."
      actions={
        <Button onClick={() => setCreating((value) => !value)}>
          <FolderPlus size={16} /> New case
        </Button>
      }
    >
      {error && (
        <div className="rounded-xl bg-[var(--color-critical)]/12 px-4 py-3 text-xs text-[var(--color-critical)] ring-1 ring-[var(--color-critical)]/25">
          {error}
        </div>
      )}

      {creating && (
        <Panel>
          <PanelHead title="Open a case" />
          <div className="space-y-3 px-5 pb-5">
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Title — e.g. “Credential key-047-0 compromised”"
              className="w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-sm focus:border-[var(--color-accent)]/40 focus:outline-none"
            />
            <textarea
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              placeholder="What you believe happened, so far"
              rows={3}
              className="w-full resize-none rounded-xl border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-xs focus:border-[var(--color-accent)]/40 focus:outline-none"
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={create} disabled={busy || !title.trim()}>
                Open case
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>
                Cancel
              </Button>
            </div>
          </div>
        </Panel>
      )}

      <div className="grid gap-5 xl:grid-cols-[320px_1fr]">
        <Panel className="overflow-hidden">
          <PanelHead title="Cases" subtitle={`${cases.length} total`} />
          {cases.length === 0 ? (
            <EmptyState
              icon={<FileSearch size={26} />}
              title="No cases yet"
              body="Open one, then attach the alerts and chains that belong to it."
            />
          ) : (
            <ul className="max-h-[620px] overflow-y-auto">
              {cases.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => open(item.id)}
                    className={`w-full border-b border-[var(--color-line)]/60 px-5 py-3.5 text-left transition last:border-0 hover:bg-[var(--color-raise-subtle)] ${
                      selected?.id === item.id ? "bg-[var(--color-accent)]/8" : ""
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="truncate text-sm font-medium">{item.title}</span>
                      <span
                        className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ring-1 ${STATUS_STYLE[item.status]}`}
                      >
                        {STATUS_LABEL[item.status]}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-[var(--color-ink-faint)]">
                      {item.alert_count} alert{item.alert_count === 1 ? "" : "s"} ·{" "}
                      {item.chain_count} chain{item.chain_count === 1 ? "" : "s"} · updated{" "}
                      {new Date(item.updated_at).toLocaleDateString()}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        {selected ? (
          <CaseView
            detail={selected}
            alerts={alerts}
            chains={chains}
            busy={busy}
            onAttach={attach}
            onDetach={detach}
            onStatus={setStatus}
            onExport={exportCase}
          />
        ) : (
          <Panel>
            <EmptyState
              icon={<FileSearch size={28} />}
              title="No case selected"
              body="Pick a case on the left, or open a new one."
            />
          </Panel>
        )}
      </div>
    </PageHeader>
  );
}

function CaseView({
  detail,
  alerts,
  chains,
  busy,
  onAttach,
  onDetach,
  onStatus,
  onExport,
}: {
  detail: CaseDetail;
  alerts: AlertSummary[];
  chains: AttackChain[];
  busy: boolean;
  onAttach: (payload: { alert_id?: string; chain_id?: string; rationale?: string }) => void;
  onDetach: (memberId: string) => void;
  onStatus: (status: CaseStatus) => void;
  onExport: () => void;
}) {
  // Memoised because the suggestion list depends on it; a fresh Set every render would make
  // that useMemo recompute on every render of the page.
  const attachedAlertIds = useMemo(
    () => new Set(detail.members.map((member) => member.alert?.id).filter(Boolean)),
    [detail.members],
  );
  const attachedChainIds = useMemo(
    () => new Set(detail.members.map((member) => member.chain?.id).filter(Boolean)),
    [detail.members],
  );

  /** The entities this case is actually about, rolled up from whatever is attached. */
  const entities = useMemo(() => {
    const map = new Map<string, { type: string; id: string; alerts: number; detectors: Set<string> }>();
    for (const member of detail.members) {
      if (member.alert) {
        const key = `${member.alert.entity_type}:${member.alert.entity_id}`;
        const entry = map.get(key) ?? {
          type: member.alert.entity_type,
          id: member.alert.entity_id,
          alerts: 0,
          detectors: new Set<string>(),
        };
        entry.alerts += 1;
        entry.detectors.add(member.alert.detector);
        map.set(key, entry);
      }
      if (member.chain) {
        const key = `${member.chain.internal_entity_type}:${member.chain.internal_entity_id}`;
        map.set(
          key,
          map.get(key) ?? {
            type: member.chain.internal_entity_type,
            id: member.chain.internal_entity_id,
            alerts: 0,
            detectors: new Set<string>(),
          },
        );
      }
    }
    return [...map.values()].sort((a, b) => b.alerts - a.alerts);
  }, [detail.members]);

  const tags = useMemo(
    () =>
      [
        ...new Set(detail.members.flatMap((member) => member.alert?.tags ?? [])),
      ].sort(),
    [detail.members],
  );

  /**
   * Alerts on an entity this case already contains, that are not attached yet.
   *
   * The whole argument for cases is that one compromise surfaces as many alerts, so leaving
   * the analyst to find the rest of them by scrolling the queue would concede the point. This
   * is a suggestion, not an auto-attach: sharing an entity is evidence of relatedness, not
   * proof of it, and the judgement stays with the analyst.
   */
  const suggested = useMemo(() => {
    const known = new Set(entities.map((entity) => `${entity.type}:${entity.id}`));
    if (known.size === 0) return [];
    return alerts.filter(
      (alert) =>
        !attachedAlertIds.has(alert.id) && known.has(`${alert.entity_type}:${alert.entity_id}`),
    );
  }, [alerts, entities, attachedAlertIds]);

  const candidates = alerts.filter(
    (alert) =>
      (alert.state === "true_positive" || alert.state === "investigating") &&
      !attachedAlertIds.has(alert.id),
  );

  return (
    <div className="space-y-5">
      <Panel>
        <PanelHead
          title={detail.title}
          subtitle={detail.summary || "No summary recorded."}
          action={
            <div className="flex items-center gap-1.5">
              {(["open", "contained", "closed"] as CaseStatus[]).map((status) => (
                <button
                  key={status}
                  onClick={() => onStatus(status)}
                  disabled={busy}
                  className={`rounded-lg px-2.5 py-1 text-[11px] ring-1 transition ${
                    detail.status === status
                      ? STATUS_STYLE[status]
                      : "text-[var(--color-ink-faint)] ring-[var(--color-line)] hover:text-[var(--color-ink)]"
                  }`}
                >
                  {STATUS_LABEL[status]}
                </button>
              ))}
              <Button variant="ghost" size="sm" onClick={onExport} disabled={busy}>
                <Download size={13} /> Export
              </Button>
            </div>
          }
        />

        <div className="grid gap-x-6 gap-y-4 px-5 pb-5 lg:grid-cols-[1fr_240px]">
          <div className="min-w-0">
            <h3 className="mb-2.5 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
              What happened, in order
            </h3>
            <CaseTimeline members={detail.members} />
          </div>

          <div className="space-y-4">
            <div>
              <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Entities involved
              </h3>
              {entities.length === 0 ? (
                <p className="text-[11px] text-[var(--color-ink-faint)]">
                  Nothing attached yet.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {entities.map((entity) => (
                    <li key={`${entity.type}:${entity.id}`} className="text-xs">
                      <span className="block truncate font-mono">{entity.id}</span>
                      <span className="text-[11px] text-[var(--color-ink-faint)]">
                        {entity.type.replace("_", " ")}
                        {entity.alerts > 0 &&
                          ` · ${entity.alerts} alert${entity.alerts === 1 ? "" : "s"} from ${entity.detectors.size} detector${entity.detectors.size === 1 ? "" : "s"}`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {tags.length > 0 && (
              <div>
                <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                  Framework tags
                </h3>
                <ul className="space-y-1.5">
                  {tags.map((tag) => (
                    <FrameworkTag key={tag} tag={tag} />
                  ))}
                </ul>
                <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                  Carried from each attached alert's detector family, so the incident can be named
                  in language a wider security team already uses.
                </p>
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Panel>
        <PanelHead
          title="Evidence"
          subtitle={
            detail.members.length === 0
              ? "Nothing attached yet"
              : `${detail.members.length} item${detail.members.length === 1 ? "" : "s"}, with the reason each was attached`
          }
        />
        <div className="px-5 pb-5">
          {detail.members.length === 0 ? (
            <p className="text-xs text-[var(--color-ink-faint)]">
              Pick from the panels below. Anything you attach keeps the reason you gave for it.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {detail.members.map((member) => (
                <EvidenceRow
                  key={member.id}
                  member={member}
                  busy={busy}
                  onDetach={() => onDetach(member.id)}
                />
              ))}
            </ul>
          )}
        </div>
      </Panel>

      {suggested.length > 0 && (
        <Panel>
          <PanelHead
            title="Related alerts you have not attached"
            subtitle="Same entity as something already in this case"
            action={<Sparkles size={16} className="text-[var(--color-accent)]" />}
          />
          <div className="max-h-[220px] overflow-y-auto px-5 pb-5">
            <ul className="space-y-1.5">
              {suggested.slice(0, 20).map((alert) => (
                <li key={alert.id}>
                  <button
                    onClick={() =>
                      onAttach({
                        alert_id: alert.id,
                        rationale: `Same entity as evidence already in this case; window ${alert.window_index}.`,
                      })
                    }
                    disabled={busy}
                    className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition hover:bg-[var(--color-raise)] disabled:opacity-35"
                  >
                    <Plus size={13} className="shrink-0 text-[var(--color-accent)]" />
                    <span className="min-w-0 flex-1 truncate font-mono">{alert.entity_id}</span>
                    <span className="shrink-0 text-[10px] text-[var(--color-ink-faint)]">
                      {alert.detector} · window {alert.window_index}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {suggested.length > 20 && (
              <p className="mt-2 text-[11px] text-[var(--color-ink-faint)]">
                Showing 20 of {suggested.length}.
              </p>
            )}
          </div>
        </Panel>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel>
          <PanelHead
            title="Attach an alert"
            subtitle="Alerts you are investigating or have confirmed"
          />
          <div className="max-h-[280px] overflow-y-auto px-5 pb-5">
            {candidates.length === 0 ? (
              <p className="text-xs text-[var(--color-ink-faint)]">
                Move an alert to investigating or true positive first — a case is for evidence you
                have looked at.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {candidates.slice(0, 30).map((alert) => (
                  <li key={alert.id}>
                    <button
                      onClick={() => onAttach({ alert_id: alert.id })}
                      disabled={busy}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition hover:bg-[var(--color-raise)] disabled:opacity-35"
                    >
                      <Plus size={13} className="shrink-0 text-[var(--color-accent)]" />
                      <span className="min-w-0 flex-1 truncate font-mono">{alert.entity_id}</span>
                      <span className="shrink-0 text-[10px] text-[var(--color-ink-faint)]">
                        {alert.window_score.toFixed(2)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHead title="Attach a chain" subtitle="Cross-layer paths in this run" />
          <div className="max-h-[280px] overflow-y-auto px-5 pb-5">
            {chains.length === 0 ? (
              <p className="text-xs text-[var(--color-ink-faint)]">
                No chains were found in this run.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {chains.map((chain) => (
                  <li key={chain.id}>
                    <button
                      onClick={() => onAttach({ chain_id: chain.id })}
                      disabled={busy || attachedChainIds.has(chain.id)}
                      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs transition hover:bg-[var(--color-raise)] disabled:opacity-35"
                    >
                      <Link2 size={13} className="shrink-0 text-[var(--color-accent)]" />
                      <span className="min-w-0 flex-1 truncate font-mono">
                        {chain.source_ip} → {chain.internal_entity_id}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

/**
 * One framework tag, split across two lines.
 *
 * `OWASP_LLM_TOP10:LLM10_Unbounded_Consumption` is 43 monospaced characters and was rendered as a
 * single non-breaking chip in a 240px column, so it ran straight out through the panel's border.
 * Splitting on the colon is not just a wrapping trick: the two halves genuinely say different
 * things — which taxonomy, and which entry in it — and the entry is the part being claimed.
 */
function FrameworkTag({ tag }: { tag: string }) {
  const [framework, ...rest] = tag.split(":");
  const technique = rest.join(":");
  return (
    <li
      title={tag}
      className="rounded-lg bg-[var(--color-raise)] px-2.5 py-1.5 ring-1 ring-[var(--color-hairline)]"
    >
      <span className="block text-[9.5px] tracking-wide text-[var(--color-ink-faint)] uppercase">
        {framework?.replace(/_/g, " ")}
      </span>
      <span className="mt-0.5 block text-[11px] leading-snug break-words text-[var(--color-ink-dim)]">
        {technique.replace(/_/g, " ") || tag}
      </span>
    </li>
  );
}

/** One attached alert or chain, with what it says and why it is here. */
function EvidenceRow({
  member,
  busy,
  onDetach,
}: {
  member: CaseMember;
  busy: boolean;
  onDetach: () => void;
}) {
  return (
    <li className="rounded-xl bg-[var(--color-surface-2)] px-4 py-3.5">
      <div className="flex items-start gap-3">
        {member.kind === "alert" && member.alert ? (
          <>
            <SeverityBadge severity={severityOf(member.alert.window_score)} />
            <div className="min-w-0 flex-1">
              <Link
                to={`/alerts/${member.alert.id}`}
                className="font-mono text-xs hover:text-[var(--color-accent)]"
              >
                {member.alert.entity_id}
              </Link>
              <span className="ml-2 text-[11px] text-[var(--color-ink-faint)]">
                {member.alert.detector} · window {member.alert.window_index} ·{" "}
                {new Date(member.alert.window_start).toLocaleDateString()}
              </span>
              {/* The explanation was already in the payload and was being discarded. It is the
                  only thing on the row that says *why* this alert exists. */}
              <p className="mt-1.5 text-[11px] leading-relaxed whitespace-pre-line text-[var(--color-ink-dim)]">
                {member.alert.explanation}
              </p>
            </div>
          </>
        ) : member.kind === "chain" && member.chain ? (
          <>
            <Waypoints size={16} className="mt-0.5 shrink-0 text-[var(--color-critical)]" />
            <div className="min-w-0 flex-1">
              <span className="font-mono text-xs">
                {member.chain.source_ip} → {member.chain.internal_entity_id}
              </span>
              <span className="ml-2 text-[11px] text-[var(--color-ink-faint)]">
                confidence {member.chain.confidence.toFixed(2)} · {member.chain.steps.length} steps
              </span>
              <ol className="mt-1.5 space-y-0.5">
                {member.chain.steps.map((step, index) => (
                  <li key={index} className="text-[11px] leading-relaxed text-[var(--color-ink-dim)]">
                    <span className="text-[var(--color-ink-faint)]">
                      {new Date(step.at).toLocaleString([], {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>{" "}
                    <span className="font-medium text-[var(--color-ink)]">{step.stage}</span> —{" "}
                    {step.detail}
                  </li>
                ))}
              </ol>
            </div>
          </>
        ) : (
          <span className="flex-1 text-xs text-[var(--color-ink-faint)] italic">
            Evidence removed with its run. The slot is kept so the case does not silently shrink.
          </span>
        )}
        <button
          onClick={onDetach}
          disabled={busy}
          aria-label="Remove from case"
          className="shrink-0 text-[var(--color-ink-faint)] hover:text-[var(--color-critical)]"
        >
          <Trash2 size={14} />
        </button>
      </div>
      {member.rationale && (
        <p className="mt-2.5 border-t border-[var(--color-line)] pt-2.5 text-[11px] leading-relaxed text-[var(--color-ink-dim)] italic">
          “{member.rationale}”
        </p>
      )}
    </li>
  );
}

function slug(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "case";
}

/** Save a string as a file without a round trip to the server. */
function download(filename: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
