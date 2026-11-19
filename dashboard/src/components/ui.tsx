/**
 * Shared presentational primitives.
 *
 * Severity is expressed with a colour *and* a shape or icon everywhere it appears. Colour
 * alone excludes colour-blind analysts, and this is a queue read under time pressure —
 * docs/DESIGN.md §11.2 makes this a requirement rather than a preference.
 *
 * Form controls live in ./fields and loading/empty/error states in ./feedback; both are
 * re-exported here so a page has one import to reach for.
 */

import type { ReactNode, Ref } from "react";
import { useCallback, useId, useRef } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, CheckCircle2, ChevronsUpDown, CircleDot, Info, ShieldAlert, TrendingDown, TrendingUp } from "lucide-react";
import { Link } from "react-router-dom";
import { useCanvasPalette } from "../lib/canvas-palette";
import type { NodeTypeMeta } from "../lib/canvas-palette";

export { Input, Select, Textarea } from "./fields";
export {
  EmptyState,
  ErrorState,
  Skeleton,
  SkeletonPanel,
  SkeletonStats,
  Spinner,
} from "./feedback";

export type Severity = "critical" | "high" | "medium" | "low";

export function severityOf(score: number): Severity {
  if (score >= 0.9) return "critical";
  if (score >= 0.75) return "high";
  if (score >= 0.5) return "medium";
  return "low";
}

const SEVERITY_STYLE: Record<Severity, { text: string; bg: string; ring: string; Icon: typeof Info }> = {
  critical: {
    text: "text-[var(--color-critical)]",
    bg: "bg-[var(--color-critical)]/12",
    ring: "ring-[var(--color-critical)]/35",
    Icon: ShieldAlert,
  },
  high: {
    text: "text-[var(--color-high)]",
    bg: "bg-[var(--color-high)]/12",
    ring: "ring-[var(--color-high)]/35",
    Icon: AlertTriangle,
  },
  medium: {
    text: "text-[var(--color-medium)]",
    bg: "bg-[var(--color-medium)]/12",
    ring: "ring-[var(--color-medium)]/35",
    Icon: CircleDot,
  },
  low: {
    text: "text-[var(--color-low)]",
    bg: "bg-[var(--color-low)]/12",
    ring: "ring-[var(--color-low)]/35",
    Icon: CheckCircle2,
  },
};

