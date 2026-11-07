import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ContinueWatchingItem, ContinueWatchingResponse, MediaFileItem } from "../../lib/api";
import { ContinueWatching } from "../ContinueWatching";

function makeFile(overrides: Partial<MediaFileItem> = {}): MediaFileItem {
  return {
    id: overrides.id ?? "file-1",
    download_id: "download-1",
    episode_id: overrides.episode_id ?? "episode-1",
    episode_label: overrides.episode_label ?? "S01E01",
    episode_name: overrides.episode_name ?? "Pilot",
    season: overrides.season ?? 1,
    episode: overrides.episode ?? 1,
    file_name: overrides.file_name ?? "episode.mkv",
    size_bytes: overrides.size_bytes ?? 1024,
    is_primary: true,
    has_episode: overrides.has_episode ?? true,
    playback:
      overrides.playback === undefined
        ? { position_s: 120, duration_s: 600, updated_at: "2026-11-01T10:00:00Z" }
        : overrides.playback,
  };
}

function makeItem(overrides: Partial<ContinueWatchingItem> = {}): ContinueWatchingItem {
  return {
    title_id: overrides.title_id ?? "title-1",
    title: overrides.title ?? "Example Show",
    kind: overrides.kind ?? "series",
    poster_url: overrides.poster_url ?? "https://images.example/poster.jpg",
    played_at: overrides.played_at ?? "2026-11-01T10:00:00Z",
    up_next: overrides.up_next ?? false,
    is_new: overrides.is_new ?? false,
    file: overrides.file ?? makeFile(),
  };
}

function renderStrip(items: ContinueWatchingItem[]) {
  const payload: ContinueWatchingResponse = { items };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ContinueWatching />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ContinueWatching", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when there is nothing in progress", () => {
    const { container } = renderStrip([]);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("continue-watching")).not.toBeInTheDocument();
  });

  it("renders a horizontal strip with the title name and file thumbnail", async () => {
    renderStrip([
      makeItem({
        file: makeFile({ id: "file-1", episode_label: "S01E03", file_name: "s01e03.mkv" }),
      }),
    ]);

    expect(await screen.findByRole("heading", { name: "Continue watching" })).toBeInTheDocument();
    expect(screen.getByText("Example Show")).toBeInTheDocument();
    expect(screen.getByText("S01E03")).toBeInTheDocument();
    expect(screen.getByTestId("media-file-card-file-1")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Continue watching files" })).toBeInTheDocument();
    expect(screen.getByText("1 title")).toBeInTheDocument();

    const image = document.querySelector('img[src*="/thumbnail"]');
    expect(image).toHaveAttribute("src", "/api/media-files/file-1/thumbnail");
  });

  it("marks a next-episode card as Up next without a seek bar", async () => {
    renderStrip([
      makeItem({
        up_next: true,
        file: makeFile({ id: "file-next", episode_label: "S01E04", playback: null }),
      }),
    ]);

    expect(await screen.findByText("Up next")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-progress-file-next")).not.toBeInTheDocument();
  });

  it("marks a freshly downloaded, never-played file as New", async () => {
    renderStrip([
      makeItem({
        played_at: null,
        is_new: true,
        file: makeFile({ id: "file-new", episode_label: "S02E03", playback: null }),
      }),
    ]);

    expect(await screen.findByText("New")).toBeInTheDocument();
    expect(screen.getByText("S02E03")).toBeInTheDocument();
  });

  it("links a card to the title's files page when playback is disabled", async () => {
    renderStrip([
      makeItem({
        title_id: "title-9",
        file: makeFile({ id: "file-9" }),
      }),
    ]);

    const link = await screen.findByTestId("media-file-card-file-9");
    expect(link).toHaveAttribute("href", "/title/title-9/files#media-file-file-9");
  });

  it("shows one card per returned title", async () => {
    renderStrip([
      makeItem({ title_id: "a", title: "First", file: makeFile({ id: "file-a" }) }),
      makeItem({ title_id: "b", title: "Second", file: makeFile({ id: "file-b" }) }),
    ]);

    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(screen.getByText("2 titles")).toBeInTheDocument();
  });

  it("bleeds the artwork to the card border like a library card", async () => {
    renderStrip([makeItem({ file: makeFile({ id: "bleed-1" }) })]);

    const card = await screen.findByTestId("media-file-card-bleed-1");
    expect(card).toHaveClass("overflow-hidden");
    expect(card.className).not.toContain("p-panel-compact");

    const art = card.querySelector(".aspect-video");
    expect(art).not.toBeNull();
    expect(art).not.toHaveClass("rounded-panel");
  });
});
