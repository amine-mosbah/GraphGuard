/**
 * Risk as it applies to an *edge*.
 *
 * Read this before changing how edges are coloured, because there is a claim boundary here that
 * is easy to cross by accident.
 *
 * **No detector scores an edge.** Node-outlier, abnormal-subgraph and community-drift all emit
 * per-entity signals; the graph carries no per-interaction anomaly score, and inventing one in the
 * browser and painting it red would assert something the analytics layer never said. The project
 * has already had five such claims caught in a pre-defence audit, and this would be a sixth.
 *
 * So an edge's tone is **inherited, and labelled as inherited**: the higher of the two endpoints'
 * risks. That is a real and useful thing to show — it makes a flagged entity's neighbourhood
 * traceable at a glance, which is the actual analyst task — but it means "this edge touches
 * something a detector flagged", not "this interaction was malicious". Every surface that draws it
 * says so.
 *
 * What *is* measured per edge, and is presented separately rather than folded into the tone, is
 * the failure ratio: how many of the interactions on this pair were blocked, refused or errored.
 * That is a fact about the events, not an opinion about an entity, and conflating the two would
 * lose the distinction that makes either of them worth showing.
 */

import type { GraphLink, GraphNode } from "./types";

/**
 * Stable identity for a link.
 *
 * `(source, target, relation)` and not just the pair: two entities can be connected by more than
 * one relation — an api_key both `call`s and `error`s against the same model — and those are
 * genuinely different edges with different counts and outcomes.
 */
export function linkKey(link: GraphLink): string {
  return `${link.source}|${link.relation}|${link.target}`;
}

/** Risk by node id, for the edge lookups below. */
export function riskIndex(nodes: GraphNode[]): Map<string, GraphNode> {
  return new Map(nodes.map((node) => [node.id, node]));
}

/**
 * The risk an edge inherits from the entities it connects.
 *
 * The maximum rather than the mean: an edge from a clean entity to a badly compromised one is
 * exactly the edge an analyst wants to see, and averaging would halve it into the background.
 */
export function edgeRisk(link: GraphLink, index: Map<string, GraphNode>): number {
  return Math.max(index.get(link.source)?.risk ?? 0, index.get(link.target)?.risk ?? 0);
}

/**
 * How much of an edge is flagged.
 *
 * The distinction the map lives or dies on. In a representative run 45 of 304 entities carry an
 * alert, and because those are the high-degree ones, **254 of 903 edges touch one** — over a
 * quarter of the graph. Painting all of them at full strength turns the map into a red hairball
 * and buries the nine edges where *both* ends are flagged, which are the ones worth looking at.
 *
 * So reach is a separate axis from band: the band says how bad, the reach says how much of the
 * edge is implicated, and only "both" earns full visual weight. This is the same
 * pulse-the-alert-not-the-world discipline as docs/DESIGN.md §11.2, applied to edges.
 */
export type EdgeReach = "none" | "one" | "both";

export function edgeReach(link: GraphLink, index: Map<string, GraphNode>): EdgeReach {
  const source = index.get(link.source)?.risk ?? 0;
  const target = index.get(link.target)?.risk ?? 0;
  if (source > 0 && target > 0) return "both";
  if (source > 0 || target > 0) return "one";
  return "none";
}

export type EdgeBand = "none" | "low" | "medium" | "high" | "critical";

/**
 * Bucket a risk into the app's severity bands.
 *
 * Deliberately the same thresholds as `severityOf` in components/ui, so an edge's colour and the
 * severity badge on the alert that caused it can never disagree. A separate continuous scale
 * would look smoother and would let the map say "high" where the queue says "medium".
 */
export function edgeBand(risk: number): EdgeBand {
  if (risk <= 0) return "none";
  if (risk >= 0.9) return "critical";
  if (risk >= 0.75) return "high";
  if (risk >= 0.5) return "medium";
  return "low";
}

export const BAND_LABEL: Record<EdgeBand, string> = {
  none: "No alert on either end",
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

/** The CSS token each band uses, for surfaces that can read custom properties. */
export const BAND_TOKEN: Record<EdgeBand, string> = {
  none: "var(--color-ok)",
  low: "var(--color-low)",
  medium: "var(--color-medium)",
  high: "var(--color-high)",
  critical: "var(--color-critical)",
};

/** Share of interactions on this edge that were blocked, refused or errored. */
export function failureRatio(link: GraphLink): number {
  const total = link.weight || 0;
  return total > 0 ? (link.failures ?? 0) / total : 0;
}
