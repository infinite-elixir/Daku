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
    banner_url: overrides.banner_url ?? null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: overrides.wanted ?? false,
    wanted_at: null,
    availability: overrides.availability ?? "none",
    saved_release_count: 0,
    file_count: overrides.file_count ?? 0,
    has_files: overrides.has_files ?? false,
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
    // A title with no files shows no availability pill: "Saved" said nothing useful.
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(screen.queryByText("Missing")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Big Buck Bunny/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Big Buck Bunny/i })).toHaveClass(
      "poster-glow",
      "kind-glow-movie",
      "absolute",
      "inset-0",
    );
    expect(screen.getByText("Big Buck Bunny").closest(".grid")).toHaveClass("gap-card");
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

  it("expands the filter bar to reveal every watch status", async () => {
    stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    expect(screen.queryByRole("button", { name: "Completed" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("button", { name: "Completed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dropped" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Untracked" })).toBeInTheDocument();
  });

  it("removes the no-file and wanted filter chips", async () => {
    stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    expect(screen.queryByRole("button", { name: "No file yet" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Wanted" })).not.toBeInTheDocument();
  });
});

describe("LibraryPage downloaded filter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("filters by has_files", async () => {
    const fetchMock = stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    fireEvent.click(screen.getByRole("button", { name: "Downloaded" }));

    expect(screen.getByRole("button", { name: "Downloaded" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([url]) => String(url));
      const last = calls.at(-1) ?? "";
      expect(last).toContain("has_files=true");
    });
  });

  it("adds a derived Not watched filter", async () => {
    const fetchMock = stubLibrary([makeTitle()]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    fireEvent.click(screen.getByRole("button", { name: "Not watched" }));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(([url]) => String(url));
      const last = calls.at(-1) ?? "";
      expect(last).toContain("watched=false");
    });
  });

  it("exposes the files link outside the poster anchor", async () => {
    stubLibrary([makeTitle({ file_count: 3 })]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    const titleLink = screen.getByRole("link", { name: /Big Buck Bunny/i });
    const filesLink = screen.getByRole("link", { name: "3 files" });

    expect(filesLink).toHaveAttribute("href", "/title/title-1/files");
    expect(filesLink.closest("a")).toBe(filesLink);
    expect(titleLink).not.toContainElement(filesLink);
    expect(titleLink.contains(filesLink)).toBe(false);
  });

  it("hides the files link when the title has no files", async () => {
    stubLibrary([makeTitle({ file_count: 0 })]);

    renderPage();

    await screen.findByText("Big Buck Bunny");
    expect(screen.queryByRole("link", { name: /files$/ })).not.toBeInTheDocument();
  });
});
