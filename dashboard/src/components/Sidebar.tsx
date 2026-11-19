/**
 * Left navigation.
 *
 * Collapsible to an icon rail: an analyst working the network map wants the canvas, and
 * a fixed 260px of chrome is a real cost on a laptop. Labels survive collapse as
 * `title` attributes and as accessible names, so the rail stays usable rather than
 * becoming a row of guesses.
 */

import { NavLink } from "react-router-dom";
import {
  ChevronLeft,
  CircleGauge,
  FileSearch,
  LifeBuoy,
  Network,
  ScrollText,
  Settings,
  ShieldAlert,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { GraphGuardMark } from "./Logo";

interface Item {
  to: string;
  label: string;
  Icon: LucideIcon;
  badge?: number;
}

export function Sidebar({
  collapsed,
  onToggle,
  openAlerts,
}: {
  collapsed: boolean;
  onToggle: () => void;
  openAlerts: number;
}) {
  const main: Item[] = [
    { to: "/", label: "Overview", Icon: CircleGauge },
    { to: "/alerts", label: "Triage Queue", Icon: ShieldAlert, badge: openAlerts },
    { to: "/network", label: "Network Map", Icon: Network },
    { to: "/chains", label: "Attack Chains", Icon: Waypoints },
    { to: "/runs", label: "Detection Runs", Icon: ScrollText },
    { to: "/evidence", label: "Investigations", Icon: FileSearch },
  ];
  const preference: Item[] = [
    { to: "/settings", label: "Settings", Icon: Settings },
    { to: "/help", label: "Help Center", Icon: LifeBuoy },
  ];

  return (
    <aside
      className={`panel-brand sidebar-shell sticky top-0 flex h-dvh shrink-0 flex-col border-y-0 border-l-0 transition-[width] duration-200 ${
        collapsed ? "w-[76px]" : "w-[260px]"
      }`}
    >
      {/*
        * Collapsed, the rail is 76px wide and `px-4` leaves 44px of content box — but a row
        * of [36px mark][12px gap][28px toggle] needs 76px, so the toggle used to overflow
        * the rail and get sliced in half by its edge. Stacking the two when collapsed is
        * what actually fits; shrinking either control instead would make the toggle a
        * sub-24px hit target.
        */}
      <div
        className={`flex px-4 py-5 ${
          collapsed ? "flex-col items-center gap-3" : "items-center gap-3"
        }`}
      >
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-[var(--color-accent)] text-[var(--color-on-accent)]">
          <GraphGuardMark size={18} />
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold tracking-tight">GraphGuard</div>
            <div className="truncate text-[10px] tracking-wide text-[var(--color-ink-faint)] uppercase">
              Graph Security Analytics
            </div>
          </div>
        )}
        <button
          onClick={onToggle}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          className="grid size-7 shrink-0 place-items-center rounded-control bg-[var(--color-raise)] text-[var(--color-ink-dim)] ring-1 ring-[var(--color-hairline)] transition hover:bg-[var(--color-raise-strong)] hover:text-[var(--color-ink)]"
        >
          <ChevronLeft size={14} className={collapsed ? "rotate-180" : ""} />
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        <Group label="Menu" collapsed={collapsed} items={main} />
        <Group label="Preference" collapsed={collapsed} items={preference} className="mt-6" />
      </nav>
    </aside>
  );
}

function Group({
  label,
  items,
  collapsed,
  className = "",
}: {
  label: string;
  items: Item[];
  collapsed: boolean;
  className?: string;
}) {
  return (
    <div className={className}>
      {!collapsed && (
        <div className="px-3 pb-2 text-[10px] font-semibold tracking-[0.14em] text-[var(--color-ink-faint)] uppercase">
          {label}
        </div>
      )}
      <ul className="space-y-1">
        {items.map(({ to, label: text, Icon, badge }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/"}
              title={collapsed ? text : undefined}
              className={({ isActive }) =>
                [
                  "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                  collapsed ? "justify-center" : "",
                  isActive
                    ? "bg-[var(--color-accent)]/14 font-medium text-[var(--color-accent)] ring-1 ring-[var(--color-accent)]/30"
                    : "text-[var(--color-ink-dim)] hover:bg-[var(--color-raise)] hover:text-[var(--color-ink)]",
                ].join(" ")
              }
            >
              {({ isActive }) => (
                <>
                  {/* Active state carries a rail marker as well as colour, so it is
                      identifiable without relying on hue. */}
                  {isActive && !collapsed && (
                    <span className="absolute top-1/2 left-0 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[var(--color-accent)]" />
                  )}
                  <Icon size={18} strokeWidth={2} className="shrink-0" />
                  {!collapsed && <span className="flex-1 truncate">{text}</span>}
                  {!collapsed && badge !== undefined && badge > 0 && (
                    <span className="rounded-full bg-[var(--color-critical)]/15 px-2 py-0.5 text-[10px] font-semibold text-[var(--color-critical)] tabular-nums">
                      {badge > 99 ? "99+" : badge}
                    </span>
                  )}
                  {collapsed && badge !== undefined && badge > 0 && (
                    <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-[var(--color-critical)]" />
                  )}
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}
