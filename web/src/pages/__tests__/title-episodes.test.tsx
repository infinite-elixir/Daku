import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Episode, ReleaseItem, ReleasesResponse, TitleDetail } from "../../lib/api";
import { TitlePage } from "../TitlePage";

type TitleDetailShape = TitleDetail;

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
  },
  {
    id: "ep-s1e1",
    season: 1,
    episode: 1,
    label: "S01E01",
    absolute: null,
    name: "Winter Is Coming",
    air_date: "2011-04-17",
    status: "missing",
  },
  {
    id: "ep-s1e2",
    season: 1,
    episode: 2,
    label: "S01E02",
    absolute: null,
    name: "The Kingsroad",
    air_date: "2011-04-24",
    status: "missing",
  },
  {
    id: "ep-s2e1",
    season: 2,
    episode: 1,
    label: "S02E01",
    absolute: null,
    name: "The North Remembers",
    air_date: "2012-04-01",
    status: "missing",
  },
  {
    id: "ep-s2e5",
    season: 2,
    episode: 5,
    label: "S02E05",
    absolute: null,
    name: "The Ghost of Harrenhal",
    air_date: "2012-04-29",
    status: "missing",
  },
];

function makeTitle(overrides: Partial<TitleDetailShape> = {}): TitleDetailShape {
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
    overview: "Noble families vie for control of Westeros.",
    poster_url: null,
    banner_url: null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    availability: "none",
    saved_release_count: 0,
    episode_count: episodes.length,
    available_episode_count: 0,
    episodes,
    ...overrides,
  };
}

function releasesPayload(): ReleasesResponse {
  const release: ReleaseItem = {
    info_hash: "1111111111111111111111111111111111111111",
    filename: "Game.of.Thrones.S01E01.1080p.mkv",
    quality: "1080p",
    size_bytes: 2_000_000_000,
    seeders: 42,
    provider: "YTS",
    magnet:
      "magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=Game.of.Thrones.S01E01.1080p.mkv",
    metadata_json: {
      source: "WEB-DL",
      video_codec: "x265",
      audio_hint: "DDP",
      languages: ["en"],
      release_group: null,
      edition: null,
      resolved_from: "torrentio",
    },
    release_id: null,
    is_saved: false,
    dead: false,
    saved_at: null,
    last_checked_at: null,
    resolved_from: "torrentio",
  };
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
    total: 1,
    total_unfiltered: 1,
    returned: 1,
    duplicates_removed: 0,
    quality_counts: {},
    releases: [release],
  };
}

