import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The rendered width of an element.
 *
 * The SVG figures on the chains page place marks on a real time axis, so they need a pixel
 * width before they can lay anything out. Scaling a fixed `viewBox` instead would be less
 * code and worse: it scales the *text* with the drawing, so a chain on a wide monitor gets
 * oversized labels and a narrow one gets unreadable ones.
 *
 * Returns 0 until the first observation, which callers use to skip the first paint rather
 * than rendering a collapsed figure that then jumps.
 */
export function useElementWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);

  useEffect(() => () => observer.current?.disconnect(), []);

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    if (!node) return;
    setWidth(node.clientWidth);
    observer.current = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(Math.round(entry.contentRect.width));
    });
    observer.current.observe(node);
  }, []);

  return [ref, width];
}
