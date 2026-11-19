/**
 * The public landing page, shown to anyone who is not signed in.
 *
 * It sits in front of the sign-in form rather than replacing it: `/` is the pitch, `/login`
 * is the door. Everything on it is dark-only and scoped by the `.landing` class in
 * styles.css, because the artwork it is built around (the navy field, the colour-bend
 * shader, the mark) is composed for a dark background.
 *
 * On the numbers: every figure quoted below is a measured result from the evaluation
 * harness at seed 1337 and appears in `docs/assets/figures/`. Where a baseline beats the
 * graph — the flat volume rule on the loud stolen-key scenario — the page says so. A
 * security product that only quotes its wins is the exact thing this project argues
 * against, and the defence is easier when the marketing page and the thesis agree.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Boxes,
  Fingerprint,
  GitBranch,
  Radar,
  Route,
  ScanSearch,
  ShieldCheck,
  TrendingUp,
  Users,
  Waypoints,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import ColorBends from "../components/backgrounds/ColorBends";
import { GraphGuardLogo, GraphGuardMark } from "../components/Logo";
import { LandingNav } from "../components/landing/LandingNav";

/**
 * Adds `is-visible` to every `.reveal` under the page as it scrolls into view, once.
 * An observer rather than a scroll handler so the work is proportional to the number of
 * revealed elements instead of to the number of scroll events.
 *
 * The at-rest hidden state is gated on `reveal-armed`, which this hook adds, so a visitor
 * whose JavaScript never ran sees the page rather than a blank navy field. The class is
 * added in a layout effect so it lands before the first paint and there is no flash of
 * fully-visible content collapsing into the animation.
 *
 * The timeout is the other half of that guarantee. A working IntersectionObserver always
 * delivers an initial batch; environments where it is suspended (a tab that is never
 * composited, some embedded webviews) deliver nothing at all, and without this the entire
 * page would sit at opacity 0 forever. Seeing no entry within a second is a reliable
 * signal that the observer is not going to report, so the effect gives up and shows
 * everything — a missing animation is a far better failure than a missing page.
 */
function useRevealOnScroll() {
  const rootRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const targets = root.querySelectorAll<HTMLElement>(".reveal");
    const revealAll = () => {
      root.classList.remove("reveal-armed");
      targets.forEach((node) => node.classList.add("is-visible"));
    };

    if (typeof IntersectionObserver === "undefined") {
      revealAll();
      return;
    }

    root.classList.add("reveal-armed");
    let delivered = false;
    const observer = new IntersectionObserver(
      (entries) => {
        delivered = true;
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.08 },
    );

    // Anything already on screen is revealed from its own geometry and never observed.
    // The first screen is what a visitor judges the page by, and it should not be waiting
    // on an observer callback to exist at all.
    const fold = window.innerHeight;
    targets.forEach((node) => {
      if (node.getBoundingClientRect().top < fold) node.classList.add("is-visible");
      else observer.observe(node);
    });

    const bail = window.setTimeout(() => {
      if (!delivered) revealAll();
    }, 1000);

    return () => {
      window.clearTimeout(bail);
      observer.disconnect();
    };
  }, []);

  return rootRef;
}

/**
 * Ramps 0 -> 1 once the returned ref's element is scrolled into view, then stops.
 *
 * Drives both the bar widths and the counting numbers in the log-vs-graph chart from one
 * clock, so a bar and its label can never disagree mid-flight.
 *
 * Three guarantees, all of which exist because a chart stuck at zero is worse than a chart
 * that never animated: it runs immediately if the element is already on screen at mount, it
 * gives up and jumps to the final values if the observer produces nothing within a second
 * (some embedded webviews never deliver), and it skips straight to the end under
 * `prefers-reduced-motion` rather than animating quickly.
 */
