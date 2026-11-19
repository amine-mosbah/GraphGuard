/**
 * Loading, empty and error affordances.
 *
 * The rule this file exists to enforce: a page that is refetching must not disappear.
 * Before it, six pages returned a bare <Spinner/> as their entire body, so every
 * refresh blanked the screen and the analyst lost their place. A skeleton that mirrors
 * the real layout keeps the page's shape on screen and reads as "this is arriving"
 * rather than "this is gone".
 */

import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 px-5 py-8 text-sm text-[var(--color-ink-faint)]">
      <span className="size-4 animate-spin rounded-full border-2 border-[var(--color-line)] border-t-[var(--color-accent)]" />
      {label}…
    </div>
  );
}

/**
 * One shimmering block.
 *
 * `delay` staggers a group so it reads as a page assembling rather than as a single grey
 * slab pulsing. Kept in milliseconds because that is what the caller is thinking in.
 */
export function Skeleton({ className = "", delay = 0 }: { className?: string; delay?: number }) {
  return (
    <span
      aria-hidden
      className={`block animate-pulse rounded-field bg-[var(--color-raise)] ${className}`}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    />
  );
}

/**
 * A panel-shaped placeholder: header lines, then body rows.
 *
 * Sized from the real thing, so swapping it for loaded content does not shift layout.
 */
export function SkeletonPanel({
  rows = 4,
  className = "",
  label = "Loading",
}: {
  rows?: number;
  className?: string;
  label?: string;
}) {
  return (
    <section className={`panel px-5 pt-5 pb-5 ${className}`} role="status" aria-label={label}>
      <Skeleton className="h-4 w-40" delay={100} />
      <Skeleton className="mt-2 h-3 w-64" delay={200} />
      <div className="mt-5 space-y-2.5">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full" delay={300 + i * 60} />
        ))}
      </div>
    </section>
  );
}

/** The stat row's placeholder, matching the 4-up grid the pages actually render. */
export function SkeletonStats({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" role="status" aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="panel px-5 py-4">
          <Skeleton className="h-3 w-24" delay={100 + i * 60} />
          <Skeleton className="mt-3 h-7 w-16" delay={160 + i * 60} />
          <Skeleton className="mt-3 h-3 w-32" delay={220 + i * 60} />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="text-[var(--color-ink-faint)]">{icon}</div>
      <div className="text-sm font-medium text-[var(--color-ink-dim)]">{title}</div>
      <p className="max-w-sm text-xs leading-relaxed text-[var(--color-ink-faint)]">{body}</p>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/**
 * A failure the analyst can act on.
 *
 * Always paired with a retry. The console's data hub used to swallow load failures and
 * render empty chrome, which is indistinguishable from "there is genuinely nothing here"
 * — the worst possible ambiguity in a security tool.
 */
export function ErrorState({
  title = "Could not load this view",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center"
    >
      <div className="text-[var(--color-critical)]">
        <AlertTriangle size={26} />
      </div>
      <div className="text-sm font-medium text-[var(--color-ink)]">{title}</div>
      {message && (
        <p className="max-w-md text-xs leading-relaxed text-[var(--color-ink-faint)]">{message}</p>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-1 inline-flex items-center gap-2 rounded-control bg-[var(--color-raise)] px-3.5 py-2 text-xs font-medium text-[var(--color-ink-dim)] ring-1 ring-[var(--color-hairline)] transition-colors duration-150 ease-out hover:bg-[var(--color-raise-strong)] hover:text-[var(--color-ink)] motion-safe:active:scale-[0.97]"
        >
          <RefreshCw size={13} aria-hidden />
          Try again
        </button>
      )}
    </div>
  );
}
