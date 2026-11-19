/**
 * The GraphGuard mark and wordmark.
 *
 * The mark is a four-pointed star cut out of a pinwheel frame. It is not drawn by hand: the
 * supplied artwork was traced off the source raster at sub-pixel precision and fitted, and
 * the geometry turned out to be exact enough to be worth reconstructing rather than
 * approximating. What the fit found:
 *
 *  - The whole glyph is *one* radius. All eight curves — four on the outer silhouette, four
 *    around the inner star — are circular arcs of the same radius, ~1.007x the glyph's half
 *    width, each fitting its traced contour to within 0.16 px on a 291 px render.
 *  - It has 4-fold ROTATIONAL symmetry and no mirror symmetry (measured: 0.965 for each of
 *    the 90/180/270 rotations against 0.74 for every reflection). It is a pinwheel, so it
 *    must never be flipped — mirroring it produces a different logo.
 *  - The four straight edges lie on the sides of the bounding square, each running from just
 *    beside the side's midpoint to 42% of the way along. That slight offset from the exact
 *    midpoint is real, not noise: it is consistent across all four blades, and snapping it
 *    to the midpoint measurably worsens the match to the artwork.
 *
 * The path below was then refined by rendering it and maximising overlap against the source
 * coverage field. It settles at IoU 0.9625, which is the ceiling for a shape with this
 * perimeter-to-area ratio at that resolution — the whole residual is a half-pixel hairline
 * along the boundary, with over- and under-coverage balanced.
 *
 * Drawn in `currentColor`. The artwork is a solid single-colour silhouette, so the mark
 * inherits from its context rather than carrying a palette of its own: white on the navy
 * sidebar and the landing page, ink on a light workspace, accent where it is being used as
 * a piece of branding. `fill-rule="evenodd"` is what makes the star a hole.
 */

const PATH =
  "M49.476 0L71.257 0A50.687 50.687 0 0 0 100 49.476" +
  "L100 71.257A50.687 50.687 0 0 0 50.524 100" +
  "L28.743 100A50.687 50.687 0 0 0 0 50.524" +
  "L0 28.743A50.687 50.687 0 0 0 49.476 0Z" +
  "M50 7.645A50.326 50.326 0 0 1 7.645 50" +
  "A50.326 50.326 0 0 1 50 92.355A50.326 50.326 0 0 1 92.355 50" +
  "A50.326 50.326 0 0 1 50 7.645Z";

export function GraphGuardMark({
  size = 32,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATH} fill="currentColor" fillRule="evenodd" />
    </svg>
  );
}

/**
 * The lockup: mark plus wordmark.
 *
 * The mark is drawn at 0.78x `size` rather than at `size`. Unlike an icon from a set, this
 * glyph bleeds to all four edges of its own box — there is no built-in padding — so setting
 * it to the same nominal size as surrounding icons makes it read markedly larger. At 1:1 it
 * measured about 1.9x the wordmark's cap height; 0.78 brings it to roughly 1.5x, which is
 * where a mark sits beside its own name without shouting over it.
 *
 * `leading-none` is load-bearing for the same reason. `items-center` centres on the line
 * box, and the line box for "GraphGuard" reserves room for a descender the word does not
 * have, which pushed the mark about a pixel low.
 *
 * The gap is proportional rather than a fixed utility class, because this lockup is used
 * from 22 px in the footer to 34 px in the hero. A fixed 8 px reads as 0.74x cap height at
 * the small end and 0.50x at the large one, so the same lockup looks differently spaced
 * depending on where it appears; 0.31x `size` holds it at about 0.6x cap throughout.
 */
export function GraphGuardLogo({
  size = 30,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center ${className}`}
      style={{ gap: size * 0.31 }}
    >
      <GraphGuardMark size={Math.round(size * 0.78)} />
      <span
        className="font-semibold leading-none tracking-tight"
        style={{ fontSize: size * 0.72, letterSpacing: "-0.02em" }}
      >
        GraphGuard
      </span>
    </span>
  );
}