function useCountUpOnView<T extends HTMLElement>(durationMs = 1100) {
  const ref = useRef<T | null>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    let frame = 0;
    let started = false;
    let delivered = false;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    const run = () => {
      if (started) return;
      started = true;
      if (reduce) {
        setProgress(1);
        return;
      }
      const t0 = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - t0) / durationMs);
        setProgress(1 - Math.pow(1 - t, 3)); // ease-out cubic: quick off the mark, soft landing
        if (t < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    };

    if (typeof IntersectionObserver === "undefined") {
      setProgress(1);
      return;
    }
    if (element.getBoundingClientRect().top < window.innerHeight) {
      run();
      return () => cancelAnimationFrame(frame);
    }

    const observer = new IntersectionObserver(
      (entries) => {
        delivered = true;
        if (entries.some((entry) => entry.isIntersecting)) {
          run();
          observer.disconnect();
        }
      },
      { threshold: 0.25 },
    );
    observer.observe(element);
    const bail = window.setTimeout(() => {
      if (delivered) return;
      // Giving up has to be final. Showing the values but leaving the observer attached
      // meant a later intersection still called run(), so the chart drew itself complete
      // and then snapped back to zero to animate again — reproducible with the tab
      // backgrounded, where observer delivery is frame-driven and stalls.
      started = true;
      observer.disconnect();
      setProgress(1);
    }, 1000);

    return () => {
      window.clearTimeout(bail);
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [durationMs]);

  return [ref, progress] as const;
}

/** Average precision, graph detector against the best flat log rule, per scenario. */
const LOG_VS_GRAPH = [
  { name: "Coordinated ring", graph: 1.0, flat: 0.222 },
  { name: "Community drift", graph: 0.622, flat: 0.145 },
  { name: "Cross-layer entity", graph: 1.0, flat: 0.026 },
  { name: "Stolen key", graph: 0.864, flat: 0.875 },
];

