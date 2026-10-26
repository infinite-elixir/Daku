import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LibraryResponse, SearchResult, TitleDetail } from "../../lib/api";
import { SearchPage } from "../SearchPage";

const RESULT: SearchResult = {
  source: "imdb",
  source_id: "tt1234567",
  kind: "series",
  title: "Big Buck Bunny",
  year: 2008,
  poster_url: null,
};

function libraryPayload(items: LibraryResponse["items"] = []): LibraryResponse {
  return { items, total: items.length, limit: 200, offset: 0 };
}

function addedTitle(): TitleDetail {
  return {
    id: "title-1",
    kind: "series",
    title: "Big Buck Bunny",
    year: 2008,
    imdb_id: "tt1234567",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    availability: "none",
    saved_release_count: 0,
    episode_count: 0,
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

function LocationSpy() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function stubApi(
  library: LibraryResponse = libraryPayload(),
  results: SearchResult[] = [RESULT],
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/titles" && init?.method === "POST") {
      return jsonResponse(addedTitle(), 201);
    }
    if (url.startsWith("/api/titles")) {
      return jsonResponse(library);
    }
    if (url.startsWith("/api/search")) {
      return jsonResponse({
        query: "bunny",
        kind: null,
        format: null,
        stale: false,
        results,
      });
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderSearch(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/search"
            element={
              <>
                <SearchPage />
                <LocationSpy />
              </>
            }
          />
          <Route path="/preview/:kind/:source/:sourceId" element={<div>Preview destination</div>} />
          <Route path="/title/:id" element={<div>Title destination</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SearchPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores the query and kind filter from the URL", async () => {
    const fetchMock = stubApi();
    renderSearch("/search?q=bunny&kind=series");

    expect(screen.getByLabelText("Search titles")).toHaveValue("bunny");
    expect(screen.getByRole("button", { name: "Series" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/search?q=bunny&kind=series&limit=30",
        expect.anything(),
      );
    });
  });

  it("links a result card to the preview", async () => {
    stubApi();
    renderSearch("/search?q=bunny");

    const link = await screen.findByRole("link", { name: "Big Buck Bunny" });
    expect(link).toHaveAttribute("href", "/preview/series/imdb/tt1234567");

    fireEvent.click(link);

    expect(await screen.findByText("Preview destination")).toBeInTheDocument();
  });

  it("adds from the card without navigating", async () => {
    const fetchMock = stubApi();
    renderSearch("/search?q=bunny");

    fireEvent.click(await screen.findByRole("button", { name: "Add Big Buck Bunny to library" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => url === "/api/titles");
      expect(call).toBeTruthy();
    });
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/titles");
    const body = JSON.parse((call?.[1] as RequestInit).body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      source: "imdb",
      source_id: "tt1234567",
      kind: "series",
      metadata_source: "cinemeta",
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/search?q=bunny");
  });

  it("shows Open for a title already in the library", async () => {
    stubApi(
      libraryPayload([
        {
          id: "lib-1",
          kind: "series",
          title: "Big Buck Bunny",
          year: 2008,
          imdb_id: "tt1234567",
          tmdb_id: null,
          tvdb_id: null,
          anilist_id: null,
          mal_id: null,
          overview: null,
          poster_url: null,
          folder_name: null,
          added_at: "2026-10-23T10:00:00",
          wanted: false,
          wanted_at: null,
          availability: "none",
          saved_release_count: 0,
        },
      ]),
    );
    renderSearch("/search?q=bunny");

    const open = await screen.findByRole("link", { name: "Open Big Buck Bunny" });
    expect(open).toHaveAttribute("href", "/title/lib-1");
    expect(
      screen.queryByRole("button", { name: "Add Big Buck Bunny to library" }),
    ).not.toBeInTheDocument();
  });

  it("shows the ANIME badge and reason for a classified result", async () => {
    stubApi(libraryPayload(), [
      {
        ...RESULT,
        title: "Frieren",
        source_id: "tt22248376",
        anime: true,
        anilist_id: 154587,
        classification_reason: "AniList exact match",
      },
    ]);
    renderSearch("/search?q=frieren");

    const badge = await screen.findByText("ANIME");
    expect(badge).toHaveAttribute("title", "AniList exact match");
  });

  it("writes the typed query into the URL", async () => {
    stubApi();
    renderSearch("/search");

    fireEvent.change(screen.getByLabelText("Search titles"), {
      target: { value: "frieren" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/search?q=frieren");
    });
  });
});
