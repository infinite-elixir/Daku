import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReleasesResponse, TitleDetail } from "../../lib/api";
import { TitlePage } from "../TitlePage";

function makeTitle(overrides: Partial<TitleDetail> = {}): TitleDetail {
  return {
    id: "title-1",
    kind: "movie",
    title: "Big Buck Bunny",
    year: 2008,
    imdb_id: "tt1234567",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: "A large rabbit deals with three bullying rodents.",
    poster_url: "https://images.metahub.space/poster/tt1234567.jpg",
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    availability: "none",
    saved_release_count: 0,
    episode_count: 0,
    available_episode_count: 0,
    episodes: [],
    ...overrides,
  };
}

function emptyReleases(): ReleasesResponse {
  return {
    title_id: "title-1",
    type: "movie",
    id: "tt1234567",
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

function renderTitle(title: TitleDetail) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/metadata")) {
        return jsonResponse({ stale: false });
      }
      if (url.includes("/releases")) {
        return jsonResponse(emptyReleases());
      }
      if (url.startsWith("/api/titles/title-1")) {
        return jsonResponse(title);
      }
      return jsonResponse({});
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/title/title-1"]}>
        <Routes>
          <Route path="/title/:id" element={<TitlePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TitlePage banner", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders a dithered banner with a mandatory bottom-up scrim", async () => {
    renderTitle(makeTitle());

    expect(await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" })).toBeVisible();
    const banner = screen.getByTestId("title-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveAttribute("aria-hidden", "true");
    expect(banner).toHaveClass("title-banner-strip", "h-52");

    const scrim = screen.getByTestId("title-banner-scrim");
    expect(scrim).toHaveClass("title-banner-scrim");
  });

  it("hides the banner below 720px without adding height or horizontal overflow", async () => {
    renderTitle(makeTitle());

    await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" });
    const banner = screen.getByTestId("title-banner");
    expect(banner).toHaveClass("title-banner-strip");
    expect(banner).toHaveClass("absolute");
  });

  it("renders the procedural fallback when the title has no poster", async () => {
    renderTitle(makeTitle({ poster_url: null }));

    const fallback = await screen.findByTestId("dither-fallback");
    expect(fallback.textContent?.trim().length).toBeGreaterThan(0);
    expect(screen.getByTestId("title-banner")).toHaveAttribute("data-testid", "title-banner");
  });

  it("keeps the title and actions outside the decorative art", async () => {
    renderTitle(makeTitle());

    const heading = await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" });
    expect(heading).toBeVisible();
    expect(screen.getByRole("button", { name: "Mark wanted" })).toBeVisible();
  });
});
