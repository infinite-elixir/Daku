import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { SearchResult, TitleSummary } from "../../lib/api";
import { LibraryCard } from "../LibraryCard";
import { kindGlowClass } from "../Pills";
import { SearchResultCard } from "../SearchResultCard";

function makeTitle(overrides: Partial<TitleSummary> = {}): TitleSummary {
  return {
    id: "title-1",
    kind: overrides.kind ?? "movie",
    title: overrides.title ?? "Big Buck Bunny",
    year: overrides.year ?? 2008,
    imdb_id: "tt1234567",
    tmdb_id: null,
    tvdb_id: null,
    anilist_id: null,
    mal_id: null,
    overview: null,
    poster_url: overrides.poster_url ?? "https://images.example/bbb.jpg",
    banner_url: overrides.banner_url ?? null,
    folder_name: null,
    added_at: "2026-10-23T10:00:00",
    wanted: false,
    wanted_at: null,
    availability: overrides.availability ?? "complete",
    saved_release_count: 0,
  };
}

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    kind: overrides.kind ?? "series",
    source: "imdb",
    source_id: "tt7654321",
    title: overrides.title ?? "Example Series",
    year: 2020,
    poster_url: overrides.poster_url ?? "https://images.example/series.jpg",
    format: "TV",
    anime: false,
    classification_reason: null,
    anilist_id: null,
    id_mal: null,
  };
}

function renderCard(card: React.ReactNode) {
  return render(<MemoryRouter>{card}</MemoryRouter>);
}

describe("poster cards", () => {
  it.each([
    ["movie", "kind-glow-movie"],
    ["series", "kind-glow-series"],
    ["anime", "kind-glow-anime"],
  ] as const)("uses the %s glow token without a kind top edge", (kind, glow) => {
    const { container } = renderCard(<LibraryCard title={makeTitle({ kind })} />);
    const card = screen.getByRole("link", { name: /Big Buck Bunny/i });

    expect(card).toHaveClass("poster-glow", glow);
    expect(container.querySelector(".h-0\\.5")).toBeNull();
  });

  it("keeps glow on hover/focus and disables it under reduced motion", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles/theme.css"), "utf8");

    expect(css).toContain("@media (hover: hover)");
    expect(css).toContain(".poster-glow:hover");
    expect(css).toContain(".poster-glow:focus-visible");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toMatch(/\.poster-glow:hover,\s*\.poster-glow:focus-visible \{\s*box-shadow: none;/);
  });

  it("exposes the glow contract through the kind helper", () => {
    expect(kindGlowClass("movie")).toBe("poster-glow kind-glow-movie");
    expect(kindGlowClass("series")).toBe("poster-glow kind-glow-series");
    expect(kindGlowClass("anime")).toBe("poster-glow kind-glow-anime");
  });

  it("keeps the kind badge while removing the coloured bar on search cards", () => {
    const { container } = renderCard(
      <SearchResultCard result={makeResult()} libraryId={null} pending={false} onAdd={() => {}} />,
    );

    expect(screen.getByText("TV")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Preview Example Series" })).toHaveClass(
      "poster-glow",
      "kind-glow-series",
    );
    expect(container.querySelector(".absolute.inset-x-0.top-0")).toBeNull();
  });
});
