/**
 * The landing page's floating navigation.
 *
 * Behaviour is modelled on agenticbeaver.com, whose bar does four things at once past a
 * 10 px scroll: the header's top padding grows, the bar's own max width *contracts*
 * (1240 → 800), it gains a glass fill, and its internal paddings tighten. That
 * contraction is the "extend and retract" — the bar reads as a full-width header at rest
 * and as a compact floating pill once you are reading.
 *
 * Two implementation notes, because the reference is a Next.js app and this is not:
 *
 *  - The reference animates the max width and the active-tab pill with framer-motion.
 *    Adding a 40 kB animation runtime to this bundle for two effects is not a trade worth
 *    making, so the width is a plain CSS `max-width` transition and the pill is measured
 *    from the DOM and translated. The measurement has to keep running *during* the
 *    300 ms padding transition, or the pill lands where the link used to be — hence the
 *    short rAF loop in `useLayoutEffect` rather than a single measurement.
 *
 *  - Smooth scrolling is `scroll-behavior: smooth` on the document plus `scroll-margin-top`
 *    on the sections (see styles.css). The reference uses the same thing; no scroll
 *    hijacking library is involved, which also means it degrades correctly under
 *    `prefers-reduced-motion`.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChartColumn, House, LayoutGrid, Menu, Radar, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { GraphGuardLogo } from "../Logo";

interface NavItem {
  label: string;
  href: string;
  /** The id of the section this tab tracks, or null for a link that leaves the page. */
  section: string | null;
  Icon: LucideIcon;
}

const ITEMS: NavItem[] = [
  { label: "Home", href: "#home", section: "home", Icon: House },
  { label: "Platform", href: "#platform", section: "platform", Icon: LayoutGrid },
  { label: "Detection", href: "#detection", section: "detection", Icon: Radar },
  { label: "Results", href: "#results", section: "results", Icon: ChartColumn },
];

/** Checked in document order; the first one crossing the probe line wins. */
const SPY_SECTIONS = ["results", "detection", "platform", "home"];

/** Matches the reference's easing and duration so every property lands together. */
const EASE = "cubic-bezier(0.25,0.1,0.25,1)";

