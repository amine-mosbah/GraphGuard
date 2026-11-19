/**
 * The 3D network map.
 *
 * Layout is a real force simulation in three dimensions (`d3-force-3d`) over the actual
 * graph edges, so proximity on screen means something: entities that interact are drawn
 * near each other. A decorative sphere layout would look similar and mean nothing, which
 * for an analytics tool is worse than no picture at all.
 *
 * Rendering is canvas 2D with a hand-written projection rather than WebGL. At the measured
 * size — a few hundred nodes and around a thousand edges — WebGL's advantage does not
 * appear, and canvas keeps the bundle free of a 3D engine.
 *
 * Motion discipline (docs/DESIGN.md §11.2): only *flagged* nodes pulse. Animating the whole
 * estate would drown the signal in movement and cost frames for nothing. The healthy bulk
 * is drawn smaller and dimmer — level-of-detail in service of attention, not of frame rate.
 * `prefers-reduced-motion` disables the pulse entirely.
 *
 * Edges are selectable and toned by risk. Two things about that are deliberate. First, the tone
 * is *inherited* from the endpoints — no detector scores an edge, and lib/graph-risk.ts explains
 * at length why pretending otherwise would be a false claim. Second, risk drives line width and
 * opacity as well as hue: a green-to-red ramp is the single worst thing to put on a colour-blind
 * analyst, so the same information has to survive with the hue removed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { forceCenter, forceLink, forceManyBody, forceSimulation } from "d3-force-3d";

import { usePrefersReducedMotion } from "./charts";
import type { GraphLink, GraphNode } from "../lib/types";
import { useTheme } from "../lib/theme";
import { useCanvasPalette } from "../lib/canvas-palette";
import type { CanvasPalette } from "../lib/canvas-palette";
import { edgeBand, edgeReach, edgeRisk, linkKey, riskIndex } from "../lib/graph-risk";
import type { EdgeBand, EdgeReach } from "../lib/graph-risk";

interface Positioned extends GraphNode {
  x: number;
  y: number;
  z: number;
  /** Screen-space position, refreshed every frame. */
  sx: number;
  sy: number;
  sr: number;
  depth: number;
}

export interface NetworkHandle {
  zoom: (factor: number) => void;
  reset: () => void;
}

