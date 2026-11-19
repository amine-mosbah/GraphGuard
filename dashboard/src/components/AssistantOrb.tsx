/**
 * The assistant's orb: a lit disc that both marks the assistant and reports that it is
 * working.
 *
 * One piece of artwork with two jobs, which is why it is a component and not two. In the
 * welcome state it holds the product mark and simply idles; while a turn is streaming it
 * holds the label, whose letters animate one after another so the analyst can tell a slow
 * answer from a stalled one. Same disc, so the waiting state is recognisably the thing
 * that was already on screen rather than a new object appearing.
 *
 * The disc keeps its own blues in both themes — see the note in styles.css. It carries no
 * information that is not also written in text next to it, and the animated label is
 * announced through a polite live region rather than through the motion.
 *
 * Adapted from the CSS-only "generating" loader pattern (rotating inset-shadow ring plus
 * staggered letters); the keyframes live in styles.css so the reduced-motion rule there
 * applies to them like everything else.
 */

import type { ReactNode } from "react";

export function AssistantOrb({
  size = 132,
  children,
  className = "",
}: {
  size?: number;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`relative grid shrink-0 place-items-center select-none ${className}`}
      style={{ width: size, height: size }}
    >
      <div
        className="gg-orb-ring pointer-events-none absolute inset-0"
        aria-hidden="true"
      />
      {children}
    </div>
  );
}

/**
 * The orb in its working state.
 *
 * `text` is split per character for the stagger, so it is hidden from assistive tech as a
 * pile of single letters and re-announced once, whole, in the live region.
 */
export function AssistantWorking({
  size = 132,
  text = "Generating",
}: {
  size?: number;
  text?: string;
}) {
  return (
    <div className="flex items-center gap-4">
      <AssistantOrb size={size}>
        <span
          aria-hidden="true"
          className="relative font-medium tracking-tight text-white"
          style={{ fontSize: size < 100 ? 10 : 13 }}
        >
          {text.split("").map((letter, index) => (
            <span
              key={index}
              className="gg-orb-letter"
              style={{ animationDelay: `${index * 0.1}s` }}
            >
              {letter === " " ? " " : letter}
            </span>
          ))}
        </span>
      </AssistantOrb>
      <span className="sr-only" role="status" aria-live="polite">
        {text}
      </span>
    </div>
  );
}