export function LandingNav({ onSignIn }: { onSignIn: () => void }) {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("home");
  /** Hovering a tab moves the pill to it, and releasing returns it — as the reference does. */
  const [hovered, setHovered] = useState<string | null>(null);

  const listRef = useRef<HTMLUListElement | null>(null);
  const pillRef = useRef<HTMLSpanElement | null>(null);
  const itemRefs = useRef(new Map<string, HTMLElement>());

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 10);
      for (const id of SPY_SECTIONS) {
        const element = document.getElementById(id);
        if (!element) continue;
        const box = element.getBoundingClientRect();
        if (box.top <= 150 && box.bottom >= 150) {
          setActiveSection(id);
          return;
        }
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const activeLabel =
    hovered ?? ITEMS.find((item) => item.section === activeSection)?.label ?? "Home";
  const activeIndex = ITEMS.findIndex((item) => item.label === activeLabel);

  const positionPill = useCallback(() => {
    const list = listRef.current;
    const pill = pillRef.current;
    const target = itemRefs.current.get(activeLabel);
    if (!list || !pill || !target) return;
    const listBox = list.getBoundingClientRect();
    const targetBox = target.getBoundingClientRect();
    pill.style.transform = `translateX(${targetBox.left - listBox.left}px)`;
    pill.style.width = `${targetBox.width}px`;
    pill.style.height = `${targetBox.height}px`;
    pill.style.opacity = "1";
  }, [activeLabel]);

  // The link paddings animate for 300 ms when `scrolled` flips, so a single measurement
  // would pin the pill to the pre-transition geometry. Track for the length of the
  // transition, then stop — this is not a permanent rAF loop.
  useLayoutEffect(() => {
    positionPill();
    let frame = 0;
    const start = performance.now();
    const track = (now: number) => {
      positionPill();
      if (now - start < 380) frame = requestAnimationFrame(track);
    };
    frame = requestAnimationFrame(track);
    return () => cancelAnimationFrame(frame);
  }, [positionPill, scrolled]);

  useEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(positionPill);
    observer.observe(list);
    return () => observer.disconnect();
  }, [positionPill]);

  return (
    <header
      className="fixed top-0 left-0 z-50 w-full px-4 md:px-8"
      style={{
        paddingTop: scrolled ? "1.5rem" : "1rem",
        transition: `padding 300ms ${EASE}`,
      }}
    >
      {/* At rest the bar is transparent and sits directly on the hero's rays. Only on a
          narrow viewport does the bright end of that gradient reach the bar's controls,
          dropping the wordmark and the menu button under 2:1 — below the 3:1 a control
          needs. So the scrim is scoped to those widths: on desktop the same overlay dulls
          the top-right ray, which is the page's signature, to fix contrast that is already
          fine there. Once scrolled, the glass fill takes over and it fades out. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-32 lg:hidden"
        style={{
          background: "linear-gradient(to bottom, rgb(8 19 40 / 0.72), transparent)",
          opacity: scrolled ? 0 : 1,
          transition: `opacity 300ms ${EASE}`,
        }}
      />
      <div
        className="mx-auto w-full"
        style={{ maxWidth: scrolled ? 800 : 1240, transition: `max-width 300ms ${EASE}` }}
      >
        <nav
          className="flex h-16 items-center justify-between rounded-full border lg:grid lg:grid-cols-[1fr_auto_1fr]"
          style={{
            transition: `background-color 300ms ${EASE}, border-color 300ms ${EASE}, box-shadow 300ms ${EASE}, padding 300ms ${EASE}, backdrop-filter 300ms ${EASE}`,
            paddingInline: scrolled ? "0.5rem" : "0.75rem",
            borderColor: scrolled ? "rgb(255 255 255 / 0.12)" : "transparent",
            backgroundColor: scrolled ? "rgb(11 22 41 / 0.62)" : "transparent",
            boxShadow: scrolled
              ? "0 8px 32px -12px rgb(3 8 18 / 0.7), inset 0 1px 0 rgb(255 255 255 / 0.09)"
              : "none",
            backdropFilter: scrolled ? "blur(18px) saturate(150%)" : "none",
            WebkitBackdropFilter: scrolled ? "blur(18px) saturate(150%)" : "none",
          }}
        >
          <a
            href="#home"
            aria-label="GraphGuard"
            className="shrink-0 justify-self-start pl-1 text-[color:var(--landing-ink)]"
          >
            <GraphGuardLogo size={26} />
          </a>

          <ul
            ref={listRef}
            className="relative hidden items-center lg:flex"
            onMouseLeave={() => setHovered(null)}
          >
            {/* One shared pill, translated between tabs — the vanilla stand-in for the
                reference's shared-layout animation. */}
            <span
              ref={pillRef}
              aria-hidden="true"
              className="pointer-events-none absolute top-0 left-0 rounded-full opacity-0"
              style={{
                background: "rgb(255 255 255 / 0.1)",
                boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.14)",
                transition: `transform 420ms ${EASE}, width 420ms ${EASE}, height 300ms ${EASE}, opacity 200ms linear`,
              }}
            />
            {ITEMS.map((item, index) => {
              const isActive = activeLabel === item.label;
              return (
                <li key={item.label} className="flex items-center">
                  {/* The divider next to the pill would be swallowed by it, so the two
                      neighbours of the active tab hide theirs. */}
                  <span
                    aria-hidden="true"
                    className="h-4 w-px transition-opacity duration-200"
                    style={{
                      background: "rgb(255 255 255 / 0.14)",
                      opacity:
                        index > 0 && index !== activeIndex && index - 1 !== activeIndex ? 1 : 0,
                    }}
                  />
                  <a
                    href={item.href}
                    ref={(node) => {
                      if (node) itemRefs.current.set(item.label, node);
                      else itemRefs.current.delete(item.label);
                    }}
                    onMouseEnter={() => setHovered(item.label)}
                    aria-current={
                      activeSection === item.section && hovered === null ? "page" : undefined
                    }
                    className="relative z-10 flex items-center gap-2 rounded-full text-[13.5px] font-semibold tracking-tight whitespace-nowrap active:scale-[0.97]"
                    style={{
                      padding: scrolled ? "0.375rem 0.75rem" : "0.5rem 0.875rem",
                      color: isActive ? "var(--landing-ink)" : "var(--landing-ink-dim)",
                      transition: `color 200ms ${EASE}, padding 300ms ${EASE}, transform 200ms ${EASE}`,
                    }}
                  >
                    <item.Icon
                      className="h-4 w-4 transition-colors duration-200"
                      strokeWidth={2.25}
                      style={{ color: isActive ? "var(--landing-accent)" : "inherit" }}
                    />
                    {item.label}
                  </a>
                </li>
              );
            })}
          </ul>

          <div className="hidden lg:flex lg:items-center lg:justify-self-end">
            <button
              type="button"
              onClick={onSignIn}
              className="rounded-full text-[15px] font-semibold text-white active:scale-[0.97]"
              style={{
                padding: scrolled ? "0.5rem 1rem" : "0.75rem 1.5rem",
                background: "linear-gradient(90deg,#2a63e4 0%,#0853e7 100%)",
                boxShadow:
                  "inset 0 1px 2px rgb(255 255 255 / 0.3), 0 1px 1px rgb(3 8 18 / 0.4), 0 8px 24px -12px rgb(8 83 231 / 0.9)",
                transition: `padding 300ms ${EASE}, opacity 300ms ${EASE}, transform 200ms ${EASE}`,
              }}
            >
              Log in
            </button>
          </div>

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex items-center justify-center rounded-full p-2 text-[color:var(--landing-ink)] lg:hidden"
            aria-label="Toggle menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
          </button>
        </nav>
      </div>

      {menuOpen && (
        <div
          className="mx-auto mt-2 max-w-[1240px] rounded-2xl border p-4 lg:hidden"
          style={{
            borderColor: "rgb(255 255 255 / 0.12)",
            background: "rgb(11 22 41 / 0.92)",
            backdropFilter: "blur(18px)",
          }}
        >
          <ul className="flex flex-col gap-1">
            {ITEMS.map((item) => (
              <li key={item.label}>
                <a
                  href={item.href}
                  onClick={() => setMenuOpen(false)}
                  className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[15px] font-medium text-[color:var(--landing-ink)] transition-colors hover:bg-white/6"
                >
                  <item.Icon className="h-4 w-4" strokeWidth={2.25} />
                  {item.label}
                </a>
              </li>
            ))}
            <li className="mt-2">
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onSignIn();
                }}
                className="block w-full rounded-full px-5 py-2.5 text-center text-[15px] font-semibold text-white"
                style={{ background: "linear-gradient(90deg,#2a63e4 0%,#0853e7 100%)" }}
              >
                Log in
              </button>
            </li>
          </ul>
        </div>
      )}
    </header>
  );
}