export function NetworkCanvas({
  nodes,
  links,
  selectedId,
  onSelect,
  selectedLinkKey,
  onSelectLink,
  typeFilter,
  relationFilter,
  colourEdgesByRisk,
  onReady,
}: {
  nodes: GraphNode[];
  links: GraphLink[];
  selectedId: string | null;
  onSelect: (node: GraphNode | null) => void;
  selectedLinkKey: string | null;
  onSelectLink: (link: GraphLink | null) => void;
  typeFilter: string | null;
  /** Show only this relation, or every relation when null. */
  relationFilter: string | null;
  colourEdgesByRisk: boolean;
  onReady?: (handle: NetworkHandle) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({
    yaw: 0.6,
    pitch: -0.3,
    zoom: 1,
    hoverId: null as string | null,
    // On the mutable ref rather than in state: it changes on every pointer move and the render
    // loop reads it each frame anyway, so routing it through React would re-render the whole
    // canvas component at pointer frequency for no benefit.
    hoverLinkKey: null as string | null,
  });
  const [hoverLabel, setHoverLabel] = useState<HoverTarget | null>(null);

  // Subscribed, not read once: the OS setting can change while the map is open,
  // and a stale value would keep the pulse running for exactly the person who
  // just asked for it to stop. Same hook the charts use.
  const reduceMotion = usePrefersReducedMotion();

  // The canvas paints its own pixels, so unlike the rest of the interface it cannot
  // inherit the theme from CSS custom properties — a var() assigned to strokeStyle is
  // not a colour, it is a silent parse failure that paints black. The tokens are
  // therefore resolved to literals once per theme, which keeps styles.css the single
  // place any colour is decided rather than leaving the map with its own copy.
  const { theme } = useTheme();
  const isLight = theme === "light";
  const palette = useCanvasPalette();

  /** Run the force simulation once per dataset, not per frame. */
  const points = useMemo<Positioned[]>(() => {
    if (nodes.length === 0) return [];
    const simNodes = nodes.map((node) => ({ ...node, x: 0, y: 0, z: 0 }));
    const byId = new Map(simNodes.map((node) => [node.id, node]));
    const simLinks = links
      .filter((link) => byId.has(link.source) && byId.has(link.target))
      .map((link) => ({ source: link.source, target: link.target }));

    const simulation = forceSimulation(simNodes, 3)
      .force("charge", forceManyBody().strength(-38))
      .force(
        "link",
        forceLink(simLinks)
          .id((node: { id: string }) => node.id)
          .distance(34)
          .strength(0.35),
      )
      .force("center", forceCenter(0, 0, 0))
      .stop();

    // Run to a settled layout synchronously. Ticking inside the render loop would
    // make the graph writhe while an analyst is trying to read it.
    for (let i = 0; i < 220; i += 1) simulation.tick();

    return simNodes.map((node) => ({
      ...node,
      sx: 0,
      sy: 0,
      sr: 0,
      depth: 0,
    })) as Positioned[];
  }, [nodes, links]);

  /**
   * Radius that contains ~95% of the layout.
   *
   * The 95th percentile rather than the maximum: a force layout usually flings two or three
   * poorly-connected nodes far out, and fitting to those would shrink the interesting core to
   * a speck. Outliers are still reachable by zooming out.
   */
  const spread = useMemo(() => {
    if (points.length === 0) return 100;
    const radii = points
      .map((point) => Math.hypot(point.x, point.y, point.z))
      .sort((a, b) => a - b);
    return Math.max(40, radii[Math.floor(radii.length * 0.95)] ?? 100);
  }, [points]);

  /**
   * Edges, resolved to layout indices and to the risk they inherit.
   *
   * Sorted so the riskiest draw last. Edges are painted in one pass behind the nodes, and with a
   * thousand of them a critical edge drawn early is simply overwritten by the benign mesh — the
   * one line the analyst is looking for would be the one hidden.
   */
  const linkPairs = useMemo(() => {
    const position = new Map(points.map((point, i) => [point.id, i]));
    const byId = riskIndex(nodes);
    return links
      .map((link) => ({
        a: position.get(link.source),
        b: position.get(link.target),
        link,
        key: linkKey(link),
        risk: edgeRisk(link, byId),
        reach: edgeReach(link, byId),
      }))
      .filter(
        (pair): pair is {
          a: number;
          b: number;
          link: GraphLink;
          key: string;
          risk: number;
          reach: EdgeReach;
        } => pair.a !== undefined && pair.b !== undefined,
      )
      // Fully-flagged edges last, so the nine that matter are never overpainted by the mesh.
      .sort((x, y) => reachOrder(x.reach) - reachOrder(y.reach) || x.risk - y.risk);
  }, [links, nodes, points]);

  /** The two entities a selected edge joins — the focus set while an edge is being inspected. */
  const selectedLinkEnds = useMemo(() => {
    const found = links.find((link) => linkKey(link) === selectedLinkKey);
    return found ? new Set([found.source, found.target]) : null;
  }, [links, selectedLinkKey]);

  const degreeById = useMemo(() => {
    const degree = new Map<string, number>();
    for (const link of links) {
      degree.set(link.source, (degree.get(link.source) ?? 0) + 1);
      degree.set(link.target, (degree.get(link.target) ?? 0) + 1);
    }
    return degree;
  }, [links]);

  const selectedNeighbours = useMemo(() => {
    const neighbours = new Set<string>();
    if (!selectedId) return neighbours;
    neighbours.add(selectedId);
    for (const link of links) {
      if (link.source === selectedId) neighbours.add(link.target);
      if (link.target === selectedId) neighbours.add(link.source);
    }
    return neighbours;
  }, [links, selectedId]);

  const draw = useCallback(
    (time: number) => {
      const canvas = canvasRef.current;
      const wrap = wrapRef.current;
      if (!canvas || !wrap) return;

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = wrap.clientWidth;
      const height = wrap.clientHeight;
      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
      }

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const { yaw, pitch, zoom, hoverId } = stateRef.current;
      const cy = Math.cos(yaw);
      const sy = Math.sin(yaw);
      const cp = Math.cos(pitch);
      const sp = Math.sin(pitch);
      const cx = width / 2;
      const cyc = height / 2;
      // Fit the layout to the smaller canvas dimension, leaving a margin so nodes at the
      // edge are not clipped by their own radius.
      const scale = ((Math.min(width, height) * 0.78) / spread) * zoom;
      // A long focal length keeps depth readable without the strong wide-angle
      // shrinkage that made far nodes vanish and the whole layout look smaller
      // than the space it was given.
      const focal = spread * scale * 3.2;

      // A restrained spatial field makes the orbit legible even before the pointer
      // moves. It is a depth cue, not a data layer: the graph itself remains the only
      // thing carrying analytical meaning.
      const field = ctx.createRadialGradient(
        cx,
        cyc,
        0,
        cx,
        cyc,
        Math.max(width, height) * 0.68,
      );
      field.addColorStop(0, withAlpha(palette.accent, isLight ? 0.055 : 0.08));
      field.addColorStop(0.58, withAlpha(palette.surface, 0.02));
      field.addColorStop(1, withAlpha(palette.surface, 0));
      ctx.fillStyle = field;
      ctx.fillRect(0, 0, width, height);

      ctx.save();
      ctx.translate(cx, cyc);
      ctx.rotate(yaw * 0.12);
      ctx.scale(1, 0.34 + Math.abs(Math.sin(pitch)) * 0.16);
      ctx.strokeStyle = withAlpha(palette.line, isLight ? 0.52 : 0.72);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 7]);
      for (const radius of [0.19, 0.36, 0.54, 0.72]) {
        ctx.beginPath();
        ctx.arc(0, 0, Math.min(width, height) * radius, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
      ctx.setLineDash([]);

      // Project every node once per frame.
      for (const point of points) {
        const x1 = point.x * cy - point.z * sy;
        const z1 = point.x * sy + point.z * cy;
        const y1 = point.y * cp - z1 * sp;
        const z2 = point.y * sp + z1 * cp;
        const perspective = focal / (focal + z2 * scale);
        point.sx = cx + x1 * scale * perspective;
        point.sy = cyc + y1 * scale * perspective;
        point.depth = z2;
        const filtered = typeFilter !== null && point.type !== typeFilter;
        const outsideFocus = selectedId !== null && !selectedNeighbours.has(point.id);
        const outsideLink = selectedLinkEnds !== null && !selectedLinkEnds.has(point.id);
        const dimmed = filtered || outsideFocus || outsideLink;
        const degree = degreeById.get(point.id) ?? 0;
        const base =
          3.8 +
          Math.min(3.2, Math.log2(degree + 1) * 0.9) +
          Math.sqrt(point.alerts) * 2 +
          point.risk * 4.8;
        point.sr = Math.max(1.6, base * perspective * (dimmed ? 0.55 : 1));
      }

      const order = points.map((_, i) => i).sort((a, b) => points[b].depth - points[a].depth);

      // --- links, behind everything ---
      ctx.lineWidth = 1;
      for (const pair of linkPairs) {
        const p = points[pair.a];
        const q = points[pair.b];
        const filtered =
          (typeFilter !== null && p.type !== typeFilter && q.type !== typeFilter) ||
          (relationFilter !== null && pair.link.relation !== relationFilter);
        const isSelectedLink = pair.key === selectedLinkKey;
        const isHoverLink = pair.key === stateRef.current.hoverLinkKey;
        const connectedToSelection =
          selectedId !== null && (p.id === selectedId || q.id === selectedId);
        const outsideFocus =
          (selectedId !== null && !connectedToSelection) ||
          (selectedLinkKey !== null && !isSelectedLink);
        const muted = filtered || outsideFocus;

        const near = (p.depth + q.depth) / 2;
        const depthFade = Math.max(0.03, 0.16 - near * 0.0012);
        // A log scale, because edge weight now spans 1 to a few thousand events. Linear would
        // make one credential's traffic a slab and everything else invisible.
        const heft = Math.min(1, Math.log10((pair.link.weight || 1) + 1) / 3.2);
        const band = edgeBand(pair.risk);

        // Risk drives width and opacity as well as hue, so the ramp survives greyscale and
        // colour blindness. Reach gates how loudly it is allowed to speak: an edge with one
        // flagged end is a tint, an edge with two is a statement. See lib/graph-risk.ts —
        // without this split, a quarter of the graph shouts and the nine edges that matter
        // are invisible inside it.
        const risky = colourEdgesByRisk && band !== "none";
        const emphasis = risky ? 0.45 + pair.risk * 0.55 : 0;

        let colour: string;
        let alpha: number;
        let lineWidth: number;

        if (isSelectedLink || isHoverLink) {
          colour = isSelectedLink ? palette.ink : palette.accent;
          alpha = 0.95;
          lineWidth = isSelectedLink ? 2.6 : 2;
        } else if (muted) {
          colour = palette.line;
          // Same reasoning as the nodes: with a link selected the graph around it has to stay
          // faintly legible, or the finding floats in a void.
          alpha = selectedLinkKey !== null ? 0.06 : outsideFocus ? 0.025 : 0.04;
          lineWidth = 0.55;
        } else if (risky && pair.reach === "both") {
          colour = bandColour(band, palette);
          alpha = Math.min(0.95, 0.5 + emphasis * 0.45);
          lineWidth = 1.4 + emphasis * 1.6 + heft * 0.5;
        } else if (risky) {
          colour = bandColour(band, palette);
          alpha = 0.1 + emphasis * 0.12;
          lineWidth = 0.7 + heft * 0.5;
        } else if (connectedToSelection) {
          colour = palette.ink;
          alpha = 0.72;
          lineWidth = 1.7;
        } else {
          // The benign bulk. Tinted towards the healthy end when the risk ramp is on, so the
          // map reads as "green mesh, red threads" — but kept faint, because a thousand edges
          // painted at full strength is a haystack, not a signal (docs/DESIGN.md §11.2).
          colour = colourEdgesByRisk ? palette.ok : palette.accent;
          alpha = Math.min(0.42, depthFade * (isLight ? 1.7 : 1) * (0.7 + heft * 0.8));
          lineWidth = 0.6 + heft * 0.7;
        }

        ctx.strokeStyle = withAlpha(colour, alpha);
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        ctx.moveTo(p.sx, p.sy);
        ctx.lineTo(q.sx, q.sy);
        ctx.stroke();
      }

      // --- nodes, far to near ---
      for (const index of order) {
        const point = points[index];
        const meta = palette.types[point.type] ?? palette.unknown;
        const filtered = typeFilter !== null && point.type !== typeFilter;
        const outsideFocus = selectedId !== null && !selectedNeighbours.has(point.id);
        // A selected edge keeps only two entities in focus, against a node selection's whole
        // neighbourhood. Fading the rest as hard would leave an all-but-black canvas with two
        // dots on it and no context to place them in, so link focus dims more gently.
        const outsideLink = selectedLinkEnds !== null && !selectedLinkEnds.has(point.id);
        const dimmed = filtered || outsideFocus || outsideLink;
        const isSelected = point.id === selectedId || selectedLinkEnds?.has(point.id) === true;
        const isHover = point.id === hoverId;
        const flagged = point.alerts > 0;

        // Depth fade: distant nodes recede rather than competing for attention.
        const fade = Math.max(isLight ? 0.48 : 0.3, 1 - (point.depth + 140) / 420);
        const dimAlpha = outsideFocus ? 0.1 : outsideLink ? 0.3 : 0.18;
        ctx.globalAlpha = dimmed ? dimAlpha : fade;

        // Pulse only what is flagged (§12.2).
        if (flagged && !reduceMotion && !dimmed) {
          const phase = (Math.sin(time / 520 + index) + 1) / 2;
          ctx.globalAlpha = (dimmed ? dimAlpha : fade) * 0.35;
          ctx.beginPath();
          ctx.arc(point.sx, point.sy, point.sr + 4 + phase * 7, 0, Math.PI * 2);
          ctx.strokeStyle = point.risk >= 0.75 ? palette.critical : palette.high;
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.globalAlpha = dimmed ? dimAlpha : fade;
        }

        // Sphere shading — a light source up and to the left reads as volume without
        // any 3D machinery.
        const lightX = point.sx - point.sr * 0.38;
        const lightY = point.sy - point.sr * 0.38;
        const gradient = ctx.createRadialGradient(
          lightX,
          lightY,
          point.sr * 0.1,
          point.sx,
          point.sy,
          point.sr,
        );
        gradient.addColorStop(0, meta.color);
        gradient.addColorStop(1, shade(meta.color, -0.55));
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(point.sx, point.sy, point.sr, 0, Math.PI * 2);
        ctx.fill();

        // Specular highlight.
        ctx.globalAlpha = (dimmed ? dimAlpha : fade) * (isLight ? 0.34 : 0.5);
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(lightX, lightY, point.sr * 0.26, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = dimmed ? dimAlpha : fade;

        if (isSelected || isHover) {
          ctx.beginPath();
          ctx.arc(point.sx, point.sy, point.sr + 4, 0, Math.PI * 2);
          ctx.strokeStyle = isSelected ? palette.ink : palette.accent;
          ctx.lineWidth = isSelected ? 2 : 1.4;
          ctx.stroke();
        }

        // The glyph makes node type readable without colour. Labels stay selective so
        // the overview remains a topology rather than a wall of text.
        if (!dimmed && (point.sr >= 6.4 || isSelected || isHover)) {
          ctx.globalAlpha = fade;
          ctx.fillStyle = isLight ? "#ffffff" : palette.surface;
          ctx.font = `700 ${Math.max(7, Math.min(10, point.sr * 0.9))}px Inter, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(meta.letter, point.sx, point.sy + 0.25);
        }

        if (!dimmed && isSelected) {
          const label = point.label.length > 22 ? `${point.label.slice(0, 20)}…` : point.label;
          ctx.globalAlpha = 0.96;
          ctx.font = "600 10px JetBrains Mono, monospace";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          const textWidth = ctx.measureText(label).width;
          const labelY = point.sy - point.sr - 9;
          ctx.fillStyle = withAlpha(palette.surface, 0.9);
          roundRect(ctx, point.sx - textWidth / 2 - 5, labelY - 13, textWidth + 10, 17, 5);
          ctx.fill();
          ctx.fillStyle = palette.ink;
          ctx.fillText(label, point.sx, labelY);
        }
      }
      ctx.globalAlpha = 1;
    },
    [
      points,
      linkPairs,
      selectedId,
      selectedNeighbours,
      selectedLinkKey,
      selectedLinkEnds,
      typeFilter,
      relationFilter,
      colourEdgesByRisk,
      reduceMotion,
      spread,
      isLight,
      palette,
      degreeById,
    ],
  );

  useEffect(() => {
    let frame = 0;
    const loop = (time: number) => {
      draw(time);
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [draw]);

  /** Nearest node under the pointer, or null past a generous tolerance. */
  const pickNode = useCallback(
    (px: number, py: number): Positioned | null => {
      let best: Positioned | null = null;
      let bestDistance = Infinity;
      for (const point of points) {
        if (typeFilter !== null && point.type !== typeFilter) continue;
        const distance = Math.hypot(point.sx - px, point.sy - py);
        if (distance < Math.max(point.sr + 6, 10) && distance < bestDistance) {
          bestDistance = distance;
          best = point;
        }
      }
      return best;
    },
    [points, typeFilter],
  );

  /**
   * Nearest edge under the pointer.
   *
   * Distance to the *segment*, not to its midpoint: an edge is a line, and hit-testing its centre
   * would make long edges unclickable along most of their length.
   *
   * Ties break towards the riskier edge rather than the nearer one. Edges overlap constantly in a
   * projected 3-D layout, and when two are within a few pixels the one the analyst is reaching for
   * is the one the tool has flagged, not whichever happens to be a pixel closer.
   */
  const pickLink = useCallback(
    (px: number, py: number): { link: GraphLink; key: string; x: number; y: number } | null => {
      const tolerance = 6;
      let best: { link: GraphLink; key: string; x: number; y: number } | null = null;
      let bestRisk = -1;
      for (const pair of linkPairs) {
        if (relationFilter !== null && pair.link.relation !== relationFilter) continue;
        const p = points[pair.a];
        const q = points[pair.b];
        if (typeFilter !== null && p.type !== typeFilter && q.type !== typeFilter) continue;
        const hit = distanceToSegment(px, py, p.sx, p.sy, q.sx, q.sy);
        if (hit.distance > tolerance) continue;
        if (pair.risk > bestRisk) {
          bestRisk = pair.risk;
          best = { link: pair.link, key: pair.key, x: hit.x, y: hit.y };
        }
      }
      return best;
    },
    [linkPairs, points, relationFilter, typeFilter],
  );

  /**
   * What is under the pointer.
   *
   * Nodes win. An edge terminates *at* a node, so near a marker both are always in range, and
   * letting the edge win would make the last few pixels of every node unselectable.
   */
  const pick = useCallback(
    (clientX: number, clientY: number): HoverTarget | null => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const px = clientX - rect.left;
      const py = clientY - rect.top;

      // The tooltip sits above its anchor, so an anchor near the top edge would push it out of
      // the canvas and over the toolbar above. Flip it below instead, and keep it horizontally
      // inside the frame.
      const place = (x: number, y: number) => ({
        x: Math.max(90, Math.min(rect.width - 90, x)),
        y,
        below: y < 56,
      });

      const node = pickNode(px, py);
      if (node) return { kind: "node", ...place(node.sx, node.sy), node };
      const link = pickLink(px, py);
      return link
        ? { kind: "link", ...place(link.x, link.y), link: link.link, key: link.key }
        : null;
    },
    [pickNode, pickLink],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    let dragging = false;
    let moved = false;
    let lastX = 0;
    let lastY = 0;

    const onDown = (event: PointerEvent) => {
      dragging = true;
      moved = false;
      lastX = event.clientX;
      lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    };

    const onMove = (event: PointerEvent) => {
      if (dragging) {
        const dx = event.clientX - lastX;
        const dy = event.clientY - lastY;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        lastX = event.clientX;
        lastY = event.clientY;
        stateRef.current.yaw += dx * 0.006;
        // Clamp pitch just short of the poles, where the projection degenerates
        // and the graph appears to flip.
        stateRef.current.pitch = Math.max(
          -1.45,
          Math.min(1.45, stateRef.current.pitch + dy * 0.006),
        );
        setHoverLabel(null);
        return;
      }
      const hit = pick(event.clientX, event.clientY);
      stateRef.current.hoverId = hit?.kind === "node" ? hit.node.id : null;
      stateRef.current.hoverLinkKey = hit?.kind === "link" ? hit.key : null;
      canvas.style.cursor = hit ? "pointer" : "grab";
      setHoverLabel(hit);
    };

    const onUp = (event: PointerEvent) => {
      if (dragging && !moved) {
        const hit = pick(event.clientX, event.clientY);
        // A node and an edge are alternative things to inspect, so selecting either clears the
        // other. Leaving both set would dim the map down to their intersection, which is
        // usually empty.
        if (hit?.kind === "node") {
          onSelectLink(null);
          onSelect(hit.node);
        } else if (hit?.kind === "link") {
          onSelect(null);
          onSelectLink(hit.link);
        } else {
          onSelect(null);
          onSelectLink(null);
        }
      }
      dragging = false;
      canvas.releasePointerCapture?.(event.pointerId);
    };

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
      stateRef.current.zoom = Math.max(0.35, Math.min(4, stateRef.current.zoom * factor));
    };

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [pick, onSelect, onSelectLink]);

  useEffect(() => {
    onReady?.({
      zoom: (factor) => {
        stateRef.current.zoom = Math.max(0.35, Math.min(4, stateRef.current.zoom * factor));
      },
      reset: () => {
        stateRef.current.yaw = 0.6;
        stateRef.current.pitch = -0.3;
        stateRef.current.zoom = 1;
      },
    });
  }, [onReady]);

  return (
    <div ref={wrapRef} className="relative size-full">
      <canvas
        ref={canvasRef}
        className="size-full cursor-grab touch-none"
        aria-label="Interactive three-dimensional entity relationship map. Drag to orbit, scroll to zoom, and click a node or a relation to inspect it."
      />
      {hoverLabel && (
        <div
          className={`pointer-events-none absolute z-10 max-w-[min(420px,90%)] -translate-x-1/2 truncate rounded-lg border border-[var(--color-line)] bg-[var(--color-surface-2)]/95 px-2.5 py-1.5 text-xs whitespace-nowrap shadow-xl backdrop-blur ${
            hoverLabel.below ? "translate-y-3" : "-translate-y-[calc(100%+12px)]"
          }`}
          style={{ left: hoverLabel.x, top: hoverLabel.y }}
        >
          {hoverLabel.kind === "node" ? (
            <>
              <span className="font-mono text-[var(--color-ink)]">{hoverLabel.node.label}</span>
              {hoverLabel.node.alerts > 0 && (
                <span className="ml-2 text-[var(--color-critical)]">
                  {hoverLabel.node.alerts} alert{hoverLabel.node.alerts > 1 ? "s" : ""}
                </span>
              )}
            </>
          ) : (
            <>
              <span className="font-mono text-[var(--color-ink)]">
                {bare(hoverLabel.link.source)}
              </span>
              <span className="mx-1.5 text-[var(--color-accent)]">
                —{hoverLabel.link.relation}→
              </span>
              <span className="font-mono text-[var(--color-ink)]">
                {bare(hoverLabel.link.target)}
              </span>
              <span className="ml-2 text-[var(--color-ink-faint)]">
                {hoverLabel.link.weight.toLocaleString()} event
                {hoverLabel.link.weight === 1 ? "" : "s"}
              </span>
              {hoverLabel.link.failures > 0 && (
                <span className="ml-1.5 text-[var(--color-critical)]">
                  {hoverLabel.link.failures.toLocaleString()} failed
                </span>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** What the pointer is over. */
type HoverTarget =
  | { kind: "node"; x: number; y: number; below: boolean; node: Positioned }
  | { kind: "link"; x: number; y: number; below: boolean; link: GraphLink; key: string };

/** Node ids are `type:id`; the tooltip has no room for the prefix the type chip already says. */
function bare(nodeId: string): string {
  const cut = nodeId.indexOf(":");
  return cut === -1 ? nodeId : nodeId.slice(cut + 1);
}

/** Painting order: mesh, then half-flagged, then fully-flagged on top. */
function reachOrder(reach: EdgeReach): number {
  return reach === "both" ? 2 : reach === "one" ? 1 : 0;
}

/** The severity token for a band, resolved to a literal the canvas can actually paint. */
function bandColour(band: EdgeBand, palette: CanvasPalette): string {
  switch (band) {
    case "critical":
      return palette.critical;
    case "high":
      return palette.high;
    case "medium":
      return palette.medium;
    case "low":
      return palette.low;
    default:
      return palette.ok;
  }
}

/** Closest point on segment AB to P, and how far away it is. */
function distanceToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): { distance: number; x: number; y: number } {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  // A degenerate segment — both endpoints projected to the same pixel — would divide by zero.
  const t =
    lengthSquared === 0
      ? 0
      : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSquared));
  const x = ax + t * dx;
  const y = ay + t * dy;
  return { distance: Math.hypot(px - x, py - y), x, y };
}

/** Darken or lighten a hex colour, for the sphere gradient. */
/**
 * Apply an alpha to a resolved colour.
 *
 * The tokens resolve to `#rrggbb`, and canvas has no equivalent of Tailwind's `/20`
 * syntax, so the channels are read out and re-emitted as rgba.
 */
function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith("#")) return color;
  const value = parseInt(color.slice(1), 16);
  return `rgba(${(value >> 16) & 0xff},${(value >> 8) & 0xff},${value & 0xff},${alpha})`;
}

function shade(hex: string, amount: number): string {
  const value = parseInt(hex.slice(1), 16);
  const channel = (shift: number) => {
    const base = (value >> shift) & 0xff;
    const next = amount < 0 ? base * (1 + amount) : base + (255 - base) * amount;
    return Math.round(Math.max(0, Math.min(255, next)));
  };
  return `rgb(${channel(16)},${channel(8)},${channel(0)})`;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, radius);
}
