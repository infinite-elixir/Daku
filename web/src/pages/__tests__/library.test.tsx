import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LibraryResponse, TitleSummary } from "../../lib/api";
import { LibraryPage } from "../LibraryPage";

function makeTitle(overrides: Partial<TitleSummary> = {}): TitleSummary {
  return {
    id: overrides.id ?? "title-1",
    kind: overrides.kind ?? "movie",
    title: overrides.title ?? "Big Buck Bunny",
    year: overrides.year ?? 2008,
    imdb_id: overrides.imdb_id ?? "tt1234567",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: overrides.poster_url ?? "https://images.example/bbb.jpg",
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: overrides.wanted ?? false,
    wanted_at: null,
    availability: overrides.availability ?? "none",
    saved_release_count: 0,
  };
}

function renderPage(children: ReactNode = <LibraryPage />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubLibrary(items: TitleSummary[]) {
  const payload: LibraryResponse = {
    items,
    total: items.length,
    limit: 100,
    offset: 0,
  };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => payload,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("LibraryPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a poster grid with title, year, kind and availability", async () => {
    stubLibrary([makeTitle()]);

    renderPage();

    expect(await screen.findByText("Big Buck Bunny")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Library" })).toHaveClass("text-h1");
    expect(screen.getByText("Big Buck Bunny")).toHaveClass("text-title");
    expect(screen.getByText("2008")).toHaveClass("text-small");
    expect(screen.getByText("2008")).toBeInTheDocument();
    expect(screen.getByText("MOVIE")).toBeInTheDocument();
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("Missing")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Big Buck Bunny/i })).toBeInTheDocument();
  });

  it("renders the empty state when the library has no titles", async () => {
    stubLibrary([]);

    renderPage();

    expect(await screen.findByText("Library is empty")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search the catalogue" })).toBeInTheDocument();
  });

  it("refetches with the selected kind filter", async () => {
    const fetchMock = stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    fireEvent.click(screen.getByRole("button", { name: "Movies" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("kind=movie"),
        expect.anything(),
      );
    });
    expect(screen.getByRole("button", { name: "Movies" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters to entries with no file yet", async () => {
    const fetchMock = stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    fireEvent.click(screen.getByRole("button", { name: "No file yet" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("availability=none"),
        expect.anything(),
      );
    });
  });

  it("renders at most one quiet backdrop from a poster already in the grid", async () => {
    const fetchMock = stubLibrary([
      makeTitle({ id: "a", title: "Alpha", poster_url: "https://images.example/a.jpg" }),
      makeTitle({ id: "b", title: "Beta", poster_url: "https://images.example/b.jpg" }),
      makeTitle({ id: "c", title: "No art", poster_url: null }),
    ]);

    renderPage();

    await screen.findByText("Alpha");
    expect(await screen.findByTestId("library-backdrop")).toBeInTheDocument();
    expect(screen.getAllByTestId("library-backdrop")).toHaveLength(1);
    expect(screen.getByTestId("library-backdrop")).toHaveClass("library-backdrop", "h-80");
    // The backdrop must not add a fetch beyond the library call itself.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("omits the backdrop on narrow screens", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    expect(screen.queryByTestId("library-backdrop")).not.toBeInTheDocument();
  });

  it("omits the backdrop when the user prefers reduced data", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query.includes("prefers-reduced-data"),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    expect(screen.queryByTestId("library-backdrop")).not.toBeInTheDocument();
  });
});