export function SeverityBadge({ severity, label }: { severity: Severity; label?: string }) {
  const style = SEVERITY_STYLE[severity];
  const { Icon } = style;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-xs font-medium ring-1 ${style.bg} ${style.text} ${style.ring}`}
    >
      <Icon size={13} aria-hidden />
      {label ?? severity}
    </span>
  );
}

const STATE_LABEL: Record<string, string> = {
  new: "New",
  investigating: "Investigating",
  true_positive: "True positive",
  false_positive: "False positive",
  exported: "Exported",
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

/**
 * Alert triage state, and run status.
 *
 * Run statuses were being passed in and falling through to the neutral grey tone, so a
 * failed run looked exactly like a queued one. They get their own tones here.
 */
export function StatePill({ state }: { state: string }) {
  const tone =
    state === "true_positive" || state === "failed"
      ? "text-[var(--color-critical)] bg-[var(--color-critical)]/12 ring-[var(--color-critical)]/30"
      : state === "false_positive"
        ? "text-[var(--color-ink-faint)] bg-[var(--color-raise)] ring-[var(--color-hairline)]"
        : state === "investigating" || state === "running"
          ? "text-[var(--color-accent)] bg-[var(--color-accent)]/12 ring-[var(--color-accent)]/30"
          : state === "exported" || state === "succeeded"
            ? "text-[var(--color-ok)] bg-[var(--color-ok)]/12 ring-[var(--color-ok)]/30"
            : "text-[var(--color-ink-dim)] bg-[var(--color-raise)] ring-[var(--color-hairline)]";
  return (
    <span className={`inline-flex rounded-chip px-2.5 py-1 text-xs font-medium ring-1 ${tone}`}>
      {STATE_LABEL[state] ?? state}
    </span>
  );
}

export function Panel({
  children,
  className = "",
  brand = false,
  pad = false,
  elevation,
  id,
  ref,
}: {
  children: ReactNode;
  className?: string;
  brand?: boolean;
  /** Apply the standard panel inset, so callers stop hand-appending `px-5 pb-5`. */
  pad?: boolean;
  elevation?: 1 | 2 | 3;
  /** A fragment target, so a link elsewhere in the console can land on this panel. */
  id?: string;
  /** React 19 passes ref as an ordinary prop; no forwardRef wrapper needed. */
  ref?: Ref<HTMLElement>;
}) {
  const skin = brand ? "panel-brand" : "panel";
  const elev = elevation ? ` elev-${elevation}` : "";
  return (
    <section id={id} ref={ref} className={`${skin}${elev} ${pad ? "px-5 py-5" : ""} ${className}`}>
      {children}
    </section>
  );
}

export function PanelHead({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold tracking-tight text-[var(--color-ink)]">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-[var(--color-ink-faint)]">{subtitle}</p>}
      </div>
      {action}
    </header>
  );
}

/**
 * The page frame.
 *
 * Every route used to open with its own copy of the same wrapper, header and h1, which
 * meant eight independent opinions about spacing. One component means the refresh
 * control, the time range and anything else the chrome grows later land in the same
 * place on every page.
 */
export function PageHeader({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="animate-enter space-y-5 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1>{title}</h1>
          {subtitle && (
            <p className="mt-1 max-w-3xl text-sm text-[var(--color-ink-dim)]">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

export type Trend = "up" | "down" | "neutral";

export function Stat({
  label,
  value,
  hint,
  accent = false,
  icon,
  delta,
  deltaLabel,
  trend = "neutral",
}: {
  label: string;
  value: string;
  hint?: ReactNode;
  accent?: boolean;
  icon?: ReactNode;
  /** Pre-formatted, e.g. "+12%" or "−3". Rendered verbatim. */
  delta?: string;
  deltaLabel?: string;
  /**
   * Whether the movement is good or bad — NOT which way the number went. Open alerts
   * falling is "up"; the caller owns that inversion because only the caller knows what
   * the metric means.
   */
  trend?: Trend;
}) {
  const tone =
    trend === "up"
      ? "text-[var(--color-ok)]"
      : trend === "down"
        ? "text-[var(--color-critical)]"
        : "text-[var(--color-ink-faint)]";
  const TrendIcon = trend === "up" ? TrendingUp : trend === "down" ? TrendingDown : null;
  return (
    <div
      className={
        accent
          ? "panel-brand relative overflow-hidden px-5 py-4"
          : "panel relative overflow-hidden px-5 py-4"
      }
    >
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-[var(--color-ink-dim)]">{label}</span>
        {icon && <span className="text-[var(--color-accent)]">{icon}</span>}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-[28px] leading-none font-semibold tracking-tight tabular-nums">
          {value}
        </span>
        {delta && (
          <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${tone}`}>
            {TrendIcon && <TrendIcon size={12} aria-hidden />}
            {delta}
          </span>
        )}
      </div>
      {(hint || deltaLabel) && (
        <div className="mt-2 text-xs text-[var(--color-ink-faint)]">{hint ?? deltaLabel}</div>
      )}
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger" | "icon";

const BUTTON_TONE: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-accent)] text-[var(--color-on-accent)] hover:bg-[var(--color-accent-soft)]",
  secondary:
    "bg-[var(--color-surface-2)] text-[var(--color-ink)] ring-1 ring-[var(--color-line)] hover:bg-[var(--color-raise-strong)]",
  outline:
    "bg-transparent text-[var(--color-ink-dim)] ring-1 ring-[var(--color-line)] hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]",
  ghost:
    "bg-[var(--color-raise)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-hairline)] hover:bg-[var(--color-raise-strong)] hover:text-[var(--color-ink)]",
  danger:
    "bg-[var(--color-critical)]/15 text-[var(--color-critical)] ring-1 ring-[var(--color-critical)]/30 hover:bg-[var(--color-critical)]/25",
  icon: "bg-[var(--color-surface)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-line)] hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]",
};

