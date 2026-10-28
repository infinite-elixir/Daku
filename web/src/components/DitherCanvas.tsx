import { useEffect, useState } from "react";

import type { Kind } from "../lib/api";
import {
  ditherCacheKey,
  getCachedDither,
  hasCanvasSupport,
  proceduralAscii,
  renderDitherDataUrl,
  setCachedDither,
  type PaletteMode,
} from "../lib/dither";

export interface DitherCanvasProps {
  posterUrl: string | null;
  title: string;
  year: number | null;
  kind: Kind;
  /** Coarse render width. It is capped and then scaled up by CSS. */
  width: number;
  height: number;
  seed?: number;
  palette?: PaletteMode;
  className?: string;
  testId?: string;
}

type ArtState =
  | { mode: "loading" }
  | { mode: "ready"; url: string }
  | { mode: "ascii"; rows: string[] }
  | { mode: "unsupported" };

type IdleWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout?: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

/** Run work after the first paint, without blocking typing or scrolling. */
function scheduleIdle(callback: () => void): () => void {
  if (typeof window === "undefined") {
    callback();
    return () => {};
  }
  const idleWindow = window as IdleWindow;
  if (typeof idleWindow.requestIdleCallback === "function") {
    const handle = idleWindow.requestIdleCallback(callback, { timeout: 1000 });
    return () => idleWindow.cancelIdleCallback?.(handle);
  }
  const handle = window.setTimeout(callback, 0);
  return () => window.clearTimeout(handle);
}

function loadImage(url: string, crossOrigin: boolean, timeoutMs = 10_000): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) {
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
      if (error) {
        reject(error);
      } else {
        resolve(image);
      }
    };
    const timer = window.setTimeout(() => finish(new Error("image timed out")), timeoutMs);
    if (crossOrigin) {
      image.crossOrigin = "anonymous";
    }
    image.decoding = "async";
    image.onload = () => finish();
    image.onerror = () => finish(new Error("image failed"));
    image.src = url;
  });
}

interface LoadedSource {
  source: CanvasImageSource;
  width: number;
  height: number;
  close?: () => void;
}

/** Use an ImageBitmap when the browser has it, otherwise the decoded image element. */
async function loadSource(url: string, crossOrigin: boolean): Promise<LoadedSource> {
  const image = await loadImage(url, crossOrigin);
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(image);
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close(),
      };
    } catch {
      // Some browsers refuse to bitmap a CORS-tainted image; use the element.
    }
  }
  return {
    source: image,
    width: image.naturalWidth || 1,
    height: image.naturalHeight || 1,
  };
}

export function DitherCanvas({
  posterUrl,
  title,
  year,
  kind,
  width,
  height,
  seed,
  palette = kind,
  className = "",
  testId = "dither-canvas",
}: DitherCanvasProps) {
  const [state, setState] = useState<ArtState>({ mode: "loading" });

  useEffect(() => {
    let cancelled = false;
    const finish = (next: ArtState) => {
      if (!cancelled) {
        setState(next);
      }
    };
    const fallback = () => ({
      mode: "ascii" as const,
      rows: proceduralAscii(
        title,
        year,
        Math.max(12, Math.round(width / 8)),
        Math.max(3, Math.round(height / 14)),
        seed ?? 0,
      ),
    });

    setState({ mode: "loading" });

    const key = ditherCacheKey(posterUrl ?? "", width, height, palette, seed ?? 0);
    if (posterUrl) {
      const cached = getCachedDither(key);
      if (cached) {
        finish({ mode: "ready", url: cached });
        return () => {
          cancelled = true;
        };
      }
    }

    const run = async () => {
      if (!posterUrl) {
        finish(fallback());
        return;
      }
      if (!hasCanvasSupport()) {
        finish({ mode: "unsupported" });
        return;
      }

      const options = { width, height, palette, seed };
      const attempt = async (url: string, crossOrigin: boolean): Promise<string> => {
        const loaded = await loadSource(url, crossOrigin);
        try {
          return renderDitherDataUrl(loaded.source, loaded.width, loaded.height, options);
        } finally {
          loaded.close?.();
        }
      };

      try {
        const url = await attempt(posterUrl, true);
        if (!cancelled) {
          setCachedDither(key, url);
          finish({ mode: "ready", url });
        }
        return;
      } catch {
        // CORS-blocked hosts fail either the load or the pixel read. Retry same-origin.
      }

      if (cancelled) {
        return;
      }

      try {
        const proxy = `/api/posters/proxy?url=${encodeURIComponent(posterUrl)}`;
        const url = await attempt(proxy, false);
        if (!cancelled) {
          setCachedDither(key, url);
          finish({ mode: "ready", url });
        }
      } catch {
        finish(fallback());
      }
    };

    const cancel = scheduleIdle(() => {
      void run();
    });
    return () => {
      cancelled = true;
      cancel();
    };
  }, [posterUrl, title, year, width, height, palette, seed]);

  return (
    <div
      aria-hidden="true"
      data-testid={testId}
      data-dither-state={state.mode}
      data-dither-kind={kind}
      className={["pointer-events-none overflow-hidden", className].join(" ")}
    >
      {state.mode === "ready" ? (
        <div
          data-testid={`${testId}-image`}
          className="h-full w-full bg-cover bg-center [image-rendering:pixelated]"
          style={{ backgroundImage: `url("${state.url}")` }}
        />
      ) : state.mode === "ascii" ? (
        <pre
          data-testid="dither-fallback"
          className="dither-ascii h-full w-full overflow-hidden whitespace-pre p-1 text-micro leading-none"
        >
          {state.rows.join("\n")}
        </pre>
      ) : (
        <div data-testid={`${testId}-gradient`} className="dither-gradient h-full w-full" />
      )}
    </div>
  );
}
