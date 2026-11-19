/**
 * The token palette, resolved to literal colours for anything that paints pixels.
 *
 * A canvas cannot inherit a CSS custom property: `ctx.strokeStyle = "var(--color-critical)"`
 * is not a colour, it is a parse error that silently paints black. So the network map had
 * accumulated its own hard-coded copies of the severity ramp, the accent and the entity-type
 * hues, in two files that had to be kept in step with styles.css by hand.
 *
 * This resolves them once from the document, so styles.css stays the single source of truth
 * and the map is themed by the same tokens as everything else. Only the *sourcing* changes:
 * the force simulation, the projection and the draw loop are untouched, and no graph library
 * is introduced (docs/DECISIONS.md D26).
 */

import { useEffect, useState } from "react";

export interface NodeTypeMeta {
  color: string;
  letter: string;
  label: string;
}

export interface CanvasPalette {
  /** Entity types, keyed as they arrive from the graph endpoint. */
  types: Record<string, NodeTypeMeta>;
  unknown: NodeTypeMeta;
  critical: string;
  high: string;
  /** The rest of the severity ramp, so edges can be toned by the risk they inherit. */
  medium: string;
  low: string;
  ok: string;
  accent: string;
  ink: string;
  line: string;
  surface: string;
}

/**
 * Entity glyphs.
 *
 * The letter is the accessible half of this: type is never carried by colour alone, which
 * matters both for a colour-blind analyst and for the printed report figures.
 *
 * The hues are read from CSS so the light theme's darker variants come for free — the dark
 * ramp is tuned for luminance against near-black and reads as pastel on white.
 */
const TYPE_GLYPHS: { key: string; letter: string; label: string; token: string }[] = [
  { key: "api_key", letter: "K", label: "API key", token: "--color-entity-api-key" },
  { key: "user", letter: "U", label: "User", token: "--color-entity-user" },
  { key: "model", letter: "M", label: "Model", token: "--color-entity-model" },
  { key: "ip", letter: "I", label: "IP address", token: "--color-entity-ip" },
  { key: "endpoint", letter: "E", label: "Endpoint", token: "--color-entity-endpoint" },
];

function read(styles: CSSStyleDeclaration, token: string, fallback: string): string {
  const value = styles.getPropertyValue(token).trim();
  return value || fallback;
}

/**
 * Resolve the palette against the document's current theme.
 *
 * Call this again on a theme change: the returned values are literals and do not update
 * themselves.
 */
export function getCanvasPalette(): CanvasPalette {
  const styles = getComputedStyle(document.documentElement);
  const types: Record<string, NodeTypeMeta> = {};
  for (const glyph of TYPE_GLYPHS) {
    types[glyph.key] = {
      color: read(styles, glyph.token, "#9daac2"),
      letter: glyph.letter,
      label: glyph.label,
    };
  }
  return {
    types,
    unknown: { color: read(styles, "--color-ink-faint", "#9daac2"), letter: "?", label: "Unknown" },
    critical: read(styles, "--color-critical", "#ff5d6c"),
    high: read(styles, "--color-high", "#ff9f43"),
    medium: read(styles, "--color-medium", "#ffd93d"),
    low: read(styles, "--color-low", "#4ec9a5"),
    ok: read(styles, "--color-ok", "#2fbf87"),
    accent: read(styles, "--color-accent", "#6f9bff"),
    ink: read(styles, "--color-ink", "#eef3fb"),
    line: read(styles, "--color-line", "#23304a"),
    surface: read(styles, "--color-surface", "#101b30"),
  };
}

/**
 * The palette, kept in step with the theme.
 *
 * Deliberately driven by a MutationObserver on `data-theme` rather than by the theme
 * context. ThemeProvider writes that attribute from an effect, and effects run
 * children-first — so a consumer that re-resolved on the context value would read the
 * *previous* theme's tokens and then never correct itself, because nothing re-renders
 * it afterwards. Watching the attribute makes the resolution independent of effect
 * ordering, which is the only thing that actually guarantees correctness here.
 */
export function useCanvasPalette(): CanvasPalette {
  const [palette, setPalette] = useState<CanvasPalette>(getCanvasPalette);

  useEffect(() => {
    const target = document.documentElement;
    const sync = () => setPalette(getCanvasPalette());
    // Resolve once on mount: the blocking script in index.html may have set the
    // attribute before React ever ran.
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(target, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return palette;
}
