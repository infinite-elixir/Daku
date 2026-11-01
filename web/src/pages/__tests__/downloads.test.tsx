import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <DownloadsPage />
      </QueryClientProvider>
    </MemoryRouter>,
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

  it("removes a download optimistically", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    const payload: DownloadsResponse = { items: [record()], total: 1 };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method ?? "GET" });
        if (init?.method === "DELETE") {
          return { ok: true, status: 204, json: async () => ({}) };
        }
        return { ok: true, status: 200, json: async () => payload };
      }),
    );
    renderPage();

    await screen.findByTestId("download-row-d1");
    expect(screen.getByTestId("download-row-d1")).toHaveClass("p-panel");
    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.queryByTestId("download-row-d1")).not.toBeInTheDocument());
    expect(calls).toContainEqual({
      url: "/api/downloads/d1?delete_files=false",
      method: "DELETE",
    });
  });

  it("restores the row and shows the API message when removal fails", async () => {
    const payload: DownloadsResponse = { items: [record()], total: 1 };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return {
            ok: false,
            status: 502,
            json: async () => ({ code: "QBT_UNREACHABLE", message: "qBittorrent is unreachable" }),
          };
        }
        return { ok: true, status: 200, json: async () => payload };
      }),
    );
    renderPage();

    await screen.findByTestId("download-row-d1");
    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("qBittorrent is unreachable");
    expect(screen.getByTestId("download-row-d1")).toBeInTheDocument();
  });
});

describe("DownloadsPage title groups", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("links a title group to its downloaded files", async () => {
    stubDownloads([
      record({
        id: "d1",
        status: "complete",
        progress: 1,
        downloaded_files: [
          {
            id: "f1",
            episode_id: "e1",
            episode_label: "S01E01",
            file_name: "Dune.mkv",
            size_bytes: 2048,
            is_primary: true,
          },
          {
            id: "f2",
            episode_id: "e2",
            episode_label: "S01E02",
            file_name: "Dune.part2.mkv",
            size_bytes: 1024,
            is_primary: false,
          },
        ],
      }),
    ]);

    renderPage();

    expect(await screen.findByRole("link", { name: "Files (2)" })).toHaveAttribute(
      "href",
      "/title/t1/files",
    );
    expect(screen.getByRole("link", { name: "Dune" })).toHaveAttribute("href", "/title/t1");
  });
});
