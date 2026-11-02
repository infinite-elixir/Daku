import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Episode,
  LibraryResponse,
  PreviewTitle,
  ReleasesResponse,
  TitleDetail,
  TitleSummary,
  WatchStatus,
} from "../../lib/api";
import { LibraryPage } from "../LibraryPage";
import { PreviewPage } from "../PreviewPage";
import { TitlePage } from "../TitlePage";

const EPISODES: Episode[] = [
  {
    id: "ep-s0e1",
    season: 0,
    episode: 1,
    label: "S00E01",
    absolute: null,
    name: "Special",
    air_date: null,
    status: "missing",
    watched: false,
  },
  {
    id: "ep-s1e1",
    season: 1,
    episode: 1,
    label: "S01E01",
    absolute: null,
    name: "Winter Is Coming",
    air_date: null,
    status: "missing",
    watched: true,
  },
  {
    id: "ep-s1e2",
    season: 1,
    episode: 2,
    label: "S01E02",
    absolute: null,
    name: "The Kingsroad",
    air_date: null,
    status: "missing",
    watched: true,
  },
  {
    id: "ep-s1e5",
    season: 1,
    episode: 5,
    label: "S01E05",
    absolute: null,
    name: "The Wolf and the Lion",
    air_date: null,
    status: "missing",
    watched: false,
  },
];

function makeTitle(overrides: Partial<TitleDetail> = {}): TitleDetail {
  const episodes = overrides.episodes ?? EPISODES;
  return {
    id: "title-1",
    kind: "series",
    title: "Game of Thrones",
    year: 2011,
    imdb_id: "tt0944947",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: null,
    banner_url: null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    watch_status: "watching",
    status_changed_at: "2026-10-23T10:00:00",
    watched_episode_count: 2,
    watchable_episode_count: 3,
    next_episode: { season: 1, episode: 5, name: "The Wolf and the Lion" },
    availability: "none",
    saved_release_count: 0,
    episode_count: episodes.length,
    available_episode_count: 0,
    episodes,
    ...overrides,
  };
}

