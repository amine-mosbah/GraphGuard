/**
 * Top bar: global search, notification centre, assistant launcher, account menu.
 *
 * Search is command-palette style (⌘K / Ctrl-K) because the entities an analyst looks up —
 * a key, an address, a model — are things they already know the identifier of. Making them
 * navigate to a page to type it costs a step every time.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  ChevronDown,
  LogOut,
  Moon,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Sun,
  UserRound,
} from "lucide-react";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import type { AlertSummary } from "../lib/types";
import {
  Segmented,
  SeverityBadge,
  severityOf,
} from "./ui";

export function Topbar({
  alerts,
  onOpenAssistant,
  onRefresh,
  refreshing = false,
}: {
  alerts: AlertSummary[];
  onOpenAssistant: () => void;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  const { user, signOut } = useAuth();
  const { preference, theme, setPreference, toggle } = useTheme();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<"none" | "search" | "bell" | "account">("none");
  const searchRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const unread = alerts.filter((a) => a.state === "new");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen("search");
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setOpen("none");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen("none");
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return alerts
      .filter(
        (a) =>
          a.entity_id.toLowerCase().includes(needle) ||
          a.detector.toLowerCase().includes(needle) ||
          a.entity_type.toLowerCase().includes(needle),
      )
      .slice(0, 8);
  }, [query, alerts]);

  const initials = (user?.full_name || user?.email || "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  return (
    <div
      ref={rootRef}
      className="sticky top-0 z-30 flex items-center gap-4 border-b border-[var(--color-line)] bg-[var(--color-canvas)]/85 px-6 py-3.5 backdrop-blur-xl"
    >
      {/* --- search --- */}
      <div className="relative max-w-md flex-1">
        <Search
          size={16}
          className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-[var(--color-ink-faint)]"
        />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen("search");
          }}
          onFocus={() => setOpen("search")}
          placeholder="Search entities, detectors, alerts…"
          aria-label="Search"
          className="w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] py-2.5 pr-20 pl-10 text-sm text-[var(--color-ink)] placeholder:text-[var(--color-ink-faint)] focus:border-[var(--color-accent)]/40 focus:outline-none"
        />
        <kbd className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 rounded-md border border-[var(--color-line)] bg-[var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-ink-faint)]">
          ⌘K
        </kbd>

        {open === "search" && query.trim() !== "" && (
          <div className="panel elev-3 absolute top-full left-0 z-40 mt-2 w-full overflow-hidden p-1.5">
            {matches.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-[var(--color-ink-faint)]">
                Nothing matches “{query}”.
              </div>
            ) : (
              matches.map((alert) => (
                <button
                  key={alert.id}
                  onClick={() => {
                    navigate(`/alerts/${alert.id}`);
                    setOpen("none");
                    setQuery("");
                  }}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-[var(--color-raise)]"
                >
                  <SeverityBadge severity={severityOf(alert.window_score)} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-xs text-[var(--color-ink)]">
                      {alert.entity_type}:{alert.entity_id}
                    </span>
                    <span className="block truncate text-[11px] text-[var(--color-ink-faint)]">
                      {alert.detector}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      <div className="flex-1" />

      {/* --- assistant --- */}
      <button
        onClick={onOpenAssistant}
        className="inline-flex items-center gap-2 rounded-xl bg-[var(--color-accent)]/12 px-3.5 py-2 text-sm font-medium text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/25 transition hover:bg-[var(--color-accent)]/20"
      >
        <Sparkles size={15} />
        <span className="hidden sm:inline">Assistant</span>
      </button>

      {/* --- refresh --- */}
      {onRefresh && (
        <button
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Reload the current run"
          title="Reload the current run"
          className="grid size-9 place-items-center rounded-control bg-[var(--color-surface)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-line)] transition-colors duration-150 ease-out hover:text-[var(--color-ink)] disabled:opacity-60"
        >
          <RefreshCw size={16} className={refreshing ? "animate-spin" : undefined} />
        </button>
      )}

      {/* --- theme --- */}
      <button
        onClick={toggle}
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        className="grid size-9 place-items-center rounded-xl bg-[var(--color-surface)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-line)] transition hover:text-[var(--color-ink)]"
      >
        {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
      </button>

      {/* --- notifications --- */}
      <div className="relative">
        <button
          onClick={() => setOpen(open === "bell" ? "none" : "bell")}
          aria-label={`Notifications, ${unread.length} unreviewed`}
          className="relative grid size-9 place-items-center rounded-xl bg-[var(--color-surface)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-line)] transition hover:text-[var(--color-ink)]"
        >
          <Bell size={17} />
          {unread.length > 0 && (
            <span className="absolute -top-1 -right-1 grid min-w-[18px] place-items-center rounded-full bg-[var(--color-critical)] px-1 text-[10px] font-bold text-[var(--color-on-severity)] tabular-nums">
              {unread.length > 99 ? "99+" : unread.length}
            </span>
          )}
        </button>

        {open === "bell" && (
          <div className="panel elev-3 absolute top-full right-0 z-40 mt-2 w-[380px] overflow-hidden">
            <div className="flex items-center justify-between border-b border-[var(--color-line)] px-4 py-3">
              <span className="text-sm font-semibold">Notification centre</span>
              <span className="text-xs text-[var(--color-ink-faint)]">
                {unread.length} unreviewed
              </span>
            </div>
            <div className="max-h-[340px] overflow-y-auto">
              {unread.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-[var(--color-ink-faint)]">
                  Nothing awaiting review.
                </div>
              ) : (
                unread.slice(0, 12).map((alert) => (
                  <button
                    key={alert.id}
                    onClick={() => {
                      navigate(`/alerts/${alert.id}`);
                      setOpen("none");
                    }}
                    className="flex w-full items-start gap-3 border-b border-[var(--color-line)]/60 px-4 py-3 text-left last:border-0 hover:bg-[var(--color-raise)]"
                  >
                    <SeverityBadge severity={severityOf(alert.window_score)} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-[var(--color-ink)]">
                        <span className="font-mono">{alert.entity_id}</span> flagged by{" "}
                        {alert.detector.split(":")[0].replace(/_/g, " ")}
                      </span>
                      <span className="mt-0.5 block text-[11px] text-[var(--color-ink-faint)]">
                        window {alert.window_index} · score {alert.window_score.toFixed(2)}
                      </span>
                    </span>
                  </button>
                ))
              )}
            </div>
            <button
              onClick={() => {
                navigate("/alerts");
                setOpen("none");
              }}
              className="w-full border-t border-[var(--color-line)] px-4 py-2.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-raise)]"
            >
              Open the triage queue
            </button>
          </div>
        )}
      </div>

      {/* --- account --- */}
      <div className="relative">
        <button
          onClick={() => setOpen(open === "account" ? "none" : "account")}
          className="flex items-center gap-2.5 rounded-xl py-1 pr-2 pl-1 transition hover:bg-[var(--color-raise)]"
        >
          <span className="grid size-9 place-items-center rounded-full bg-[var(--color-brand-400)] text-xs font-semibold text-[var(--color-ink)]">
            {initials}
          </span>
          <span className="hidden text-left leading-tight sm:block">
            <span className="block max-w-[140px] truncate text-[13px] font-medium">
              {user?.full_name || user?.email}
            </span>
            <span className="block text-[11px] text-[var(--color-ink-faint)] capitalize">
              {user?.role}
            </span>
          </span>
          <ChevronDown size={14} className="text-[var(--color-ink-faint)]" />
        </button>

        {open === "account" && (
          <div className="panel elev-3 absolute top-full right-0 z-40 mt-2 w-64 overflow-hidden p-1.5">
            <div className="border-b border-[var(--color-line)] px-3 py-2.5">
              <div className="truncate text-xs text-[var(--color-ink-dim)]">Signed in as</div>
              <div className="truncate text-sm font-medium">{user?.email}</div>
            </div>
            <div className="px-3 pt-2 pb-1.5">
              <div className="mb-1.5 text-[10px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Appearance
              </div>
              <Segmented
                ariaLabel="Appearance"
                className="w-full"
                value={preference}
                onChange={setPreference}
                options={[
                  { value: "light", label: "Light" },
                  { value: "dark", label: "Dark" },
                  { value: "system", label: "System" },
                ]}
              />
            </div>
            <div className="my-1 h-px bg-[var(--color-line)]" />
            <MenuItem icon={<UserRound size={15} />} label="Profile" onClick={() => setOpen("none")} />
            <MenuItem
              icon={<Settings size={15} />}
              label="Settings"
              onClick={() => {
                navigate("/settings");
                setOpen("none");
              }}
            />
            <MenuItem icon={<LogOut size={15} />} label="Sign out" onClick={signOut} danger />
          </div>
        )}
      </div>
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition hover:bg-[var(--color-raise)] ${
        danger ? "text-[var(--color-critical)]" : "text-[var(--color-ink-dim)] hover:text-[var(--color-ink)]"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