function LogVsGraphChart() {
  const [ref, progress] = useCountUpOnView<HTMLDivElement>();
  // Rows start in sequence rather than together, so the eye is led down the comparison
  // instead of being handed four simultaneous animations.
  const STAGGER = 0.09;
  const span = 1 - STAGGER * (LOG_VS_GRAPH.length - 1);

  return (
    <div ref={ref} className="space-y-6">
      {LOG_VS_GRAPH.map((row, index) => {
        const t = Math.max(0, Math.min(1, (progress - index * STAGGER) / span));
        return (
          <div key={row.name}>
            <div className="mb-1.5 text-[13px]" style={{ color: "var(--landing-ink)" }}>
              {row.name}
            </div>
            <div className="flex flex-col gap-1">
              {(
                [
                  [SERIES[0], row.graph],
                  [SERIES[1], row.flat],
                ] as const
              ).map(([series, value]) => {
                // The stolen-key row separates 0.864 from 0.875 — about one percent of the
                // track, which no bar length can communicate. The leading value is
                // emphasised so the comparison is readable as text. It is decided from the
                // final numbers, not the animated ones, or the emphasis would flicker
                // between the two bars while they grow.
                const leads = value === Math.max(row.graph, row.flat);
                return (
                  <div key={series.label} className="flex items-center gap-2.5">
                    {/* The track gives every row the same 0–1 baseline, so a short bar
                        reads as a low score rather than as a rendering artefact. */}
                    <div
                      className="h-2 flex-1 overflow-hidden rounded-full"
                      style={{ background: "rgb(255 255 255 / 0.06)" }}
                    >
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${value * t * 100}%`, background: series.colour }}
                      />
                    </div>
                    <span
                      className="w-11 shrink-0 text-right font-mono text-[11.5px] tabular-nums"
                      style={{
                        color: leads ? "var(--landing-ink)" : "var(--landing-ink-faint)",
                        fontWeight: leads ? 600 : 400,
                      }}
                    >
                      {(value * t).toFixed(3)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] font-semibold tracking-[0.14em] uppercase"
      style={{
        color: "var(--landing-accent)",
        background: "rgb(111 155 255 / 0.1)",
        border: "1px solid rgb(111 155 255 / 0.22)",
      }}
    >
      {children}
    </span>
  );
}

function GlassCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl p-6 ${className}`}
      style={{
        background: "var(--landing-glass)",
        border: "1px solid var(--landing-line)",
        boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.06)",
      }}
    >
      {children}
    </div>
  );
}

function DetectorCard({
  Icon,
  name,
  catches,
  body,
  metric,
}: {
  Icon: LucideIcon;
  name: string;
  catches: string;
  body: string;
  metric: string;
}) {
  return (
    <GlassCard className="reveal flex h-full flex-col">
      <div
        className="mb-4 grid size-11 place-items-center rounded-xl"
        style={{ background: "rgb(111 155 255 / 0.12)", color: "var(--landing-accent)" }}
      >
        <Icon size={20} strokeWidth={2.1} />
      </div>
      <h3 className="text-[17px] font-semibold" style={{ color: "var(--landing-ink)" }}>
        {name}
      </h3>
      <p
        className="mt-1 text-[13px] font-medium"
        style={{ color: "var(--landing-accent)" }}
      >
        {catches}
      </p>
      <p
        className="mt-3 flex-1 text-[14px] leading-relaxed"
        style={{ color: "var(--landing-ink-dim)" }}
      >
        {body}
      </p>
      <p
        className="mt-5 border-t pt-4 font-mono text-[12px]"
        style={{ borderColor: "var(--landing-line)", color: "var(--landing-ink-faint)" }}
      >
        {metric}
      </p>
    </GlassCard>
  );
}

/**
 * The hero figure: a small heterogeneous graph, drawn the way the console's network map
 * draws one — typed nodes, one scored critical, and a cross-layer edge from an address to
 * the identity it reached. Hand-authored SVG rather than a screenshot so it stays sharp
 * and weighs nothing.
 */
function HeroGraph() {
  const nodes: { x: number; y: number; r: number; fill: string; label: string }[] = [
    { x: 78, y: 62, r: 9, fill: "#ff9f43", label: "IP" },
    { x: 168, y: 40, r: 8, fill: "#6f9bff", label: "K" },
    { x: 262, y: 74, r: 13, fill: "#ff5a5f", label: "K" },
    { x: 352, y: 44, r: 8, fill: "#c792ea", label: "M" },
    { x: 372, y: 132, r: 8, fill: "#c792ea", label: "M" },
    { x: 246, y: 168, r: 8, fill: "#4ec9a5", label: "U" },
    { x: 132, y: 152, r: 8, fill: "#57c7ff", label: "E" },
    { x: 60, y: 190, r: 7, fill: "#57c7ff", label: "E" },
  ];
  const edges: [number, number, boolean][] = [
    [0, 1, false],
    [0, 6, false],
    [6, 7, false],
    [1, 2, true],
    [2, 3, true],
    [2, 4, true],
    [2, 5, true],
    [5, 6, false],
    [1, 3, false],
  ];

  return (
    <svg viewBox="0 0 430 220" className="h-full w-full" role="img" aria-label="A heterogeneous telemetry graph with one node scored critical">
      <defs>
        <radialGradient id="gg-hot" cx="50%" cy="50%">
          <stop offset="0%" stopColor="#ff5a5f" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#ff5a5f" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="262" cy="74" r="52" fill="url(#gg-hot)" />
      {edges.map(([a, b, hot], index) => (
        <line
          key={index}
          x1={nodes[a].x}
          y1={nodes[a].y}
          x2={nodes[b].x}
          y2={nodes[b].y}
          stroke={hot ? "#ff5a5f" : "#6f9bff"}
          strokeOpacity={hot ? 0.5 : 0.28}
          strokeWidth={hot ? 1.8 : 1.2}
        />
      ))}
      {nodes.map((node, index) => (
        <g key={index}>
          <circle cx={node.x} cy={node.y} r={node.r} fill={node.fill} fillOpacity="0.92" />
          <text
            x={node.x}
            y={node.y + 3.4}
            textAnchor="middle"
            fontSize={node.r > 10 ? 11 : 9}
            fontWeight="700"
            fill="#081328"
            fontFamily="Inter, sans-serif"
          >
            {node.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

/** Fixed series identity for the log-vs-graph chart: assigned once, never reassigned. */
const SERIES = [
  { label: "Graph detector", colour: "#6f9bff" },
  { label: "Best flat log rule", colour: "#ff9f43" },
] as const;

const PIPELINE: { Icon: LucideIcon; title: string; body: string }[] = [
  {
    Icon: Boxes,
    title: "Normalise",
    body: "Pluggable adapters map AI-platform telemetry (OpenTelemetry gen_ai.*) and HTTP gateway access logs onto one canonical trace event. Nothing downstream ever parses a raw log format.",
  },
  {
    Icon: Waypoints,
    title: "Relate",
    body: "Every event becomes a timestamped edge in a single heterogeneous graph — users, API keys, models, addresses, endpoints — with time-windowed snapshots so the structure can be watched changing.",
  },
  {
    Icon: Radar,
    title: "Detect",
    body: "Three unsupervised detectors score the graph: the individual, the group's coordination, and the group's drift. No labels, no rules to hand-write, no model of \"known bad\".",
  },
  {
    Icon: ShieldCheck,
    title: "Explain",
    body: "Each alert arrives with its subgraph, its timeline, a plain-language reason and a MITRE ATLAS / OWASP LLM tag. Scores inform an analyst; nothing is auto-blocked.",
  },
];

export function Landing({ onSignIn }: { onSignIn: () => void }) {
  const rootRef = useRevealOnScroll();

  /*
   * The landing is dark in both themes, but `body` keeps painting the themed canvas — so
   * under the light theme the macOS overscroll bounce, and every UA-drawn surface
   * (scrollbars, form controls), stayed light against a navy page. Flagging the document
   * while this route is mounted lets styles.css override both, and the flag is removed on
   * unmount so the console goes back to honouring the analyst's theme.
   */
  useLayoutEffect(() => {
    document.documentElement.dataset.landing = "true";
    return () => {
      delete document.documentElement.dataset.landing;
    };
  }, []);

  return (
    <div ref={rootRef} className="landing min-h-dvh overflow-x-hidden">
      <LandingNav onSignIn={onSignIn} />

      {/* ---------------------------------------------------------------- hero */}
      <section id="home" className="relative isolate overflow-hidden">
        {/*
          * Decorative, behind everything, with the page's own navy showing through the
          * shader's alpha.
          *
          * The numeric props are the ones React Bits runs on its own hero — rotation 90,
          * speed 0.2, frequency 1, noise 0.15, iterations 1 — with two exceptions.
          *
          * Intensity is 0.9 rather than their 1.3. Their hero is a two-column layout whose
          * headline sits in a quiet corner while the beams sweep the empty half; this one
          * is centred, so the copy sits directly on the brightest part of the field and
          * 1.3 put "See Threats." under the 3:1 floor on 2% of its glyph pixels.
          *
          * The second exception: Their editor reads `bandWidth={0.14}`, which renders as a flat wash
          * on the published component: `w = 1 - exp(-bw / exp(bw * m))` spans only
          * 0.08–0.13 at that value, so the bands have no contrast left to show. Their hero
          * runs a newer build that scales the prop differently (note 1/0.14 ≈ 7, close to
          * the registry default). On the published parameterisation, 6 is what reproduces
          * the reference's structure, so 6 is what this uses.
          *
          * Only the colour changes otherwise: their single #A855F7 purple becomes this
          * product's own #2f6bf0, the blue the primary buttons start on, so the field reads
          * as GraphGuard rather than as someone else's brand.
          *
          * One colour, not a ramp, and that is not a stylistic preference. The shader
          * accumulates stops additively — `sumCol += uColors[i] * w` — so a three-stop ramp
          * renders roughly three times as bright as the reference and clips its band cores
          * to white. Measured, that pushed "Map Risk." to 3.5:1 median with half its pixels
          * under the 3:1 floor for large text. A single stop keeps the luminance profile on
          * top of the reference's and the headline legible.
          *
          * Their hero also fades the field out at the top so it does not fight the nav.
          * That is a `fadeTop` prop on the newer build of the component, not on the
          * published registry version, so it is a CSS mask here — same result, and it keeps
          * the shader itself untouched and diffable against upstream.
          */}
        <div
          className="pointer-events-none absolute inset-0 -z-10"
          aria-hidden="true"
          style={{
            maskImage: "linear-gradient(to bottom, transparent 0%, #000 22%, #000 100%)",
            WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, #000 22%, #000 100%)",
          }}
        >
          <ColorBends
            colors={["#2f6bf0"]}
            rotation={90}
            speed={0.2}
            frequency={1.0}
            noise={0.15}
            bandWidth={6}
            iterations={1}
            intensity={0.9}
          />
        </div>
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-64"
          aria-hidden="true"
          style={{ background: "linear-gradient(to bottom, transparent, var(--landing-canvas))" }}
        />
        {/*
          * A scrim under the copy, not a dimmer on the artwork.
          *
          * The beams sweep the whole frame, so wherever a bright core crosses a glyph the
          * local contrast collapses regardless of how the type is coloured — measured, the
          * accent-blue "Map Risk." fell to 1.6:1 on its worst pixels. The alternatives were
          * to lighten that blue or to fade the effect globally, and both are worse: the
          * blue is the poster's, and fading everything throws away the thing this section
          * exists to show. Darkening only the ellipse the text occupies keeps the palette
          * exact, keeps the corners at full brightness, and buys the contrast back where it
          * is actually needed.
          */}
        <div
          className="pointer-events-none absolute inset-0 -z-10"
          aria-hidden="true"
          style={{
            background:
              "radial-gradient(62% 44% at 50% 44%, rgb(6 15 33 / 0.86) 0%, rgb(6 15 33 / 0.62) 42%, rgb(6 15 33 / 0) 76%)",
          }}
        />

        <div className="mx-auto max-w-5xl px-6 pt-40 pb-16 text-center md:pt-48 md:pb-24">
          <div className="reveal is-visible mb-10 flex justify-center">
            <GraphGuardLogo size={34} />
          </div>

          <h1
            className="mx-auto max-w-3xl text-[clamp(2.6rem,7.4vw,4.6rem)] leading-[1.03] font-bold tracking-[-0.03em] text-balance"
            style={{ color: "var(--landing-ink)" }}
          >
            See Threats.
            <br />
            <span style={{ color: "#6f9bff" }}>Map Risk.</span>
            <br />
            Stop Drift.
          </h1>

          <p
            className="mx-auto mt-7 max-w-xl text-[17px] leading-relaxed"
            style={{ color: "rgb(238 243 251 / 0.86)" }}
          >
            Graph-based security monitoring for modern systems and anomalous behaviour
            detection.
          </p>

          <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              onClick={onSignIn}
              className="inline-flex items-center gap-2 rounded-full px-7 py-3.5 text-[15px] font-semibold text-white transition-transform active:scale-[0.97]"
              style={{
                background: "linear-gradient(90deg,#2a63e4 0%,#0853e7 100%)",
                boxShadow:
                  "inset 0 1px 2px rgb(255 255 255 / 0.3), 0 12px 32px -14px rgb(8 83 231 / 0.95)",
              }}
            >
              Open the console
              <ArrowRight size={17} strokeWidth={2.3} />
            </button>
            <a
              href="#detection"
              className="inline-flex items-center gap-2 rounded-full px-7 py-3.5 text-[15px] font-semibold transition-colors"
              style={{
                color: "var(--landing-ink)",
                border: "1px solid var(--landing-line)",
                background: "var(--landing-glass)",
              }}
            >
              How the detection works
            </a>
          </div>

          {/* The claim under the headline, stated once and stated honestly. */}
          <div
            className="reveal mx-auto mt-20 max-w-4xl overflow-hidden rounded-3xl"
            style={{
              background: "rgb(255 255 255 / 0.035)",
              border: "1px solid var(--landing-line)",
              boxShadow:
                "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 40px 80px -40px rgb(3 8 18 / 0.9)",
            }}
          >
            <div
              className="flex items-center gap-2 border-b px-5 py-3"
              style={{ borderColor: "var(--landing-line)" }}
            >
              <span className="size-2.5 rounded-full" style={{ background: "#ff5a5f" }} />
              <span className="size-2.5 rounded-full" style={{ background: "#ffd93d" }} />
              <span className="size-2.5 rounded-full" style={{ background: "#2fd6a5" }} />
              <span
                className="ml-3 hidden truncate font-mono text-[11px] sm:inline"
                style={{ color: "var(--landing-ink-faint)" }}
              >
                run 0f31c2 · 5 detectors · 1 attack chain
              </span>
            </div>
            <div className="grid min-w-0 gap-6 p-5 text-left sm:p-6 md:grid-cols-[1.35fr_1fr]">
              <div className="min-w-0 aspect-[430/220]">
                <HeroGraph />
              </div>
              <div className="flex min-w-0 flex-col justify-center gap-3">
                {[
                  { severity: "#ff5a5f", title: "api_key/ak_7f2 diverged from its community", meta: "node-outlier · 0.93 · ATLAS TA0004" },
                  { severity: "#ff9f43", title: "19 accounts calling one model in unison", meta: "abnormal-subgraph · 1.00 · LLM04" },
                  { severity: "#ffd93d", title: "app-dev cluster shape shifted overnight", meta: "community-drift · 0.87 · ATLAS TA0043" },
                ].map((alert) => (
                  <div
                    key={alert.title}
                    className="rounded-xl px-4 py-3"
                    style={{
                      background: "rgb(255 255 255 / 0.04)",
                      border: "1px solid var(--landing-line)",
                    }}
                  >
                    <div className="flex items-start gap-2.5">
                      <span
                        className="mt-1.5 size-2 shrink-0 rounded-full"
                        style={{ background: alert.severity }}
                      />
                      <div className="min-w-0">
                        <div
                          className="truncate text-[13px] font-medium"
                          style={{ color: "var(--landing-ink)" }}
                        >
                          {alert.title}
                        </div>
                        <div
                          className="mt-0.5 truncate font-mono text-[11px]"
                          style={{ color: "var(--landing-ink-faint)" }}
                        >
                          {alert.meta}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------ platform */}
      <section id="platform" className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="reveal mx-auto max-w-2xl text-center">
          <Eyebrow>The platform</Eyebrow>
          <h2
            className="mt-5 text-[clamp(1.9rem,4vw,2.75rem)] leading-tight font-bold tracking-[-0.025em] text-balance"
            style={{ color: "var(--landing-ink)" }}
          >
            A log line is a fact. A graph is a story.
          </h2>
          <p className="mt-5 text-[16px] leading-relaxed" style={{ color: "var(--landing-ink-dim)" }}>
            Flat inspection evaluates events in isolation and resets every session, so it
            cannot express “this account stopped behaving like itself” or “these twenty
            accounts move in unison”. GraphGuard makes the relationships first-class, then
            scores them.
          </p>
        </div>

        <div className="mt-12 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map((step, index) => (
            <GlassCard key={step.title} className="reveal h-full">
              <div className="mb-4 flex items-center justify-between">
                <div
                  className="grid size-10 place-items-center rounded-xl"
                  style={{
                    background: "rgb(111 155 255 / 0.12)",
                    color: "var(--landing-accent)",
                  }}
                >
                  <step.Icon size={19} strokeWidth={2.1} />
                </div>
                <span className="font-mono text-[11px]" style={{ color: "#6f9bff" }}>
                  0{index + 1}
                </span>
              </div>
              <h3 className="text-[16px] font-semibold" style={{ color: "var(--landing-ink)" }}>
                {step.title}
              </h3>
              <p
                className="mt-2 text-[13.5px] leading-relaxed"
                style={{ color: "var(--landing-ink-dim)" }}
              >
                {step.body}
              </p>
            </GlassCard>
          ))}
        </div>
      </section>

      {/* ----------------------------------------------------------- detection */}
      <section id="detection" className="relative py-16 md:py-24">
        <div
          className="pointer-events-none absolute inset-0 -z-10"
          aria-hidden="true"
          style={{
            background:
              "radial-gradient(70% 55% at 50% 0%, rgb(8 83 231 / 0.16) 0%, transparent 70%)",
          }}
        />
        <div className="mx-auto max-w-6xl px-6">
          <div className="reveal mx-auto max-w-2xl text-center">
            <Eyebrow>Detection</Eyebrow>
            <h2
              className="mt-5 text-[clamp(1.9rem,4vw,2.75rem)] leading-tight font-bold tracking-[-0.025em] text-balance"
              style={{ color: "var(--landing-ink)" }}
            >
              Three detectors, three shapes of abuse
            </h2>
            <p
              className="mt-5 text-[16px] leading-relaxed"
              style={{ color: "var(--landing-ink-dim)" }}
            >
              Each one leads on the mode it targets, and each is written once and runs on
              any telemetry layer the adapters feed in.
            </p>
          </div>

          <div className="mt-12 grid gap-4 md:grid-cols-3">
            <DetectorCard
              Icon={Fingerprint}
              name="Node outlier"
              catches="The impostor"
              body="Scores how far one node has drifted from its peers and from its own past. A stolen API key does not change its name — it changes its hours, its models and its volume."
              metric="ROC-AUC 0.930 · stolen key"
            />
            <DetectorCard
              Icon={Users}
              name="Abnormal subgraph"
              catches="The coordinated ring"
              body="Finds a small cluster acting in suspicious unison — freshly created accounts hitting one model in sync. No member looks odd alone; the coordination is the signal."
              metric="ROC-AUC 1.000 · coordinated ring"
            />
            <DetectorCard
              Icon={TrendingUp}
              name="Community drift"
              catches="The slow shift"
              body="Watches the shape of a behavioural cluster change across snapshots. Catches gradual compromise and misconfiguration, where no single account ever looks anomalous."
              metric="ROC-AUC 0.867 · community drift"
            />
          </div>

          {/* The cross-layer story — the feature that only exists because it is one graph. */}
          <GlassCard className="reveal mt-4">
            <div className="grid items-start gap-6 md:grid-cols-[auto_1fr]">
              <div
                className="grid size-12 place-items-center rounded-xl"
                style={{ background: "rgb(255 90 95 / 0.12)", color: "#ff8a8e" }}
              >
                <Route size={22} strokeWidth={2.1} />
              </div>
              <div>
                <h3
                  className="text-[17px] font-semibold"
                  style={{ color: "var(--landing-ink)" }}
                >
                  Cross-layer attack chains
                </h3>
                <p
                  className="mt-2 max-w-3xl text-[14px] leading-relaxed"
                  style={{ color: "var(--landing-ink-dim)" }}
                >
                  An address hammers the auth endpoint. Minutes later a valid key signs in
                  from that same address, then reaches for models it has never touched.
                  Each event alone is a shrug; joined on the shared address identity they
                  are one chain, external breach to internal abuse. In the reference run:
                  one chain found, both endpoints correct, zero false chains — and the
                  brute-force-without-breach control correctly produces none.
                </p>
              </div>
            </div>
          </GlassCard>
        </div>
      </section>

      {/* ------------------------------------------------------------- results */}
      <section id="results" className="mx-auto max-w-6xl px-6 py-16 md:py-24">
        <div className="reveal mx-auto max-w-2xl text-center">
          <Eyebrow>Measured</Eyebrow>
          <h2
            className="mt-5 text-[clamp(1.9rem,4vw,2.75rem)] leading-tight font-bold tracking-[-0.025em] text-balance"
            style={{ color: "var(--landing-ink)" }}
          >
            The graph holds when the attacker gets quiet
          </h2>
          <p className="mt-5 text-[16px] leading-relaxed" style={{ color: "var(--landing-ink-dim)" }}>
            A flat volume rule is competitive against a loud attacker. Turn the volume
            down and it collapses; the graph detector barely moves. That crossover is the
            whole argument.
          </p>
        </div>

        <div className="mt-12 grid gap-4 md:grid-cols-3">
          {[
            {
              value: "0.874 → 0.124",
              label: "Flat volume rule, average precision, as the attacker quietens",
              tone: "#ff8a8e",
            },
            {
              value: "0.708 → 0.738",
              label: "Graph detector across the same loudness sweep — it holds",
              tone: "#2fd6a5",
            },
            {
              value: "4× – 8×",
              label: "Where the two cross. Below it, only the graph is still looking",
              tone: "#6f9bff",
            },
          ].map((stat) => (
            <GlassCard key={stat.value} className="reveal">
              <div
                className="font-mono text-[26px] font-semibold tracking-tight"
                style={{ color: stat.tone }}
              >
                {stat.value}
              </div>
              <p
                className="mt-2.5 text-[13.5px] leading-relaxed"
                style={{ color: "var(--landing-ink-dim)" }}
              >
                {stat.label}
              </p>
            </GlassCard>
          ))}
        </div>

        <div className="mt-4 grid items-start gap-4 lg:grid-cols-[1.4fr_1fr]">
          <GlassCard className="reveal">
            <div className="mb-5 flex items-center gap-2.5">
              <ScanSearch size={17} style={{ color: "var(--landing-accent)" }} />
              <h3 className="text-[15px] font-semibold" style={{ color: "var(--landing-ink)" }}>
                Invisible in the logs, obvious in the graph
              </h3>
            </div>
            <p className="mb-5 text-[13px]" style={{ color: "var(--landing-ink-dim)" }}>
              Average precision per scenario. Higher is better; 1.000 is perfect ranking.
            </p>
            {/* Each series keeps ONE colour in every row. An earlier pass tinted the bars by
                which one won, which meant the reader had to decode the palette per row and
                the losing blue looked like a third series — a chart must never repaint a
                series by its outcome. Each bar is also labelled with its own value, so the
                comparison survives without colour. */}
            <div className="mb-5 flex items-center gap-5">
              {SERIES.map((series) => (
                <span key={series.label} className="flex items-center gap-2">
                  <span
                    className="h-2 w-4 rounded-full"
                    style={{ background: series.colour }}
                  />
                  <span className="text-[12px]" style={{ color: "var(--landing-ink-dim)" }}>
                    {series.label}
                  </span>
                </span>
              ))}
            </div>
            <LogVsGraphChart />
            <p
              className="mt-6 border-t pt-4 text-[12.5px] leading-relaxed"
              style={{ borderColor: "var(--landing-line)", color: "var(--landing-ink-faint)" }}
            >
              The flat rule narrowly wins the stolen-key row (0.875 to 0.864). That cell is
              conceded rather than tuned away — the loudness sweep above is what answers it.
            </p>
          </GlassCard>

          <GlassCard className="reveal">
            <div>
              <div className="mb-5 flex items-center gap-2.5">
                <GitBranch size={17} style={{ color: "var(--landing-accent)" }} />
                <h3
                  className="text-[15px] font-semibold"
                  style={{ color: "var(--landing-ink)" }}
                >
                  What is actually claimed
                </h3>
              </div>
              <ul className="space-y-3.5">
                {[
                  "Validated on synthetic telemetry with known ground truth, at a fixed seed, reproducible from the CLI with no database.",
                  "Unsupervised throughout — no labelled attack data is required to run a detector.",
                  "Known limitations are reported, not tuned away: baseline erosion under a persistent attacker, drift that legitimate change can imitate, and unison detection an attacker can desynchronise.",
                  "Scores inform an analyst. Nothing is blocked automatically; a legitimate power user scores high too.",
                ].map((line) => (
                  <li key={line} className="flex gap-3">
                    <span
                      className="mt-1.5 size-1.5 shrink-0 rounded-full"
                      style={{ background: "var(--landing-accent)" }}
                    />
                    <span
                      className="text-[13.5px] leading-relaxed"
                      style={{ color: "var(--landing-ink-dim)" }}
                    >
                      {line}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </GlassCard>
        </div>
      </section>

      {/* ----------------------------------------------------------------- cta */}
      <section className="relative overflow-hidden px-6 pb-24">
        <div
          className="reveal mx-auto max-w-4xl rounded-3xl px-8 py-16 text-center"
          style={{
            background:
              "radial-gradient(120% 140% at 50% -20%, rgb(8 83 231 / 0.35) 0%, rgb(255 255 255 / 0.03) 60%)",
            border: "1px solid var(--landing-line)",
            boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.08)",
          }}
        >
          <div className="mb-6 flex justify-center">
            <GraphGuardMark size={46} />
          </div>
          <h2
            className="mx-auto max-w-xl text-[clamp(1.7rem,3.6vw,2.4rem)] leading-tight font-bold tracking-[-0.025em] text-balance"
            style={{ color: "var(--landing-ink)" }}
          >
            The alerts are already ranked and explained.
          </h2>
          <p
            className="mx-auto mt-4 max-w-md text-[15px] leading-relaxed"
            style={{ color: "var(--landing-ink-dim)" }}
          >
            Sign in to the detection console — triage queue, live network map, attack
            chains and every saved run.
          </p>
          <button
            type="button"
            onClick={onSignIn}
            className="mt-8 inline-flex items-center gap-2 rounded-full px-7 py-3.5 text-[15px] font-semibold text-white transition-transform active:scale-[0.97]"
            style={{
              background: "linear-gradient(90deg,#2a63e4 0%,#0853e7 100%)",
              boxShadow:
                "inset 0 1px 2px rgb(255 255 255 / 0.3), 0 12px 32px -14px rgb(8 83 231 / 0.95)",
            }}
          >
            Log in
            <ArrowRight size={17} strokeWidth={2.3} />
          </button>
          <p className="mt-5 text-[12.5px]" style={{ color: "var(--landing-ink-faint)" }}>
            Accounts are provisioned by an administrator. There is no self-service
            registration.
          </p>
        </div>
      </section>

      {/* -------------------------------------------------------------- footer */}
      <footer
        className="border-t px-6 py-10"
        style={{ borderColor: "var(--landing-line)" }}
      >
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-5 md:flex-row">
          <div style={{ color: "var(--landing-ink)" }}>
            <GraphGuardLogo size={22} />
          </div>
          <nav className="flex flex-wrap items-center justify-center gap-x-7 gap-y-2">
            {["Home", "Platform", "Detection", "Results"].map((label) => (
              <a
                key={label}
                href={`#${label.toLowerCase()}`}
                className="text-[13.5px] text-[color:var(--landing-ink-faint)] transition-colors hover:text-[color:var(--landing-ink)]"
              >
                {label}
              </a>
            ))}
          </nav>
          <p className="text-[13px]" style={{ color: "var(--landing-ink-faint)" }}>
            Graduation project · ENISo × Cyber Science Lab
          </p>
        </div>
      </footer>
    </div>
  );
}
