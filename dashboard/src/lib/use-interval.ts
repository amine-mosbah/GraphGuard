/**
 * A self-terminating polling hook.
 *
 * `delay: null` stops the timer, which is how a caller expresses "there is nothing left
 * to wait for" — the runs page polls only while a run is queued or running, then stops
 * on its own rather than hammering the API forever.
 *
 * The callback lives in a ref so that changing it does not restart the interval. Passing
 * a fresh closure every render is the normal case in React, and restarting the timer on
 * each one would mean a 5-second poll that never actually fires.
 */

import { useEffect, useRef } from "react";

export function useInterval(callback: () => void, delay: number | null): void {
  const saved = useRef(callback);

  useEffect(() => {
    saved.current = callback;
  }, [callback]);

  useEffect(() => {
    if (delay === null) return;
    const id = window.setInterval(() => saved.current(), delay);
    return () => window.clearInterval(id);
  }, [delay]);
}
