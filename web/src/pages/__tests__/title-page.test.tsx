import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    banner_url: "https://images.metahub.space/background/tt1234567/medium.jpg",
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

function renderTitle(title: TitleDetail, refreshResult?: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/metadata/refresh")) {
        return jsonResponse(
          refreshResult ?? {
            title_id: title.id,
            source: "anilist",
            partial: false,
            refresh_error: null,
            report: { added: 0, updated: 0, skipped: 0, stale: false },
            overridden_fields: [],
          },
        );
      }
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

  it("reserves the hero and facts regions while loading", async () => {
    renderTitle(makeTitle());

    const skeleton = screen.getByTestId("title-skeleton");
    expect(skeleton).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("title-skeleton-poster")).toHaveClass("title-hero-poster");
    expect(screen.getByTestId("title-skeleton-identity")).toHaveClass("flex", "flex-col");
    expect(screen.getByTestId("title-skeleton-facts")).toHaveClass("title-facts");

    await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" });
    expect(screen.queryByTestId("title-skeleton")).not.toBeInTheDocument();
  });

  it("shows an artwork notice and can refresh metadata", async () => {
    renderTitle(makeTitle({ poster_url: null }), {
      title_id: "title-1",
      source: "anilist",
      partial: true,
      refresh_error: { code: "ARTWORK_UNAVAILABLE", message: "No usable poster artwork was found" },
      report: { added: 0, updated: 0, skipped: 0, stale: false },
      overridden_fields: [],
    });

    expect(await screen.findByText("Artwork unavailable.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh metadata" }));
    expect(
      await screen.findByText("No usable poster artwork was found"),
    ).toBeInTheDocument();
  });

  it("renders the wide banner image directly with a mandatory bottom-up scrim", async () => {
    renderTitle(makeTitle());

    expect(await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" })).toBeVisible();
    const banner = screen.getByTestId("title-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveAttribute("aria-hidden", "true");
    expect(banner).toHaveClass("title-banner-strip", "title-banner-bleed", "h-[300px]");

    const image = screen.getByTestId("title-banner-image");
    expect(image).toHaveAttribute(
      "src",
      "https://images.metahub.space/background/tt1234567/medium.jpg",
    );
    expect(image).toHaveAttribute("fetchpriority", "high");
    expect(image).toHaveAttribute("decoding", "async");
    expect(image).toHaveAttribute("width", "1280");
    // The gradient sits underneath while the remote image loads.
    expect(screen.getByTestId("title-banner-gradient")).toHaveClass("banner-fallback");
    expect(screen.queryByTestId("title-banner-art")).not.toBeInTheDocument();

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

  it("renders a gradient fallback when the title has no banner or poster", async () => {
    renderTitle(makeTitle({ poster_url: null, banner_url: null }));

    expect(await screen.findByTestId("title-banner-gradient")).toBeInTheDocument();
    expect(screen.queryByTestId("title-banner-image")).not.toBeInTheDocument();
    expect(screen.getByTestId("title-banner")).toHaveAttribute("data-testid", "title-banner");
  });

  it("keeps the title and actions outside the decorative art", async () => {
    renderTitle(makeTitle());

    const heading = await screen.findByRole("heading", { level: 1, name: "Big Buck Bunny" });
    expect(heading).toBeVisible();
    expect(screen.getByRole("button", { name: "Mark wanted" })).toBeVisible();
  });

  it("shows missing files and reconciles them on demand", async () => {
    renderTitle(makeTitle({ missing_file_count: 3 }));

    const missing = await screen.findByTestId("title-missing-files");
    expect(missing).toHaveTextContent("3 files missing");
    fireEvent.click(screen.getByRole("button", { name: "Clean up" }));

    await waitFor(() =>
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        "/api/titles/title-1/media/reconcile",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