function jsonResponse(payload: unknown, status = 200) {
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

function renderTitle(entry: string, title: TitleDetailShape) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/releases/saved")) {
      return jsonResponse({
        title_id: "title-1",
        total: 0,
        episode_id: null,
        quality: [],
        dead: null,
        refresh: { attempted: 0, checked: 0, found: 0, missed: 0, stale: false, reason: null },
        releases: [],
      });
    }
    if (url.includes("/releases")) {
      return jsonResponse(releasesPayload());
    }
    if (url.startsWith("/api/titles/title-1")) {
      return jsonResponse(title);
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
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
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("TitlePage episodes", () => {
  beforeEach(() => {
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists every season as tabs with Specials last and starts fully collapsed", async () => {
    const fetchMock = renderTitle("/title/title-1", makeTitle());

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Season 01", "Season 02", "Specials"]);
    expect(screen.getByRole("tab", { name: "Season 01" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("button", { name: /^S01E01/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/releases?season=")),
    ).toBe(false);
  });

  it("expands one episode card at a time and shows its releases inside it", async () => {
    const fetchMock = renderTitle("/title/title-1", makeTitle());

    fireEvent.click(await screen.findByRole("button", { name: /^S01E01/ }));

    expect(await screen.findByText("Game.of.Thrones.S01E01.1080p.mkv")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^S01E01/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/title/title-1?season=1&episode=1",
    );
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

    const list = screen.getByText("Game.of.Thrones.S01E01.1080p.mkv").closest("ul");
    expect(list?.className).toContain("overflow-y-auto");

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles/title-1/releases?season=1&episode=1&sort=seeders&limit=20",
        expect.anything(),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /^S01E02/ }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/title/title-1?season=1&episode=2",
      );
    });
    expect(screen.getByRole("button", { name: /^S01E01/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("button", { name: /^S01E02/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles/title-1/releases?season=1&episode=2&sort=seeders&limit=20",
        expect.anything(),
      );
    });
  });

  it("collapses the open episode when it is clicked again", async () => {
    renderTitle("/title/title-1", makeTitle());

    const row = await screen.findByRole("button", { name: /^S01E01/ });
    fireEvent.click(row);
    expect(await screen.findByText("Game.of.Thrones.S01E01.1080p.mkv")).toBeInTheDocument();

    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.queryByText("Game.of.Thrones.S01E01.1080p.mkv")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/title/title-1");
  });

  it("opens the deep-linked episode card", async () => {
    renderTitle("/title/title-1?season=2&episode=5", makeTitle());

    expect(await screen.findByRole("tab", { name: "Season 02" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const row = screen.getByRole("button", { name: /^S02E05/ });
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("Game.of.Thrones.S01E01.1080p.mkv")).toBeInTheDocument();
  });

  it("falls back to the first normal episode for an invalid pair", async () => {
    renderTitle("/title/title-1?season=9&episode=9", makeTitle());

    const row = await screen.findByRole("button", { name: /^S01E01/ });
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tab", { name: "Season 01" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("opens Specials from their tab without auto-selecting them", async () => {
    renderTitle("/title/title-1", makeTitle());

    fireEvent.click(await screen.findByRole("tab", { name: "Specials" }));

    const special = await screen.findByRole("button", { name: /^S00E01/ });
    expect(special).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("location")).toHaveTextContent("/title/title-1");

    fireEvent.click(special);

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/title/title-1?season=0&episode=1",
      );
    });
    expect(special).toHaveAttribute("aria-expanded", "true");
  });

  it("supports arrow-key navigation between season tabs without expanding", async () => {
    renderTitle("/title/title-1", makeTitle());

    const first = await screen.findByRole("tab", { name: "Season 01" });
    fireEvent.keyDown(first, { key: "ArrowRight" });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Season 02" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/title/title-1");
  });

  it("falls back to the episode number when a name is missing", async () => {
    const episodes: Episode[] = [
      {
        id: "ep-s1e3",
        season: 1,
        episode: 3,
        label: "S01E03",
        absolute: 3,
        name: null,
        air_date: null,
        status: "missing",
      },
      {
        id: "ep-s1e4",
        season: 1,
        episode: 4,
        label: "S01E04",
        absolute: 4,
        name: "A Real Name",
        air_date: null,
        status: "missing",
      },
    ];
    renderTitle("/title/title-1", makeTitle({ episodes, episode_count: episodes.length }));

    expect(await screen.findByText("Episode 3")).toBeInTheDocument();
    expect(screen.getByText("A Real Name")).toBeInTheDocument();
    expect(screen.queryByText("Untitled episode")).not.toBeInTheDocument();
  });

  it("adds every season and Specials from the seasons payload", async () => {
    const episodes = EPISODES.filter((episode) => episode.season === 1);
    renderTitle(
      "/title/title-1",
      makeTitle({
        episodes,
        episode_count: episodes.length,
        seasons: [
          {
            library_season: 0,
            provider_season: 0,
            source: "cinemeta",
            anilist_id: null,
            episode_count: 1,
          },
          {
            library_season: 1,
            provider_season: 1,
            source: "cinemeta",
            anilist_id: null,
            episode_count: 2,
          },
          {
            library_season: 2,
            provider_season: 2,
            source: "cinemeta",
            anilist_id: null,
            episode_count: 3,
          },
        ],
      }),
    );

    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Season 01", "Season 02", "Specials"]);
  });

  it("shows an explanatory empty state when there are no episodes", async () => {
    const fetchMock = renderTitle("/title/title-1", makeTitle({ episodes: [] }));

    expect(await screen.findByText("No episodes recorded yet")).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: "Refresh metadata" })
        .some((button) => button.hasAttribute("disabled")),
    ).toBe(true);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/releases?season=")),
    ).toBe(false);
  });
});
