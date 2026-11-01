import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TitleDetail, TitleOverrides } from "../../lib/api";
import { MetadataFixerDialog } from "../../components/MetadataFixerDialog";

function makeTitle(overrides: Partial<TitleDetail> = {}): TitleDetail {
  return {
    id: "title-1",
    kind: "series",
    title: "Frieren: Beyond Journey's End",
    year: 2023,
    imdb_id: "tt22248376",
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
    episode_count: 28,
    available_episode_count: 0,
    episodes: [],
    metadata_source: "cinemeta",
    metadata_synced_at: null,
    classification: {
      is_anime: true,
      confidence: "exact",
      reason: "AniList exact match",
      suggested_anilist_id: 154587,
    },
    ...overrides,
  };
}

function emptyOverrides(): TitleOverrides {
  return {
    title_id: "title-1",
    fields: {},
    season_offsets: [],
    episodes: [],
    provider_values: {},
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function renderDialog(title = makeTitle(), onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/metadata/anilist/")) {
      const id = Number(url.split("/").pop());
      if (id === 31706) {
        return jsonResponse({
          anilist_id: 31706,
          type: "MANGA",
          format: "MANGA",
          episodes: null,
          season_year: null,
          title: "JoJo's Bizarre Adventure: Part 7 - Steel Ball Run",
          is_anime: false,
        });
      }
      return jsonResponse({
        anilist_id: id,
        type: "ANIME",
        format: "TV",
        episodes: 28,
        season_year: 2023,
        title: "Frieren: Beyond Journey's End",
        is_anime: true,
      });
    }
    if (url.endsWith("/overrides")) {
      return jsonResponse(emptyOverrides());
    }
    if (url.endsWith("/seasons/rebuild")) {
      return jsonResponse({
        ...title,
        rebuild: {
          source: "cinemeta",
          kept_episodes: 10,
          renumbered: 10,
          created: 1,
          unmatched: 0,
          files_at_risk: 0,
        },
      });
    }
    if (init?.method === "PATCH" || init?.method === "PUT") {
      return jsonResponse({ ...title, overrides: emptyOverrides() });
    }
    return jsonResponse(title);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <MetadataFixerDialog title={title} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchMock, onClose };
}

describe("MetadataFixerDialog", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("suggests the AniList identity and saves a kind change", async () => {
    const { fetchMock } = renderDialog();
    const anilist = await screen.findByLabelText("AniList id");
    await waitFor(() => expect(anilist).toHaveValue("154587"));

    fireEvent.click(screen.getByRole("button", { name: "Anime" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({
        kind: "anime",
        anilist_id: 154587,
      });
    });
    expect(await screen.findByText(/Identity updated/)).toBeInTheDocument();
  });

  it("disables a manga AniList candidate and explains why", async () => {
    renderDialog(makeTitle({ kind: "anime", anilist_id: 31706 }));

    expect(
      await screen.findByText(/manga entry, not an anime/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Use this anime entry" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("blocks a destructive kind change until it is confirmed", async () => {
    const { fetchMock } = renderDialog();
    await screen.findByLabelText("AniList id");

    fireEvent.click(screen.getByRole("button", { name: "Movie" }));
    expect(screen.getByText("Destructive change")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Confirm the destructive kind change first.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);

    fireEvent.click(screen.getByLabelText("I understand this removes the episode list."));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true),
    );
  });

  it("shows the rebuild warning and the rebuild result", async () => {
    const title = makeTitle({
      kind: "anime",
      episode_count: 11,
      available_episode_count: 1,
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
          anilist_id: 120377,
          episode_count: 10,
        },
      ],
    });
    renderDialog(title);

    expect(
      await screen.findByText(/Rebuilding renumbers the current 11 episode rows/),
    ).toBeInTheDocument();
    expect(screen.getByText(/files on disk are kept/)).toBeInTheDocument();
    expect(screen.getByText("Specials")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Rebuild from IMDb" }));

    expect(
      await screen.findByText("Rebuilt from cinemeta; 10 episode rows kept, 1 created."),
    ).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const { onClose } = renderDialog();
    await screen.findByLabelText("AniList id");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
