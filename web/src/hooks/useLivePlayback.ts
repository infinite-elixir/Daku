import { useEffect, useState } from "react";

import { getPlaybackState, type PlaybackState } from "../lib/api";

/**
 * Poll the host player while a file card is on screen.
 *
 * Deliberately not a TanStack Query: the player state is process-local, cheap and only
 * needed when the reader is enabled, so a disabled poll must not even create an observer.
 */
export function useLivePlayback(
  enabled: boolean,
  intervalMs = 3000,
): PlaybackState | null {
  const [active, setActive] = useState<PlaybackState | null>(null);

  useEffect(() => {
    if (!enabled) {
      setActive(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const data = await getPlaybackState();
        if (!cancelled) {
          setActive(data.active);
        }
      } catch {
        if (!cancelled) {
          setActive(null);
        }
      }
      if (!cancelled) {
        timer = window.setTimeout(() => {
          void poll();
        }, intervalMs);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [enabled, intervalMs]);

  return active;
}
