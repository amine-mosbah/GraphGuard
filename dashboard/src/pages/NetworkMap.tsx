import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity,
  CircleGauge,
  Eye,
  GitBranch,
  Maximize2,
  Minus,
  Network,
  Plus,
  RotateCcw,
  Search,
  ShieldAlert,
  Spline,
  X,
} from "lucide-react";
import { BehaviourProfileChart } from "../components/charts";
import { NetworkCanvas, type NetworkHandle } from "../components/NetworkCanvas";
import {
  BAND_LABEL,
  BAND_TOKEN,
  edgeBand,
  edgeRisk,
  failureRatio,
  linkKey,
  riskIndex,
} from "../lib/graph-risk";
import {
  Chip,
  EmptyState,
  Input,
  PageHeader,
  Panel,
  SeverityBadge,
  SkeletonPanel,
  SkeletonStats,
  severityOf,
  useNodeTypeMeta,
} from "../components/ui";
import type { AlertSummary, GraphLink, GraphNode, NetworkGraph } from "../lib/types";

export function NetworkMap({
  graph,
  alerts,
  loading,
}: {
  graph: NetworkGraph | null;
  alerts: AlertSummary[];
  loading: boolean;
}) {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [selectedLink, setSelectedLink] = useState<GraphLink | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [relationFilter, setRelationFilter] = useState<string | null>(null);
  /**
   * Whether edges are toned by the risk they inherit from their endpoints.
   *
   * On by default because it is the reason to look at this map at all, but switchable: at a
   * thousand edges the risk ramp is a lot of colour, and an analyst reading pure topology wants
   * it out of the way.
   */
  const [riskEdges, setRiskEdges] = useState(true);
  /**
   * `?focus=<type>:<id>` selects a node on arrival.
   *
   * The chains page links here to say "this is the credential the external address ended up
   * driving" — without it, that link lands the analyst on a graph of several hundred nodes and
   * asks them to find it by eye, which is exactly the work the tool exists to remove.
   */
  const [params] = useSearchParams();
  const focusId = params.get("focus");
  const [handle, setHandle] = useState<NetworkHandle | null>(null);
  const [query, setQuery] = useState("");
  const panelRef = useRef<HTMLElement>(null);
  const onReady = useCallback((next: NetworkHandle) => setHandle(next), []);
  const palette = useNodeTypeMeta();

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const node of graph?.nodes ?? []) map.set(node.type, (map.get(node.type) ?? 0) + 1);
    return map;
  }, [graph]);

  const degrees = useMemo(() => {
    const map = new Map<string, number>();
    for (const link of graph?.links ?? []) {
      map.set(link.source, (map.get(link.source) ?? 0) + 1);
      map.set(link.target, (map.get(link.target) ?? 0) + 1);
    }
    return map;
  }, [graph]);

  const flagged = useMemo(
    () => (graph?.nodes ?? []).filter((node) => node.alerts > 0).sort((a, b) => b.risk - a.risk),
    [graph],
  );

  const nodeIndex = useMemo(() => riskIndex(graph?.nodes ?? []), [graph]);

  /** Relation types present in this run, with how many edges each accounts for. */
  const relations = useMemo(() => {
    const map = new Map<string, number>();
    for (const link of graph?.links ?? []) map.set(link.relation, (map.get(link.relation) ?? 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [graph]);

  /** Edges that inherit a risk band, worst first — the shortlist worth clicking. */
  const riskyLinks = useMemo(
    () =>
      (graph?.links ?? [])
        .map((link) => ({ link, risk: edgeRisk(link, nodeIndex) }))
        .filter((entry) => entry.risk > 0)
        .sort((a, b) => b.risk - a.risk),
    [graph, nodeIndex],
  );

  const matches = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (term.length < 2) return [];
    return (graph?.nodes ?? [])
      .filter((node) => node.label.toLowerCase().includes(term))
      .slice(0, 6);
  }, [graph, query]);

  useEffect(() => {
    if (!focusId) return;
    const node = graph?.nodes.find((candidate) => candidate.id === focusId);
    if (node) setSelected(node);
  }, [focusId, graph]);

  const selectedAlerts = useMemo(
    () =>
      selected
        ? alerts.filter((alert) => `${alert.entity_type}:${alert.entity_id}` === selected.id)
        : [],
    [selected, alerts],
  );

  if (loading) {
    return (
      <PageHeader title="Network map" subtitle="Running the three-dimensional force simulation…">
        <SkeletonStats />
        <SkeletonPanel rows={9} />
        <SkeletonPanel rows={5} />
      </PageHeader>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="p-6">
        <Panel>
          <EmptyState
            icon={<Network size={28} />}
            title="No graph to draw"
            body="The network map is materialised when a detection run completes. Trigger a run to populate it."
          />
        </Panel>
      </div>
    );
  }

  const visibleNodes = typeFilter ? (counts.get(typeFilter) ?? 0) : graph.nodes.length;

  return (
    <PageHeader
      title="Network map"
      subtitle="Explore the latest entity graph in three dimensions. Connected entities settle near one another; node size reflects reach and alert risk. Click a line to inspect the relation itself — what kind of interaction it is, how many times it happened, and how much of it failed."
    >
      <Panel ref={panelRef} className="relative overflow-hidden" elevation={1}>
        <div className="flex flex-col gap-3 border-b border-[var(--color-line)] px-4 py-3.5 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <div className="relative">
              <Input
                aria-label="Find an entity"
                icon={<Search size={14} />}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Find entity…"
                className="w-52 py-1.5 text-xs"
              />
              {matches.length > 0 && (
                <div className="absolute top-[calc(100%+8px)] left-0 z-30 w-64 rounded-control border border-[var(--color-line)] bg-[var(--color-surface)] p-1.5 shadow-[var(--elev-3)]">
                  {matches.map((node) => (
                    <button
                      key={node.id}
                      type="button"
                      onClick={() => {
                        setSelected(node);
                        setQuery("");
                      }}
                      className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs hover:bg-[var(--color-raise)]"
                    >
                      <span className="truncate font-mono text-[var(--color-ink)]">{node.label}</span>
                      <span className="ml-3 text-[var(--color-ink-faint)]">{palette[node.type]?.label ?? node.type}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <span className="ml-1 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
              Show
            </span>
            <Chip selected={typeFilter === null} onClick={() => setTypeFilter(null)}>
              All
            </Chip>
            {[...counts.entries()].map(([type, count]) => (
              <Chip
                key={type}
                selected={typeFilter === type}
                onClick={() => setTypeFilter(typeFilter === type ? null : type)}
                tint={palette[type]?.color}
              >
                <span
                  className="grid size-4 place-items-center rounded-chip text-[9px] font-bold text-white"
                  style={{ background: palette[type]?.color }}
                  aria-hidden
                >
                  {palette[type]?.letter ?? "?"}
                </span>
                {palette[type]?.label ?? type} <span className="opacity-60">{count}</span>
              </Chip>
            ))}
          </div>

          <div className="flex shrink-0 items-center gap-2 text-[11px] text-[var(--color-ink-faint)]">
            <span className="inline-flex items-center gap-1.5 rounded-chip bg-[var(--color-raise)] px-2.5 py-1 ring-1 ring-[var(--color-hairline)]">
              <span className="size-1.5 rounded-full bg-[var(--color-ok)]" aria-hidden />
              Force-directed 3D
            </span>
            <span className="font-mono tabular-nums">{visibleNodes} visible</span>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-b border-[var(--color-line)] px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
              Relation
            </span>
            <Chip selected={relationFilter === null} onClick={() => setRelationFilter(null)}>
              Any
            </Chip>
            {relations.map(([relation, count]) => (
              <Chip
                key={relation}
                selected={relationFilter === relation}
                onClick={() => setRelationFilter(relationFilter === relation ? null : relation)}
                title={`Show only ${relation} relations`}
              >
                <span className="font-mono">{relation}</span>
                <span className="opacity-60">{count}</span>
              </Chip>
            ))}
          </div>
          <RiskLegend enabled={riskEdges} onToggle={() => setRiskEdges((value) => !value)} />
        </div>

        <div className="relative h-[540px] sm:h-[600px] xl:h-[640px]">
          <NetworkCanvas
            nodes={graph.nodes}
            links={graph.links}
            selectedId={selected?.id ?? null}
            onSelect={setSelected}
            selectedLinkKey={selectedLink ? linkKey(selectedLink) : null}
            onSelectLink={setSelectedLink}
            typeFilter={typeFilter}
            relationFilter={relationFilter}
            colourEdgesByRisk={riskEdges}
            onReady={onReady}
          />

          <div className="pointer-events-none absolute top-4 left-4 hidden items-stretch overflow-hidden rounded-control bg-[color-mix(in_oklab,var(--color-surface)_86%,transparent)] ring-1 ring-[var(--color-hairline)] backdrop-blur-md sm:flex">
            <MapMetric label="Entities" value={graph.nodes.length} />
            <MapMetric label="Relations" value={graph.links.length} border />
            <MapMetric label="Flagged" value={flagged.length} border alert={flagged.length > 0} />
            <MapMetric label="Types" value={counts.size} border />
          </div>

          <div className="absolute top-4 right-4 flex flex-col gap-1.5">
            <CanvasButton onClick={() => handle?.zoom(1.2)} label="Zoom in"><Plus size={14} /></CanvasButton>
            <CanvasButton onClick={() => handle?.zoom(1 / 1.2)} label="Zoom out"><Minus size={14} /></CanvasButton>
            <CanvasButton onClick={() => handle?.reset()} label="Reset view"><RotateCcw size={14} /></CanvasButton>
            <CanvasButton onClick={() => void panelRef.current?.requestFullscreen?.()} label="Fullscreen"><Maximize2 size={14} /></CanvasButton>
          </div>

          <div className="pointer-events-none absolute bottom-4 left-1/2 hidden -translate-x-1/2 rounded-chip bg-[color-mix(in_oklab,var(--color-surface)_86%,transparent)] px-3 py-1.5 text-center text-[11px] whitespace-nowrap text-[var(--color-ink-dim)] ring-1 ring-[var(--color-hairline)] backdrop-blur-md sm:block">
            Drag to orbit · scroll to zoom · click a node to isolate its connections, or a line to
            inspect the relation
          </div>
        </div>
      </Panel>

      {selectedLink ? (
        <RelationDock
          link={selectedLink}
          index={nodeIndex}
          alerts={alerts}
          onSelectNode={(node) => {
            setSelectedLink(null);
            setSelected(node);
          }}
          onClear={() => setSelectedLink(null)}
        />
      ) : (
        <AnalysisDock
          node={selected}
          alerts={selectedAlerts}
          connectionCount={selected ? (degrees.get(selected.id) ?? 0) : 0}
          allNodes={graph.nodes}
          flagged={flagged}
          counts={counts}
          riskyLinks={riskyLinks}
          onSelect={setSelected}
          onSelectLink={setSelectedLink}
          onClear={() => setSelected(null)}
        />
      )}
    </PageHeader>
  );
}

/**
 * What the edge colours mean, and — just as importantly — what they do not.
 *
 * The disclaimer is not decoration. Painting a line red reads as "this interaction was
 * malicious", and no detector in this system scores an interaction; the tone is inherited from
 * the entities at each end. Stating that in the legend is the difference between a useful visual
 * shorthand and a claim the project cannot support (see lib/graph-risk.ts).
 */
function RiskLegend({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2.5">
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={enabled}
        className={`inline-flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-[11px] font-medium ring-1 transition-colors ${
          enabled
            ? "bg-[var(--color-accent)]/14 text-[var(--color-accent)] ring-[var(--color-accent)]/30"
            : "bg-[var(--color-raise)] text-[var(--color-ink-dim)] ring-[var(--color-hairline)]"
        }`}
      >
        <Spline size={12} aria-hidden />
        Tone edges by risk
      </button>

      {enabled && (
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-[var(--color-ink-faint)]"
          title="Inherited from the entities each line connects — no detector scores a relation."
        >
          <LegendKey
            colour="var(--color-critical)"
            height={4}
            opacity={1}
            label="Both ends flagged"
          />
          <LegendKey
            colour="var(--color-critical)"
            height={1.5}
            opacity={0.35}
            label="One end flagged"
          />
          <LegendKey colour="var(--color-ok)" height={1.5} opacity={0.6} label="Neither" />
          <span>
            hue follows the worse endpoint&apos;s severity — inherited, not scored per relation
          </span>
        </div>
      )}
    </div>
  );
}

/** One key in the edge legend. Width carries the tier as well as the colour does. */
function LegendKey({
  colour,
  height,
  opacity,
  label,
}: {
  colour: string;
  height: number;
  opacity: number;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="rounded-chip"
        style={{ background: colour, width: 18, height, opacity }}
        aria-hidden
      />
      {label}
    </span>
  );
}

/**
 * The inspector for one relation.
 *
 * Separates the two things an edge knows, because they are different kinds of statement:
 * *measured* facts about the interactions themselves (how many, how many failed, over what
 * period), and *inherited* risk from the entities at either end. Merging them into one "edge
 * score" would be the easy thing to build and would be asserting something no detector produced.
 */
function RelationDock({
  link,
  index,
  alerts,
  onSelectNode,
  onClear,
}: {
  link: GraphLink;
  index: Map<string, GraphNode>;
  alerts: AlertSummary[];
  onSelectNode: (node: GraphNode) => void;
  onClear: () => void;
}) {
  const palette = useNodeTypeMeta();
  const source = index.get(link.source);
  const target = index.get(link.target);
  const risk = edgeRisk(link, index);
  const band = edgeBand(risk);
  const failed = failureRatio(link);

  const endpointAlerts = alerts.filter((alert) => {
    const id = `${alert.entity_type}:${alert.entity_id}`;
    return id === link.source || id === link.target;
  });

  const span =
    link.first_seen && link.last_seen
      ? `${new Date(link.first_seen).toLocaleString()} → ${new Date(link.last_seen).toLocaleString()}`
      : "not recorded";

  return (
    <Panel className="overflow-hidden" elevation={1}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="grid size-9 shrink-0 place-items-center rounded-control ring-1"
            style={{
              background: `color-mix(in oklab, ${BAND_TOKEN[band]} 14%, transparent)`,
              color: BAND_TOKEN[band],
              boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${BAND_TOKEN[band]} 30%, transparent)`,
            }}
          >
            <Spline size={17} />
          </span>
          <div className="min-w-0">
            <h2 className="flex flex-wrap items-center gap-1.5 text-sm font-semibold text-[var(--color-ink)]">
              <EndpointButton node={source} id={link.source} onSelect={onSelectNode} />
              <span className="rounded-chip bg-[var(--color-raise)] px-2 py-0.5 font-mono text-[11px] font-normal text-[var(--color-accent)] ring-1 ring-[var(--color-hairline)]">
                {link.relation}
              </span>
              <span aria-hidden className="text-[var(--color-ink-faint)]">→</span>
              <EndpointButton node={target} id={link.target} onSelect={onSelectNode} />
            </h2>
            <p className="mt-0.5 text-xs text-[var(--color-ink-faint)]">
              {source ? (palette[source.type]?.label ?? source.type) : "unknown"} to{" "}
              {target ? (palette[target.type]?.label ?? target.type) : "unknown"} · {span}
            </p>
          </div>
        </div>
        <button
          onClick={onClear}
          className="inline-flex items-center gap-1.5 rounded-chip bg-[var(--color-raise)] px-3 py-1.5 text-[11px] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-hairline)] hover:text-[var(--color-ink)]"
        >
          <X size={12} /> Close
        </button>
      </div>

      <div className="grid divide-y divide-[var(--color-line)] lg:grid-cols-2 lg:divide-x lg:divide-y-0">
        <div className="p-5">
          <SectionLabel icon={<Activity size={13} />} label="Measured on this relation" />
          <dl className="mt-3 grid grid-cols-2 gap-4">
            <Figure label="Interactions" value={link.weight.toLocaleString()} />
            <Figure
              label="Failed"
              value={`${link.failures.toLocaleString()} (${Math.round(failed * 100)}%)`}
              tone={failed >= 0.5 ? "var(--color-critical)" : undefined}
            />
          </dl>
          <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            Blocked, refused and errored outcomes on this pair during the snapshot window. A fact
            about the events — unlike the risk beside it, no detector is being quoted.
          </p>
        </div>

        <div className="p-5">
          <SectionLabel icon={<ShieldAlert size={13} />} label="Risk inherited from the endpoints" />
          <div className="mt-3 flex items-center gap-3">
            <span
              className="rounded-chip px-2.5 py-1 text-xs font-medium ring-1"
              style={{
                color: BAND_TOKEN[band],
                background: `color-mix(in oklab, ${BAND_TOKEN[band]} 12%, transparent)`,
                boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${BAND_TOKEN[band]} 30%, transparent)`,
              }}
            >
              {band === "none" ? "No alert on either end" : BAND_LABEL[band]}
            </span>
            <span className="font-mono text-xs tabular-nums text-[var(--color-ink-dim)]">
              {risk.toFixed(2)}
            </span>
          </div>
          <p className="mt-2.5 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
            The higher of the two entities&apos; scores. GraphGuard scores entities, never
            interactions, so this says the relation <em>touches</em> something flagged — not that
            the traffic on it was malicious.
          </p>

          {endpointAlerts.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {endpointAlerts.slice(0, 4).map((alert) => (
                <li key={alert.id}>
                  <Link
                    to={`/alerts/${alert.id}`}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition hover:bg-[var(--color-raise)]"
                  >
                    <SeverityBadge severity={severityOf(alert.window_score)} />
                    <span className="min-w-0 flex-1 truncate font-mono">{alert.entity_id}</span>
                    <span className="shrink-0 text-[10px] text-[var(--color-ink-faint)]">
                      {alert.detector}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

function EndpointButton({
  node,
  id,
  onSelect,
}: {
  node: GraphNode | undefined;
  id: string;
  onSelect: (node: GraphNode) => void;
}) {
  const label = node?.label ?? id.slice(id.indexOf(":") + 1);
  if (!node) return <span className="font-mono text-sm">{label}</span>;
  return (
    <button
      type="button"
      onClick={() => onSelect(node)}
      className="font-mono text-sm hover:text-[var(--color-accent)]"
      title="Inspect this entity"
    >
      {label}
    </button>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <dt className="text-[11px] text-[var(--color-ink-faint)]">{label}</dt>
      <dd
        className="mt-0.5 font-mono text-lg font-semibold tabular-nums"
        style={{ color: tone ?? "var(--color-ink)" }}
      >
        {value}
      </dd>
    </div>
  );
}

function MapMetric({ label, value, border = false, alert = false }: { label: string; value: number; border?: boolean; alert?: boolean }) {
  return (
    <div className={`min-w-20 px-3 py-2.5 ${border ? "border-l border-[var(--color-line)]" : ""}`}>
      <div className="text-[9px] font-semibold tracking-wider text-[var(--color-ink-faint)] uppercase">{label}</div>
      <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${alert ? "text-[var(--color-critical)]" : "text-[var(--color-ink)]"}`}>
        {value.toLocaleString()}
      </div>
    </div>
  );
}

function AnalysisDock({
  node,
  alerts,
  connectionCount,
  allNodes,
  flagged,
  counts,
  riskyLinks,
  onSelect,
  onSelectLink,
  onClear,
}: {
  node: GraphNode | null;
  alerts: AlertSummary[];
  connectionCount: number;
  allNodes: GraphNode[];
  flagged: GraphNode[];
  counts: Map<string, number>;
  riskyLinks: { link: GraphLink; risk: number }[];
  onSelect: (node: GraphNode) => void;
  onSelectLink: (link: GraphLink) => void;
  onClear: () => void;
}) {
  const palette = useNodeTypeMeta();

  if (!node) {
    return (
      <Panel className="overflow-hidden" elevation={1}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-control bg-[var(--color-wash-accent)] text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/20"><Eye size={17} /></span>
            <div>
              <h2 className="text-sm font-semibold text-[var(--color-ink)]">Network overview</h2>
              <p className="text-xs text-[var(--color-ink-faint)]">Select an entity to open its behavioural analysis here.</p>
            </div>
          </div>
          <span className="rounded-chip bg-[var(--color-raise)] px-3 py-1.5 text-[11px] text-[var(--color-ink-faint)] ring-1 ring-[var(--color-hairline)]">Nothing selected</span>
        </div>

        <div className="grid divide-y divide-[var(--color-line)] lg:grid-cols-[1.2fr_1fr] lg:divide-x lg:divide-y-0">
          <div className="p-5">
            <SectionLabel icon={<GitBranch size={13} />} label="Entity distribution" />
            <div className="mt-4 flex h-3 overflow-hidden rounded-chip bg-[var(--color-line)]">
              {[...counts.entries()].map(([type, count]) => (
                <span key={type} style={{ width: `${(count / allNodes.length) * 100}%`, background: palette[type]?.color ?? "var(--color-ink-faint)" }} title={`${palette[type]?.label ?? type}: ${count}`} />
              ))}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
              {[...counts.entries()].map(([type, count]) => (
                <div key={type} className="rounded-control bg-[var(--color-raise-subtle)] px-3 py-2.5 ring-1 ring-[var(--color-hairline)]">
                  <div className="flex items-center gap-2 text-[11px] text-[var(--color-ink-faint)]">
                    <span className="size-2 rounded-full" style={{ background: palette[type]?.color }} />
                    <span className="truncate">{palette[type]?.label ?? type}</span>
                  </div>
                  <div className="mt-1 font-mono text-base font-semibold tabular-nums">{count}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="p-5">
            <SectionLabel icon={<ShieldAlert size={13} />} label="Highest-risk entities" />
            {flagged.length > 0 ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                {flagged.slice(0, 4).map((candidate) => (
                  <button key={candidate.id} type="button" onClick={() => onSelect(candidate)} className="group flex items-center gap-3 rounded-control bg-[var(--color-raise-subtle)] px-3 py-2.5 text-left ring-1 ring-[var(--color-hairline)] transition hover:bg-[var(--color-raise)] hover:ring-[var(--color-accent)]/30">
                    <span className="grid size-8 shrink-0 place-items-center rounded-lg text-[11px] font-bold text-white" style={{ background: palette[candidate.type]?.color }}>{palette[candidate.type]?.letter ?? "?"}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-xs text-[var(--color-ink)]">{candidate.label}</span>
                      <span className="mt-0.5 block text-[10px] text-[var(--color-ink-faint)]">{candidate.alerts} alert{candidate.alerts === 1 ? "" : "s"}</span>
                    </span>
                    <span className="font-mono text-xs font-semibold text-[var(--color-critical)] tabular-nums">{Math.round(candidate.risk * 100)}</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-xs text-[var(--color-ink-faint)]">No entity was flagged in this run.</p>
            )}

            {/* Clicking a thin line in a projected 3-D layout is fiddly at the best of times, so
                the edges worth inspecting are also reachable as a list. Same selection, no aim
                required. */}
            {riskyLinks.length > 0 && (
              <div className="mt-5 border-t border-[var(--color-line)] pt-4">
                <SectionLabel icon={<Spline size={13} />} label="Relations touching a flagged entity" />
                <ul className="mt-3 space-y-1.5">
                  {riskyLinks.slice(0, 5).map(({ link, risk }) => (
                    <li key={linkKey(link)}>
                      <button
                        type="button"
                        onClick={() => onSelectLink(link)}
                        className="flex w-full items-center gap-2.5 rounded-control px-2.5 py-2 text-left text-xs transition hover:bg-[var(--color-raise)]"
                      >
                        <span
                          className="h-1 w-4 shrink-0 rounded-chip"
                          style={{ background: BAND_TOKEN[edgeBand(risk)] }}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
                          {link.source.slice(link.source.indexOf(":") + 1)}
                          <span className="mx-1 text-[var(--color-accent)]">{link.relation}</span>
                          {link.target.slice(link.target.indexOf(":") + 1)}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-[var(--color-ink-faint)] tabular-nums">
                          {link.weight.toLocaleString()}×
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[10px] text-[var(--color-ink-faint)]">
                  Ranked by the risk each inherits from its endpoints, not by any score of its own.
                </p>
              </div>
            )}
          </div>
        </div>
      </Panel>
    );
  }

  const meta = palette[node.type] ?? { color: "#9daac2", letter: "?", label: node.type };
  const profile = RATE_ATTRIBUTE_LIST.flatMap((label) => {
    const value = numericAttribute(node, label);
    return value === null ? [] : [{ label: SHORT_RATE_LABELS[label] ?? label, value }];
  });
  const scalarAttributes = Object.entries(node.attributes).filter(([label]) => !RATE_ATTRIBUTES.has(label));

  return (
    <Panel className="overflow-hidden" elevation={2}>
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-line)] px-5 py-4">
        <span className="grid size-11 shrink-0 place-items-center rounded-control text-sm font-bold text-white shadow-[var(--elev-1)]" style={{ background: meta.color }}>{meta.letter}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate font-mono text-sm font-semibold text-[var(--color-ink)]">{node.label}</h2>
            {node.alerts > 0 ? <SeverityBadge severity={severityOf(node.risk)} /> : <span className="rounded-chip bg-[var(--color-wash-ok)] px-2.5 py-1 text-xs text-[var(--color-ok)] ring-1 ring-[var(--color-ok)]/25">Not flagged</span>}
          </div>
          <p className="mt-0.5 text-xs text-[var(--color-ink-faint)]">{meta.label} · latest analysis window</p>
        </div>
        <button type="button" onClick={onClear} className="inline-flex items-center gap-1.5 rounded-control px-2.5 py-1.5 text-xs text-[var(--color-ink-faint)] transition hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]"><X size={13} /> Clear selection</button>
      </div>

      <div className="grid divide-y divide-[var(--color-line)] xl:grid-cols-[240px_minmax(0,1.5fr)_minmax(280px,0.9fr)] xl:divide-x xl:divide-y-0">
        <div className="p-5">
          <SectionLabel icon={<CircleGauge size={13} />} label="Risk and reach" />
          <div className="mt-4 flex flex-col items-center gap-4 sm:flex-row xl:flex-col">
            <RiskRing value={node.risk} flagged={node.alerts > 0} />
            <div className="grid flex-1 grid-cols-2 gap-2 xl:w-full">
              <MiniMetric label="Connections" value={connectionCount} />
              <MiniMetric label="Alerts" value={node.alerts} alert={node.alerts > 0} />
              <MiniMetric label="Events" value={numericAttribute(node, "Events in window")} />
              <MiniMetric label="Sessions" value={numericAttribute(node, "Distinct sessions")} />
            </div>
          </div>
        </div>

        <div className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <SectionLabel icon={<Activity size={13} />} label="Behaviour profile" />
            <span className="text-[10px] text-[var(--color-ink-faint)]">Observed shares, not detector thresholds</span>
          </div>
          <div className="mt-2 grid items-center gap-3 md:grid-cols-[minmax(240px,0.9fr)_minmax(250px,1.1fr)]">
            <BehaviourProfileChart data={profile} />
            <dl className="space-y-3">
              {RATE_ATTRIBUTE_LIST.map((label) => {
                const value = numericAttribute(node, label);
                return value === null ? null : <RateRow key={label} label={label} value={value} />;
              })}
            </dl>
          </div>
          {scalarAttributes.length > 0 && (
            <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 border-t border-[var(--color-line)] pt-4 sm:grid-cols-3">
              {scalarAttributes.map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="truncate text-[10px] text-[var(--color-ink-faint)]">{label}</dt>
                  <dd className="mt-1 truncate font-mono text-xs font-medium text-[var(--color-ink)] tabular-nums">{typeof value === "number" ? value.toLocaleString() : value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        <div className="p-5">
          <SectionLabel icon={<ShieldAlert size={13} />} label="Detector signals" />
          {alerts.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {alerts.slice(0, 5).map((alert) => (
                <li key={alert.id}>
                  <Link to={`/alerts/${alert.id}`} className="block rounded-control bg-[var(--color-raise-subtle)] p-3 ring-1 ring-[var(--color-hairline)] transition hover:bg-[var(--color-raise)] hover:ring-[var(--color-accent)]/30">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="truncate text-[var(--color-ink-dim)]">{alert.detector}</span>
                      <span className="font-mono font-semibold tabular-nums">{alert.window_score.toFixed(2)}</span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-chip bg-[var(--color-line)]">
                      <div className="h-full rounded-chip bg-[var(--color-critical)]" style={{ width: `${Math.min(100, Math.max(0, alert.window_score * 100))}%` }} />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-4 rounded-control bg-[var(--color-wash-ok)] p-4 text-xs leading-relaxed text-[var(--color-ok)] ring-1 ring-[var(--color-ok)]/20">No detector flagged this entity in the latest run.</div>
          )}
          <p className="mt-4 border-t border-[var(--color-line)] pt-4 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">GraphGuard measures behaviour from API and gateway telemetry. Owner, directory, and asset-inventory context require a separate enrichment source.</p>
        </div>
      </div>
    </Panel>
  );
}

const RATE_ATTRIBUTE_LIST = ["Error rate", "Refusal rate", "Out-of-hours share", "Weekend share"];
const RATE_ATTRIBUTES = new Set(RATE_ATTRIBUTE_LIST);
const SHORT_RATE_LABELS: Record<string, string> = {
  "Error rate": "Errors",
  "Refusal rate": "Refusals",
  "Out-of-hours share": "Off hours",
  "Weekend share": "Weekend",
};

function numericAttribute(node: GraphNode, label: string): number | null {
  const value = node.attributes[label];
  return typeof value === "number" ? value : null;
}

function SectionLabel({ icon, label }: { icon: React.ReactNode; label: string }) {
  return <div className="flex items-center gap-2 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase"><span className="text-[var(--color-accent)]">{icon}</span>{label}</div>;
}

function RiskRing({ value, flagged }: { value: number; flagged: boolean }) {
  const radius = 43;
  const circumference = 2 * Math.PI * radius;
  const progress = circumference * (1 - Math.min(1, Math.max(0, value)));
  const severity = severityOf(value);
  const color = flagged ? `var(--color-${severity})` : "var(--color-ok)";
  return (
    <div className="relative size-28 shrink-0" role="img" aria-label={`Risk score ${Math.round(value * 100)} percent`}>
      <svg viewBox="0 0 112 112" className="size-full -rotate-90">
        <circle cx="56" cy="56" r={radius} fill="none" stroke="var(--color-line)" strokeWidth="8" />
        <circle cx="56" cy="56" r={radius} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={progress} />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div><div className="font-mono text-xl font-semibold tabular-nums">{Math.round(value * 100)}</div><div className="text-[9px] tracking-wide text-[var(--color-ink-faint)] uppercase">Risk score</div></div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value, alert = false }: { label: string; value: number | null; alert?: boolean }) {
  return (
    <div className="rounded-control bg-[var(--color-raise-subtle)] px-3 py-2.5 ring-1 ring-[var(--color-hairline)]">
      <dt className="text-[10px] text-[var(--color-ink-faint)]">{label}</dt>
      <dd className={`mt-1 font-mono text-sm font-semibold tabular-nums ${alert ? "text-[var(--color-critical)]" : "text-[var(--color-ink)]"}`}>{value === null ? "—" : value.toLocaleString()}</dd>
    </div>
  );
}

function RateRow({ label, value }: { label: string; value: number }) {
  const percent = Math.min(100, Math.max(0, value * 100));
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-xs"><dt className="text-[var(--color-ink-faint)]">{label}</dt><dd className="font-mono text-[var(--color-ink)] tabular-nums">{Math.round(percent)}%</dd></div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-chip bg-[var(--color-line)]"><div className="h-full rounded-chip bg-[var(--color-accent)]" style={{ width: `${percent}%` }} /></div>
    </div>
  );
}

function CanvasButton({ children, onClick, label }: { children: React.ReactNode; onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className="grid size-9 place-items-center rounded-control border border-[var(--color-line)] bg-[var(--color-surface)]/88 text-[var(--color-ink-dim)] shadow-[var(--elev-1)] backdrop-blur-md transition hover:border-[var(--color-accent)]/35 hover:text-[var(--color-ink)] active:scale-95">{children}</button>
  );
}