export function Button({
  children,
  onClick,
  variant = "primary",
  size = "md",
  type = "button",
  disabled,
  loading = false,
  to,
  href,
  title,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  type?: "button" | "submit";
  disabled?: boolean;
  /** Swaps the label for a spinner and blocks input, without changing the button's width. */
  loading?: boolean;
  /** Renders a react-router <Link> instead of a <button>. */
  to?: string;
  href?: string;
  title?: string;
  className?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-control font-medium transition-colors duration-150 ease-out disabled:opacity-50 disabled:cursor-not-allowed motion-safe:active:scale-[0.97]";
  const sizing =
    variant === "icon"
      ? size === "sm"
        ? "size-8"
        : "size-9"
      : size === "sm"
        ? "px-3 py-1.5 text-xs"
        : "px-4 py-2.5 text-sm";
  const cls = `${base} ${sizing} ${BUTTON_TONE[variant]} ${className}`;

  if (to) {
    return (
      <Link to={to} title={title} className={cls}>
        {children}
      </Link>
    );
  }
  if (href) {
    return (
      <a href={href} title={title} className={cls}>
        {children}
      </a>
    );
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      aria-busy={loading || undefined}
      className={cls}
    >
      {loading ? (
        <>
          <span className="size-3.5 animate-spin rounded-full border-2 border-current/30 border-t-current" />
          {variant !== "icon" && children}
        </>
      ) : (
        children
      )}
    </button>
  );
}

/**
 * A selectable chip.
 *
 * One implementation replacing four: the map's type filters, the queue's state filters,
 * the case status buttons and the appearance switch had each grown their own class
 * string, so the same control looked slightly different on every page.
 */
export function Chip({
  children,
  selected = false,
  onClick,
  tint,
  title,
  className = "",
}: {
  children: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  /** A CSS colour for the selected state, when the chip stands for a coloured category. */
  tint?: string;
  title?: string;
  className?: string;
}) {
  const base =
    "inline-flex items-center gap-1.5 rounded-chip px-2.5 py-1 text-xs font-medium ring-1 transition-colors duration-150 ease-out motion-safe:active:scale-[0.97]";
  const tone = selected
    ? "bg-[var(--color-accent)]/14 text-[var(--color-accent)] ring-[var(--color-accent)]/30"
    : "bg-[var(--color-raise)] text-[var(--color-ink-dim)] ring-[var(--color-hairline)] hover:bg-[var(--color-raise-strong)] hover:text-[var(--color-ink)]";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={onClick ? selected : undefined}
      title={title}
      className={`${base} ${tint && selected ? "ring-current" : tone} ${className}`}
      style={tint && selected ? { color: tint, background: `${tint}22` } : undefined}
    >
      {children}
    </button>
  );
}

/**
 * A removable filter chip.
 *
 * Every active filter — whatever kind it is — carries its own removal closure, so the
 * bar can render a flat list without knowing what any given filter means.
 */
