import { useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { DownloadRecord, DownloadsResponse } from "../lib/api";

export const DOWNLOADS_QUERY_KEY = ["downloads"] as const;

const POLL_INTERVAL_MS = 3000;
const FAILURES_BEFORE_POLLING = 2;
const DOWNLOAD_EVENTS = [
  "download.created",
  "download.updated",
  "organize.completed",
  "system.health",
] as const;

export interface EventStreamState {
  connected: boolean;
  polling: boolean;
}

/** Merge one download payload into the cached list without touching other rows. */
export function applyDownloadPayload(
  current: DownloadsResponse | undefined,
  payload: DownloadRecord,
): DownloadsResponse | undefined {
  if (!current) {
    return current;
  }
  let found = false;
  const items = current.items.map((item) => {
    if (item.id !== payload.id) {
      return item;
    }
    found = true;
    return { ...item, ...payload };
  });
  if (!found) {
    return { items: [payload, ...items], total: current.total + 1 };
  }
  return { ...current, items };
}

function removeDownload(
  current: DownloadsResponse | undefined,
  payload: { id?: unknown },
): DownloadsResponse | undefined {
  if (!current || typeof payload.id !== "string") {
    return current;
  }
  const items = current.items.filter((item) => item.id !== payload.id);
  return items.length === current.items.length ? current : { ...current, items, total: items.length };
}

function updateCache(queryClient: QueryClient, setter: (current: DownloadsResponse | undefined) => DownloadsResponse | undefined) {
  queryClient.setQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY, (current) => setter(current));
}

/**
 * Subscribe the TanStack Query cache to `/api/events`.
 *
 * `EventSource` reconnects by itself. If it fails twice, or is unavailable in
 * the browser, this hook falls back to invalidating the downloads query every
 * three seconds. There is no second store: the cache is the only client state.
 */
export function useEventSource(url = "/api/events"): EventStreamState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<EventStreamState>({ connected: false, polling: false });

  useEffect(() => {
    let source: EventSource | null = null;
    let failures = 0;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let disposed = false;

    const refetch = () => {
      void queryClient.invalidateQueries({ queryKey: DOWNLOADS_QUERY_KEY });
    };

    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const startPolling = () => {
      if (disposed || pollTimer !== null) {
        return;
      }
      setState({ connected: false, polling: true });
      refetch();
      pollTimer = setInterval(refetch, POLL_INTERVAL_MS);
    };

    const handlePayload = (event: MessageEvent<string>) => {
      failures = 0;
      let payload: unknown;
      try {
        payload = JSON.parse(event.data) as unknown;
      } catch {
        return;
      }
      if (typeof payload !== "object" || payload === null) {
        return;
      }
      const record = payload as DownloadRecord;
      if (typeof record.id !== "string") {
        return;
      }
      updateCache(queryClient, (current) => applyDownloadPayload(current, record));
    };

    const handleRemoved = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as { id?: unknown };
        updateCache(queryClient, (current) => removeDownload(current, payload));
      } catch {
        refetch();
      }
    };

    if (typeof EventSource === "undefined") {
      startPolling();
      return () => {
        disposed = true;
        stopPolling();
      };
    }

    const connect = () => {
      source = new EventSource(url);
      source.onopen = () => {
        failures = 0;
        stopPolling();
        setState({ connected: true, polling: false });
      };
      source.onerror = () => {
        failures += 1;
        setState((previous) => ({ ...previous, connected: false }));
        if (failures >= FAILURES_BEFORE_POLLING && source !== null) {
          source.close();
          source = null;
          startPolling();
        }
      };
      for (const name of DOWNLOAD_EVENTS) {
        source.addEventListener(name, handlePayload as EventListener);
      }
      source.addEventListener("download.removed", handleRemoved as EventListener);
      source.addEventListener("resync", refetch as EventListener);
      source.addEventListener("heartbeat", () => {
        failures = 0;
      });
    };

    connect();

    return () => {
      disposed = true;
      stopPolling();
      source?.close();
      source = null;
    };
  }, [queryClient, url]);

  return state;
}
