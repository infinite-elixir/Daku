import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DownloadRecord, DownloadsResponse } from "../../lib/api";
import { DownloadsPage } from "../DownloadsPage";

type Listener = (event: MessageEvent<string>) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Listener[]>();

  constructor() {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener as Listener);
    this.listeners.set(type, list);
  }

  close(): void {}

  emit(type: string, payload: unknown): void {
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>;
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
    external_id: "a".repeat(40),
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

function stubDownloads(items: DownloadRecord[]): void {
  const payload: DownloadsResponse = { items, total: items.length };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    }),
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DownloadsPage />
    </QueryClientProvider>,
  );
}

describe("DownloadsPage qBittorrent 5 progress", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a progress bar and advances it on a download.updated event", async () => {
    stubDownloads([record({ progress: 0.1 })]);
    renderPage();

    const bar = await screen.findByRole("progressbar", { name: /Dune progress/i });
    expect(bar).toHaveAttribute("aria-valuenow", "10");

    act(() => {
      FakeEventSource.instances[0]!.emit(
        "download.updated",
        record({ progress: 0.7, downloaded_bytes: 700 }),
      );
    });

    await waitFor(() => expect(bar).toHaveAttribute("aria-valuenow", "70"));
  });

  it("never renders a raw add response as an error", async () => {
    stubDownloads([record({ status: "failed", error: "qBittorrent rejected the torrent" })]);
    renderPage();

    expect(await screen.findByText("qBittorrent rejected the torrent")).toBeInTheDocument();
    expect(screen.queryByText(/success_count/)).not.toBeInTheDocument();
    expect(screen.queryByText(/added_torrent_ids/)).not.toBeInTheDocument();
  });
});
