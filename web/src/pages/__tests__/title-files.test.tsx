import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MediaFileItem, TitleDetail, TitleFilesResponse } from "../../lib/api";
import { TitleFilesPage } from "../TitleFilesPage";

function makeTitle(overrides: Partial<TitleDetail> = {}): TitleDetail {
  return {
    id: "t1",
    kind: "series",
    title: "Cyberpunk: Edgerunners",
    year: 2022,
    imdb_id: "tt12590266",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: "https://images.example/poster.jpg",
    banner_url: null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    availability: "complete",
    saved_release_count: 0,
    episode_count: 0,
    available_episode_count: 0,
    episodes: [],
    ...overrides,
  };
}

function makeFile(overrides: Partial<MediaFileItem> = {}): MediaFileItem {
  return {
    id: overrides.id ?? "f1",
    download_id: "d1",
    episode_id: overrides.episode_id ?? "e1",
    episode_label: overrides.episode_label ?? "S01E01",
    episode_name: overrides.episode_name ?? "Let You Down",
    season: overrides.season ?? 1,
    episode: overrides.episode ?? 1,
    file_name: overrides.file_name ?? "s01e01.mkv",
    size_bytes: overrides.size_bytes ?? 1024,
    is_primary: true,
    has_episode: overrides.has_episode ?? true,
  };
}

function makeFiles(items: MediaFileItem[], seasons = [
  { library_season: 1, file_count: 1, total_size_bytes: 1024 },
]): TitleFilesResponse {
  return {
    title_id: "t1",
    file_count: items.length,
    total_size_bytes: items.reduce((total, file) => total + (file.size_bytes ?? 0), 0),
    seasons,
    items,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

interface FetchStub {
  title?: TitleDetail;
  files?: TitleFilesResponse;
  filesStatus?: number;
  filesMessage?: string;
}

function stubFetch({ title = makeTitle(), files, filesStatus = 200, filesMessage }: FetchStub) {
  return vi.fn(async (url: string) => {
    if (url.endsWith("/files")) {
      if (filesStatus >= 400) {
        return jsonResponse({ code: "ERROR", message: filesMessage ?? "Files failed" }, filesStatus);
      }
      return jsonResponse(files ?? makeFiles([makeFile()]));
    }
    return jsonResponse(title);
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/title/t1/files"]}>
        <Routes>
          <Route path="/title/:id/files" element={<TitleFilesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  callback: IntersectionObserverCallback;
  elements: Element[] = [];

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeIntersectionObserver.instances.push(this);
  }

  observe(element: Element) {
    this.elements.push(element);
  }

  disconnect() {}
  unobserve() {}

  intersections(isIntersecting = true) {
    this.callback(
      this.elements.map(
        (target) => ({ target, isIntersecting }) as IntersectionObserverEntry,
      ),
      this as unknown as IntersectionObserver,
    );
  }
}

describe("TitleFilesPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeIntersectionObserver.instances = [];
  });

  it("renders season tabs and one grid for the newest season", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        files: makeFiles(
          [
            makeFile({ id: "s1e1", season: 1, episode_label: "S01E01", file_name: "s01e01.mkv" }),
            makeFile({ id: "s2e1", season: 2, episode_label: "S02E01", file_name: "s02e01.mkv" }),
          ],
          [
            { library_season: 1, file_count: 1, total_size_bytes: 1024 },
            { library_season: 2, file_count: 1, total_size_bytes: 1024 },
          ],
        ),
      }),
    );

    const { container } = renderPage();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Cyberpunk: Edgerunners" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 files · 2.00 KB")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Season 1" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByRole("tab", { name: "Season 2" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("media-file-card-s2e1")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-card-s1e1")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Season 1" }));
    expect(screen.getByTestId("media-file-card-s1e1")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-card-s2e1")).not.toBeInTheDocument();

    // The mpv decision replaces Jellyfin playback: no stream URL may render.
    expect(container.innerHTML).not.toContain("/Videos/");
  });

  it("renders an Extras group after the season grid", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        files: makeFiles([
          makeFile({ id: "ep", season: 1 }),
          makeFile({
            id: "extra",
            season: null,
            episode: null,
            episode_id: null,
            episode_label: null,
            episode_name: null,
            file_name: "making-of.mkv",
            has_episode: false,
          }),
        ]),
      }),
    );

    renderPage();

    await screen.findByTestId("media-file-card-extra");
    expect(screen.getByRole("heading", { name: "Extras" })).toBeInTheDocument();
    expect(screen.getByTestId("media-file-card-ep")).toBeInTheDocument();
  });

  it("shows every movie file without season tabs", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        title: makeTitle({ kind: "movie", title: "Big Buck Bunny" }),
        files: {
          title_id: "t1",
          file_count: 1,
          total_size_bytes: 1024,
          seasons: [],
          items: [
            makeFile({
              id: "movie",
              season: null,
              episode: null,
              episode_id: null,
              episode_label: null,
              episode_name: null,
              file_name: "Big Buck Bunny.mkv",
              has_episode: false,
            }),
          ],
        },
      }),
    );

    renderPage();

    expect(await screen.findByTestId("media-file-card-movie")).toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "File seasons" })).not.toBeInTheDocument();
    expect(screen.queryByText("Extras")).not.toBeInTheDocument();
  });

  it("renders an empty state with a link back to the title", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({ files: { ...makeFiles([]), seasons: [] } }),
    );

    renderPage();

    expect(await screen.findByText("No files yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the title" })).toHaveAttribute(
      "href",
      "/title/t1",
    );
  });

  it("renders the error state and retries", async () => {
    const fetchMock = stubFetch({ filesStatus: 500, filesMessage: "Files are unavailable" });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("Files are unavailable")).toBeInTheDocument();
    const before = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/files")).length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      const after = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/files")).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("only requests thumbnails for cards that become visible", async () => {
    vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
    vi.stubGlobal(
      "fetch",
      stubFetch({
        files: makeFiles([
          makeFile({ id: "a", season: 1, episode: 1 }),
          makeFile({ id: "b", season: 1, episode: 2, episode_label: "S01E02" }),
        ]),
      }),
    );

    const { container } = renderPage();

    await screen.findByTestId("media-file-card-a");
    expect(container.querySelectorAll('img[src*="/thumbnail"]')).toHaveLength(0);

    expect(FakeIntersectionObserver.instances.length).toBe(2);
    for (const observer of FakeIntersectionObserver.instances) {
      observer.intersections(true);
    }

    await waitFor(() => {
      expect(container.querySelectorAll('img[src*="/thumbnail"]')).toHaveLength(2);
    });
  });
});
