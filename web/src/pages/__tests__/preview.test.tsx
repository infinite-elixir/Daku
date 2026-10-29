import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PreviewTitle, TitleDetail } from "../../lib/api";
import { PreviewPage } from "../PreviewPage";

function makePreview(overrides: Partial<PreviewTitle> = {}): PreviewTitle {
  return {
    id: null,
    source: "imdb",
    source_id: "tt0944947",
    library_title_id: null,
    stale: false,
    kind: "series",
    title: "Game of Thrones",
    year: 2011,
    imdb_id: "tt0944947",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: "Noble families vie for control of Westeros.",
    poster_url: "https://images.example/got.jpg",
    banner_url: null,
    folder_name: null,
    wanted: false,
    availability: "none",
    saved_release_count: 0,
    episode_count: 2,
    available_episode_count: 0,
    episodes: [
      {
        id: "preview:1:1",
        season: 1,
        episode: 1,
        label: "S01E01",
        absolute: null,
        name: "Winter Is Coming",
        air_date: "2011-04-17",
        status: "missing",
      },
      {
        id: "preview:1:2",
        season: 1,
        episode: 2,
        label: "S01E02",
        absolute: null,
        name: "The Kingsroad",
        air_date: "2011-04-24",
        status: "missing",
      },
    ],
    ...overrides,
  };
}

function addedTitle(): TitleDetail {
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
    availability: "none",
    saved_release_count: 0,
    episode_count: 2,
    available_episode_count: 0,
    episodes: [],
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function renderPreview(initialEntry: string | { pathname: string; state: unknown }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/preview/:kind/:source/:sourceId" element={<PreviewPage />} />
          <Route path="/title/:id" element={<div>Title destination</div>} />
          <Route path="/search" element={<div>Search destination</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PreviewPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows metadata and adds the title, then lands on the title page", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/metadata/preview")) {
        return jsonResponse(makePreview());
      }
      if (url === "/api/titles" && init?.method === "POST") {
        return jsonResponse(addedTitle(), 201);
      }
      if (url === "/api/titles/title-1") {
        return jsonResponse(addedTitle());
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPreview("/preview/series/imdb/tt0944947");

    expect(await screen.findByText("Game of Thrones")).toBeInTheDocument();
    expect(screen.getByText("2011 · tt0944947")).toBeInTheDocument();
    expect(screen.getByText(/Noble families/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Season 01" })).toBeInTheDocument();
    expect(screen.getByText("Winter Is Coming")).toBeInTheDocument();
    expect(screen.getByText("The Kingsroad")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/titles");
    const body = JSON.parse((call?.[1] as RequestInit).body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      source: "imdb",
      source_id: "tt0944947",
      kind: "series",
      metadata_source: "cinemeta",
    });

    expect(await screen.findByText("Title destination")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/releases"))).toBe(false);
  });

  it("uses the same hero without library counters or library actions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          makePreview({
            saved_release_count: 4,
            available_episode_count: 2,
          }),
        ),
      ),
    );

    renderPreview("/preview/series/imdb/tt0944947");

    expect(await screen.findByTestId("title-hero")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Game of Thrones" })).toBeInTheDocument();
    expect(screen.queryByTestId("title-counters")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark wanted" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fix metadata" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to library" })).toBeInTheDocument();
  });

  it("shows Open when the title is already in the library", async () => {    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(makePreview({ library_title_id: "lib-1", id: "lib-1" }))),
    );

    renderPreview("/preview/series/imdb/tt0944947");

    const open = await screen.findByRole("link", { name: "Open" });
    expect(open).toHaveAttribute("href", "/title/lib-1");
    expect(screen.queryByRole("button", { name: "Add to library" })).not.toBeInTheDocument();
  });

  it("falls back to the search candidate when metadata is unreachable", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/metadata/preview")) {
        return jsonResponse({ code: "METADATA_UNAVAILABLE", message: "down" }, 502);
      }
      if (url === "/api/titles" && init?.method === "POST") {
        return jsonResponse(addedTitle(), 201);
      }
      return jsonResponse(addedTitle());
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPreview({
      pathname: "/preview/series/imdb/tt0944947",
      state: { candidate: { source: "imdb", source_id: "tt0944947", kind: "series", title: "Game of Thrones", year: 2011, poster_url: null } },
    });

    expect(await screen.findByText(/Metadata is unreachable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to library" })).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("shows the classification notice with a fixer link for a library series", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(
          makePreview({
            anime: true,
            anilist_id: 154587,
            library_title_id: "lib-1",
            id: "lib-1",
            classification: {
              is_anime: true,
              confidence: "exact",
              reason: "AniList exact match",
              suggested_anilist_id: 154587,
            },
          }),
        ),
      ),
    );

    renderPreview("/preview/series/imdb/tt0944947");

    expect(await screen.findByText(/This looks like anime/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix the classification?" })).toHaveAttribute(
      "href",
      "/title/lib-1?fix=classification",
    );
  });

  it("labels the Add action as anime and sends the anime hint", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/metadata/preview")) {
        return jsonResponse(
          makePreview({
            anime: true,
            anilist_id: 154587,
            classification: {
              is_anime: true,
              confidence: "exact",
              reason: "AniList exact match",
              suggested_anilist_id: 154587,
            },
          }),
        );
      }
      if (url === "/api/titles" && init?.method === "POST") {
        return jsonResponse(addedTitle(), 201);
      }
      return jsonResponse(addedTitle());
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPreview("/preview/series/imdb/tt0944947");

    fireEvent.click(await screen.findByRole("button", { name: "Add as anime" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === "/api/titles");
      expect(call).toBeTruthy();
    });
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/titles");
    const body = JSON.parse((call?.[1] as RequestInit).body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      anime: true,
      anilist_id: 154587,
      metadata_source: "cinemeta",
    });
  });

  it("renders an inline error when Add fails", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/metadata/preview")) {
        return jsonResponse(makePreview());
      }
      if (url === "/api/titles" && init?.method === "POST") {
        return jsonResponse({ code: "METADATA_UNAVAILABLE", message: "nope" }, 502);
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPreview("/preview/series/imdb/tt0944947");

    fireEvent.click(await screen.findByRole("button", { name: "Add to library" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not add the title");
    expect(screen.getByRole("button", { name: "Add to library" })).toBeEnabled();
  });
});