function emptyReleases(): ReleasesResponse {
  return {
    title_id: "title-1",
    type: "series",
    id: "tt0944947",
    episode_id: null,
    stale: false,
    cached_at: null,
    sort: "seeders",
    quality_filter: [],
    providers: [],
    min_quality: null,
    limit: 20,
    total: 0,
    total_unfiltered: 0,
    returned: 0,
    duplicates_removed: 0,
    quality_counts: {},
    releases: [],
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function LocationSpy() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderWithProviders(children: ReactNode, entry = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderTitlePage(initial: TitleDetail) {
  let current = initial;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/metadata")) {
      return jsonResponse({ stale: false });
    }
    if (url.includes("/releases/saved")) {
      return jsonResponse({
        title_id: initial.id,
        total: 0,
        episode_id: null,
        quality: [],
        dead: null,
        refresh: { attempted: 0, checked: 0, found: 0, missed: 0, stale: false, reason: null },
        releases: [],
      });
    }
    if (url.includes("/releases")) {
      return jsonResponse(emptyReleases());
    }
    if (url.match(/\/api\/titles\/[^/]+\/episodes\//) && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as { watched: boolean };
      const episodeId = url.split("/").at(-1);
      current = {
        ...current,
        episodes: current.episodes.map((episode) =>
          episode.id === episodeId ? { ...episode, watched: body.watched } : episode,
        ),
      };
      return jsonResponse(current);
    }
    if (url === `/api/titles/${initial.id}` && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as {
        watch_status?: WatchStatus | null;
        clear_progress?: boolean;
      };
      current = {
        ...current,
        watch_status: body.watch_status ?? null,
        status_changed_at: "2026-10-24T10:00:00",
        ...(body.clear_progress
          ? {
              watched_episode_count: 0,
              episodes: current.episodes.map((episode) => ({ ...episode, watched: false })),
            }
          : {}),
      };
      return jsonResponse(current);
    }
    if (url.startsWith(`/api/titles/${initial.id}`)) {
      return jsonResponse(current);
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  renderWithProviders(
    <Routes>
      <Route
        path="/title/:id"
        element={
          <>
            <TitlePage />
            <LocationSpy />
          </>
        }
      />
    </Routes>,
    `/title/${initial.id}`,
  );
  return fetchMock;
}

function stubLibrary(items: TitleSummary[]) {
  const payload: LibraryResponse = { items, total: items.length, limit: 100, offset: 0 };
  const fetchMock = vi.fn(async (url: string) => {
    if (url === "/api/continue-watching") {
      return jsonResponse({ items: [] });
    }
    return jsonResponse(payload);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeSummary(overrides: Partial<TitleSummary> = {}): TitleSummary {
  return {
    id: overrides.id ?? "title-1",
    kind: overrides.kind ?? "series",
    title: overrides.title ?? "Game of Thrones",
    year: 2011,
    imdb_id: "tt0944947",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: null,
    banner_url: null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    watch_status: overrides.watch_status ?? "watching",
    watched_episode_count: overrides.watched_episode_count ?? 2,
    watchable_episode_count: overrides.watchable_episode_count ?? 4,
    availability: "none",
    saved_release_count: 0,
    file_count: 0,
    has_files: false,
    ...overrides,
  };
}

describe("watch status UI", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("changes a movie status from the header control", async () => {
    const fetchMock = renderTitlePage(makeTitle({ kind: "movie", episodes: [] }));

    const select = await screen.findByLabelText("Watch status");
    expect(select).toHaveValue("watching");

    fireEvent.change(select, { target: { value: "completed" } });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles/title-1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ watch_status: "completed", clear_progress: false }),
        }),
      );
    });
  });

  it("shows progress and next up for a watching series", async () => {
    renderTitlePage(makeTitle());

    expect(await screen.findByText("2 / 3 watched")).toBeInTheDocument();
    expect(screen.getByText("Next up: S01E05")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Episode watch progress" })).toHaveAttribute(
      "aria-valuenow",
      "2",
    );
  });

  it("toggles an episode without selecting the row", async () => {
    const fetchMock = renderTitlePage(makeTitle());

    const checkbox = await screen.findByLabelText("Mark S01E05 watched");
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles/title-1/episodes/ep-s1e5",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ watched: true }),
        }),
      );
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/title/title-1");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/releases?season=")),
    ).toBe(false);
  });

  it("marks a whole season watched from the season header menu", async () => {
    const fetchMock = renderTitlePage(
      makeTitle({
        episodes: EPISODES.map((episode) => ({ ...episode, watched: false })),
        watched_episode_count: 0,
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Season 1 actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Mark season watched" }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls).toContain("/api/titles/title-1/episodes/ep-s1e1");
      expect(urls).toContain("/api/titles/title-1/episodes/ep-s1e2");
      expect(urls).toContain("/api/titles/title-1/episodes/ep-s1e5");
      expect(urls).not.toContain("/api/titles/title-1/episodes/ep-s0e1");
    });
  });

  it("composes library status chips with the other filters", async () => {
    const fetchMock = stubLibrary([makeSummary()]);
    renderWithProviders(<LibraryPage />, "/library");

    await screen.findByText("Game of Thrones");
    fireEvent.click(screen.getByRole("button", { name: "Watching" }));
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("button", { name: "Completed" }));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([url]) => String(url));
      const last = calls.at(-1) ?? "";
      expect(last).toContain("status=watching%2Ccompleted");
    });
    expect(screen.getByRole("button", { name: "Watching" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Completed" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows status and progress on library cards", async () => {
    stubLibrary([makeSummary()]);
    renderWithProviders(<LibraryPage />, "/library");

    expect(await screen.findByText("2 / 4 watched")).toBeInTheDocument();
    expect(screen.getAllByText("Watching").length).toBeGreaterThan(0);
  });

  it("never shows watch controls on a preview", async () => {
    const preview: PreviewTitle = {
      id: null,
      source: "imdb",
      source_id: "tt0944947",
      library_title_id: "title-1",
      stale: false,
      kind: "series",
      title: "Game of Thrones",
      year: 2011,
      imdb_id: "tt0944947",
      tmdb_id: null,
      tvdb_id: null,
      anilist_id: null,
      mal_id: null,
      overview: null,
      poster_url: null,
      banner_url: null,
      folder_name: null,
      wanted: false,
      watch_status: "watching",
      availability: "none",
      saved_release_count: 0,
      episode_count: 0,
      available_episode_count: 0,
      episodes: [],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(preview)));

    renderWithProviders(
      <Routes>
        <Route path="/preview/:kind/:source/:sourceId" element={<PreviewPage />} />
      </Routes>,
      "/preview/series/imdb/tt0944947",
    );

    expect(await screen.findByText("In library: Watching")).toBeInTheDocument();
    expect(screen.queryByLabelText("Watch status")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