export function FilterChip({
  label,
  value,
  onRemove,
}: {
  label: string;
  value: string;
  onRemove: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-chip bg-[var(--color-accent)]/10 py-1 pr-1.5 pl-2.5 text-xs font-medium text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/30">
      <span className="text-[var(--color-ink-faint)]">{label}:</span>
      {value}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove the ${label} filter`}
        className="grid size-4 place-items-center rounded-chip text-current opacity-70 transition-opacity hover:opacity-100"
      >
        ×
      </button>
    </span>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

/**
 * A segmented control.
 *
 * Roving tabindex: one stop in the tab order, arrows to move within it. The sliding
 * indicator is a CSS transform driven by the selected index rather than a layout
 * animation library — the widths are equal, so `translateX(index * 100%)` is exact.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className = "",
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  className?: string;
}) {
  const id = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const index = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const last = options.length - 1;
      let next = -1;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = index === last ? 0 : index + 1;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = index === 0 ? last : index - 1;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = last;
      if (next < 0) return;
      event.preventDefault();
      const option = options[next];
      if (!option) return;
      onChange(option.value);
      // Move focus with selection, so the roving tabindex stays on the active item.
      listRef.current?.querySelectorAll("button")[next]?.focus();
    },
    [index, options, onChange],
  );

  return (
    <div
      ref={listRef}
      role="group"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={`relative isolate inline-flex gap-1 rounded-chip border border-[var(--color-line)] bg-[var(--color-surface-2)] p-1 ${className}`}
    >
      <span
        aria-hidden
        className="absolute inset-y-1 left-1 -z-10 rounded-chip bg-[var(--color-accent)] transition-transform duration-200 ease-pop"
        style={{
          width: `calc((100% - 0.5rem - ${(options.length - 1) * 0.25}rem) / ${options.length})`,
          transform: `translateX(calc(${index} * (100% + 0.25rem)))`,
        }}
      />
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            id={`${id}-${option.value}`}
            aria-pressed={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={`relative z-0 min-w-16 flex-1 rounded-chip px-3 py-1 text-xs font-medium transition-colors duration-150 ease-out ${
              active
                ? "text-[var(--color-on-accent)]"
                : "text-[var(--color-ink-dim)] hover:text-[var(--color-ink)]"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/* --- table ---------------------------------------------------------------
   Hand-rolled rather than a table library. The queue renders one page of rows
   with four sortable columns; a headless table engine would be more code than
   this and another dependency to justify at the defence. */

export function Table({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={`w-full border-collapse text-sm ${className}`}>{children}</table>
    </div>
  );
}

export type SortDirection = "asc" | "desc";

/**
 * A column header, optionally sortable.
 *
 * Unsorted columns still show an affordance at low opacity, so the analyst can see
 * which columns are sortable without hovering every one of them.
 */
export function Th({
  children,
  sortKey,
  activeKey,
  direction,
  onSort,
  align = "left",
  className = "",
}: {
  children: ReactNode;
  sortKey?: string;
  activeKey?: string;
  direction?: SortDirection;
  onSort?: (key: string) => void;
  align?: "left" | "right";
  className?: string;
}) {
  const base = `sticky top-0 z-10 bg-[var(--color-surface)] px-3 py-2.5 text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase ${
    align === "right" ? "text-right" : "text-left"
  } ${className}`;

  if (!sortKey || !onSort) {
    return (
      <th scope="col" className={base}>
        {children}
      </th>
    );
  }

  const active = activeKey === sortKey;
  const Icon = !active ? ChevronsUpDown : direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
      className={base}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1.5 uppercase transition-colors hover:text-[var(--color-ink)] ${
          align === "right" ? "flex-row-reverse" : ""
        }`}
      >
        {children}
        <Icon
          size={12}
          aria-hidden
          className={active ? "text-[var(--color-accent)]" : "opacity-40"}
        />
      </button>
    </th>
  );
}

export function Td({
  children,
  align = "left",
  className = "",
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`px-3 py-2.5 ${align === "right" ? "text-right" : "text-left"} ${className}`}
    >
      {children}
    </td>
  );
}

/**
 * The entity-type palette for the active theme.
 *
 * Resolved from CSS rather than duplicated here. It re-resolves on a theme change because
 * the returned values are literal colours — see lib/canvas-palette.ts for why the map needs
 * literals at all.
 */
export function useNodeTypeMeta(): Record<string, NodeTypeMeta> {
  return useCanvasPalette().types;
}

export function TypeChip({ type }: { type: string }) {
  const palette = useNodeTypeMeta();
  const meta = palette[type] ?? { color: "var(--color-ink-faint)", letter: "?", label: type };
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-ink-dim)]">
      <span
        className="grid size-4 place-items-center rounded-[5px] text-[9px] font-bold text-[var(--color-surface)]"
        style={{ background: meta.color }}
        aria-hidden
      >
        {meta.letter}
      </span>
      {meta.label}
    </span>
  );
}
