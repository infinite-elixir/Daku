import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { TitleMetadata } from "../../lib/api";
import { TitleView, type TitleViewData } from "../TitleView";

const METADATA: TitleMetadata = {
  imdb_rating: 8.3,
  rt_rating: 96,
  metacritic_rating: 87,
  runtime_minutes: 24,
  genres: ["Animation", "Sci-Fi"],
  cast: ["Aoi Yuuki", "Kenjiro Tsuda"],
  directors: ["Hiroyuki Imaishi"],
  writers: ["Masahiko Otsuka"],
  studios: ["Trigger"],
  stale: false,
};

function makeTitle(overrides: Partial<TitleViewData> = {}): TitleViewData {
  return {
    kind: "series",
    title: "Cyberpunk: Edgerunners",
    year: 2022,
    imdb_id: "tt8312878",
    anilist_id: null,
    overview: "A street kid tries to survive in a technology-obsessed city.",
    poster_url: "https://images.example/poster.jpg",
    banner_url: null,
    folder_name: "Cyberpunk Edgerunners (2022)",
    availability: "complete",
    wanted: false,
    saved_release_count: 4,
    episode_count: 10,
    available_episode_count: 10,
    episodes: [],
    seasons: [],
    ...overrides,
  };
}

function renderView(
  title: Partial<TitleViewData> = {},
  options: { actions?: ReactNode; showCounters?: boolean } = {},
) {
  return render(
    <MemoryRouter>
      <TitleView
        title={makeTitle(title)}
        metadata={METADATA}
        back={{ to: "/library", label: "← Library" }}
        actions={options.actions}
        showCounters={options.showCounters}
      />
    </MemoryRouter>,
  );
}

describe("TitleView hero", () => {
  it("uses the poster-matched grid with the 720px container breakpoint", () => {
    renderView();

    const hero = screen.getByTestId("title-hero");
    expect(hero.tagName).toBe("SECTION");
    expect(hero).toHaveClass(
      "grid",
      "grid-cols-1",
      "gap-gutter",
      "@min-[720px]:grid-cols-[180px_minmax(0,1fr)]",
      "@min-[1024px]:grid-cols-[minmax(160px,220px)_minmax(0,1fr)]",
    );
    expect(screen.getByTestId("title-poster")).toHaveClass("title-hero-poster");
    expect(screen.getByTestId("title-identity")).toHaveClass("flex", "flex-col");
  });

  it("labels the hero once and puts the h1 inside it", () => {
    renderView();

    const hero = screen.getByRole("region", { name: "Cyberpunk: Edgerunners" });
    expect(hero).toBe(screen.getByTestId("title-hero"));
    expect(hero).toContainElement(
      screen.getByRole("heading", { level: 1, name: "Cyberpunk: Edgerunners" }),
    );
  });

  it("pins a single action row to the poster's bottom edge", () => {
    renderView({}, { actions: <button type="button">Mark wanted</button> });

    const rows = screen.getAllByTestId("title-actions");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveClass("mt-auto");
    expect(screen.getByTestId("title-identity")).toContainElement(rows[0] ?? null);
    expect(screen.getAllByRole("button", { name: "Mark wanted" })).toHaveLength(1);
  });

  it("keeps counters in one inline dl row", () => {
    renderView();

    const counters = screen.getByTestId("title-counters");
    expect(counters.tagName).toBe("DL");
    expect(counters).toHaveClass("flex", "flex-wrap");
    expect(counters).toHaveTextContent(/Saved releases\s*4/);
    expect(counters).toHaveTextContent(/Episodes\s*10\/10/);
    expect(counters).toHaveTextContent("Cyberpunk Edgerunners (2022)");
  });

  it("drops library counters when asked (preview)", () => {
    renderView({}, { showCounters: false });

    expect(screen.queryByTestId("title-counters")).not.toBeInTheDocument();
  });

  it("places ratings once under the title and never in a rail", () => {
    const { container } = renderView();

    expect(screen.getAllByLabelText("IMDb rating 8.3 out of 10")).toHaveLength(1);
    expect(container.querySelector("aside")).not.toBeInTheDocument();
    expect(container.querySelector("[data-testid*='rail']")).not.toBeInTheDocument();
  });

  it("moves overview and facts outside the hero grid, full width", () => {
    renderView();

    const hero = screen.getByTestId("title-hero");
    const factsScope = screen.getByTestId("title-facts-scope");
    expect(hero).not.toContainElement(factsScope);

    expect(factsScope).toHaveTextContent(/A street kid tries to survive/);
    const facts = factsScope.querySelector(".title-facts");
    expect(facts).not.toBeNull();
    expect(facts).toHaveClass(
      "grid",
      "grid-cols-1",
      "@min-[720px]:grid-cols-2",
      "@min-[1024px]:grid-cols-[repeat(auto-fill,minmax(220px,1fr))]",
      "gap-panel",
    );
    expect(facts).toHaveTextContent("Animation");
    expect(facts).toHaveTextContent("Trigger");
  });

  it("keeps the episodes section full width below the hero", () => {
    renderView();

    const hero = screen.getByTestId("title-hero");
    const episodes = screen.getByRole("region", { name: "Episodes" });
    expect(hero).not.toContainElement(episodes);
  });
});
