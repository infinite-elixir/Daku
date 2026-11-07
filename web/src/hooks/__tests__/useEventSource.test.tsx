import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DOWNLOADS_QUERY_KEY, useEventSource } from "../useEventSource";
import type { DownloadRecord, DownloadsResponse } from "../../lib/api";

type Listener = (event: MessageEvent<string>) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener as Listener);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.();
  }

  fail(): void {
    this.onerror?.();
  }

  emit(type: string, payload: unknown): void {
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  emitRaw(type: string): void {
    const event = { data: "" } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function record(overrides: Partial<DownloadRecord> = {}): DownloadRecord {
  return {
    id: "d1",
    release_id: "r1",
    source: "torrent",
    status: "active",
    engine: "qbittorrent",
    external_id: "hash",
    progress: 0.1,
    downloaded_bytes: 100,
    total_bytes: 1000,
    speed_bps: 50,
    eta_seconds: 18,
    error: null,
    created_at: "2026-10-25T00:00:00Z",
    updated_at: "2026-10-25T00:00:00Z",
    title_id: "t1",
    title: "Dune",
    episode_id: null,
    episode_label: null,
    quality: "1080p",
    filename: "Dune.mkv",
    ...overrides,
  };
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useEventSource(), { wrapper });
  return { queryClient, ...view };
}

function seed(queryClient: QueryClient): { first: DownloadRecord; second: DownloadRecord } {
  const first = record({ id: "d1", progress: 0.1 });
  const second = record({ id: "d2", progress: 0.4 });
  const initial: DownloadsResponse = { items: [first, second], total: 2 };
  queryClient.setQueryData(DOWNLOADS_QUERY_KEY, initial);
  return { first, second };
}

describe("useEventSource", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("updates only the changed row in the query cache", () => {
    const { queryClient } = setup();
    const { second } = seed(queryClient);
    const source = FakeEventSource.instances[0]!;

    act(() => source.open());
    act(() => source.emit("download.updated", record({ id: "d1", progress: 0.82 })));

    const cached = queryClient.getQueryData<DownloadsResponse>(DOWNLOADS_QUERY_KEY);
    expect(cached?.items[0]?.progress).toBe(0.82);
    expect(cached?.items[1]).toBe(second);
  });

  it("refreshes title and library views when a download finishes", () => {
    const { queryClient } = setup();
    seed(queryClient);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const source = FakeEventSource.instances[0]!;

    act(() =>
      source.emit(
        "download.updated",
        record({
          status: "complete",
          progress: 1,
          downloaded_files: [
            {
              id: "f1",
              episode_id: "e5",
              episode_label: "S01E05",
              file_name: "S01E05.mkv",
              size_bytes: 100,
              is_primary: true,
            },
          ],
        }),
      ),
    );

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["title"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["title-files"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["library"] });
  });

  it("does not refetch the library for an in-progress update", () => {
    const { queryClient } = setup();
    seed(queryClient);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const source = FakeEventSource.instances[0]!;

    act(() => source.emit("download.updated", record({ progress: 0.5 })));

    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["title"] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["title-files"] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["library"] });
  });

  it("refetches on a resync event", () => {
    const { queryClient } = setup();
    seed(queryClient);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const source = FakeEventSource.instances[0]!;

    act(() => source.emitRaw("resync"));

    expect(invalidate).toHaveBeenCalledWith({ queryKey: DOWNLOADS_QUERY_KEY });
  });

  it("falls back to three-second polling after two failures", () => {
    vi.useFakeTimers();
    const { queryClient, result } = setup();
    seed(queryClient);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const source = FakeEventSource.instances[0]!;

    act(() => source.fail());
    act(() => source.fail());

    expect(source.closed).toBe(true);
    expect(result.current.polling).toBe(true);
    const calls = invalidate.mock.calls.length;

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(invalidate.mock.calls.length).toBeGreaterThan(calls);
  });

  it("polls immediately when EventSource is unavailable", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", undefined);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seed(queryClient);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useEventSource(), { wrapper });

    expect(result.current.polling).toBe(true);
    expect(invalidate).toHaveBeenCalled();

    const calls = invalidate.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(invalidate.mock.calls.length).toBeGreaterThan(calls);
  });

  it("reports connected after the stream opens", () => {
    const { result } = setup();
    const source = FakeEventSource.instances[0]!;

    act(() => source.open());

    expect(result.current.connected).toBe(true);
    expect(result.current.polling).toBe(false);
  });
});
