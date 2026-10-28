import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReleasesSection } from "../../components/ReleasesSection";
import type {
  ReleaseItem,
  ReleasesResponse,
  SavedReleasesResponse,
  StoredRelease,
  TitleDetail,
  ReleaseOptions,
} from "../../lib/api";

const RELEASE: ReleaseItem = {
  info_hash: "1111111111111111111111111111111111111111",
  filename: "Big.Buck.Bunny.1080p.mkv",
  quality: "1080p",
  size_bytes: 4_500_000_000,
  seeders: 120,
  provider: "YTS",
  magnet:
    "magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=Big.Buck.Bunny.1080p.mkv",
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

const STORED: StoredRelease = {
  ...RELEASE,
  id: "release-1",
  release_id: "release-1",
  title_id: "title-1",
  episode_id: null,
  last_seen_seeders: 120,
  miss_count: 0,
  fetched_at: "2026-10-23T10:00:00",
  is_saved: true,
  saved_at: "2026-10-23T10:00:00",
  last_checked_at: "2026-10-23T10:00:00",
};

function makeTitle(overrides: Partial<TitleDetail> = {}): TitleDetail {
  return {
    id: "title-1",
    kind: "movie",
    title: "Big Buck Bunny",
    year: 2008,
    imdb_id: "tt1254207",
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
    episode_count: 0,
    available_episode_count: 0,
    episodes: [],
    ...overrides,
  };
}

function findPayload(releases: ReleaseItem[]): ReleasesResponse {
  return {
    title_id: "title-1",
    type: "movie",
    id: "tt1254207",
    episode_id: null,
    stale: false,
    cached_at: "2026-10-23T10:00:00",
    sort: "seeders",
    quality_filter: ["cam", "scr", "unknown"],
    providers: [],
    min_quality: null,
    limit: 20,
    total: releases.length,
    total_unfiltered: releases.length,
    returned: releases.length,
    duplicates_removed: 0,
    quality_counts: {},
    releases,
  };
}

function savedPayload(releases: StoredRelease[]): SavedReleasesResponse {
  return {
    title_id: "title-1",
    total: releases.length,
    episode_id: null,
    quality: [],
    dead: null,
    refresh: { attempted: 0, checked: 0, found: 0, missed: 0, stale: false, reason: null },
    releases,
  };
}

function optionsPayload(overrides: Partial<ReleaseOptions> = {}): ReleaseOptions {
  return {
    providers: ["yts", "eztv", "1337x", "thepiratebay", "torrentgalaxy"],
    quality_labels: ["2160p", "1440p", "1080p", "720p", "576p", "480p"],
    min_qualities: ["480p", "576p", "720p", "1080p", "1440p", "2160p"],
    sorts: ["seeders", "quality", "size"],
    defaults: { providers: [], min_quality: null, sort: "seeders", limit: 20 },
    limit_steps: [20, 50, 100, 200],
    ...overrides,
  };
}

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

type Handler = (
  url: string,
  init?: RequestInit,
) => Response | Promise<Response | undefined> | undefined;

function stubApi(handler: Handler) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const result = handler(url, init);
    if (result) {
      return result;
    }
    if (url.includes("/releases/saved")) {
      return jsonResponse(savedPayload([]));
    }
    if (url.includes("/releases")) {
      return jsonResponse(findPayload([]));
    }
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function installMemoryStorage() {
  const data = new Map<string, string>();
  const mock = {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
    clear: () => {
      data.clear();
    },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: mock });
  return mock;
}

function postedBody(fetchMock: ReturnType<typeof vi.fn>, path: string): Record<string, unknown> {
  const call = fetchMock.mock.calls.find(
    ([url, init]) => url === path && (init as RequestInit | undefined)?.method === "POST",
  );
  expect(call).toBeTruthy();
  const init = (call as [string, RequestInit])[1];
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

describe("ReleasesSection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage?.clear();
  });

  it("stars a Torrentio result by persisting and saving it", async () => {
    const fetchMock = stubApi((url, init) => {
      if (init?.method === "POST" && url === "/api/releases") {
        return jsonResponse(STORED);
      }
      if (url.includes("/releases")) {
        return jsonResponse(findPayload([RELEASE]));
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();
    expect(screen.getByText(/120 seeders/)).toBeInTheDocument();
    expect(screen.getByText(/WEB-DL/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Save Big\.Buck/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/releases",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const body = postedBody(fetchMock, "/api/releases");
    expect(body.magnet).toContain("btih:1111");
    expect(body.title_id).toBe("title-1");
    expect(body.save).toBe(true);
    expect(await screen.findByRole("status")).toHaveTextContent("Saved");
  });

  it("saves a pasted magnet", async () => {
    const fetchMock = stubApi((url, init) => {
      if (init?.method === "POST" && url === "/api/releases") {
        return jsonResponse({ ...STORED, resolved_from: "manual", provider: "manual" });
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    fireEvent.change(await screen.findByLabelText("Paste a magnet"), {
      target: { value: "magnet:?xt=urn:btih:" + "a".repeat(40) + "&dn=Manual.mkv" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save magnet" }));

    await waitFor(() => {
      const body = postedBody(fetchMock, "/api/releases");
      expect(body.magnet).toContain("a".repeat(40));
      expect(body.title_id).toBe("title-1");
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Magnet saved");
  });

  it("opens the Saved tab, shows a dead badge and links to Find another", async () => {
    const fetchMock = stubApi((url) => {
      if (url.includes("/releases/saved")) {
        return jsonResponse(savedPayload([{ ...STORED, dead: true, miss_count: 3 }]));
      }
      if (url.includes("/releases")) {
        return jsonResponse(findPayload([RELEASE]));
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle({ saved_release_count: 1 })} />);

    expect(await screen.findByText("Dead")).toBeInTheDocument();
    expect(screen.getByText(/Checked/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Find another" }));

    expect(screen.getByRole("button", { name: "Find releases" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/titles/title-1/releases?sort=seeders&limit=20",
        expect.anything(),
      );
    });
  });

  it("downloads a saved release by its row id", async () => {
    const fetchMock = stubApi((url, init) => {
      if (url.includes("/releases/saved")) {
        return jsonResponse(savedPayload([STORED]));
      }
      if (init?.method === "POST" && url === "/api/downloads") {
        return jsonResponse({ id: "download-1", release_id: "release-1", status: "queued" });
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle({ saved_release_count: 1 })} />);

    fireEvent.click(await screen.findByRole("button", { name: /^Download Big\.Buck/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/downloads",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Queued for download");
  });

  it("shows a 2160p release with the quality filter and sends min_quality", async () => {
    const ultra: ReleaseItem = {
      ...RELEASE,
      info_hash: "9".repeat(40),
      filename: "Big.Buck.Bunny.2160p.mkv",
      quality: "2160p",
    };
    const fetchMock = stubApi((url) => {
      if (url === "/api/releases/options") {
        return jsonResponse(optionsPayload());
      }
      if (url.includes("min_quality=2160p") && url.includes("sort=quality")) {
        return jsonResponse({
          ...findPayload([ultra]),
          sort: "quality",
          min_quality: "2160p",
          total: 1,
          total_unfiltered: 78,
          returned: 1,
          quality_counts: { "2160p": 1 },
        });
      }
      if (url.includes("/releases")) {
        return jsonResponse(findPayload([RELEASE]));
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Minimum quality"), { target: { value: "2160p" } });
    fireEvent.change(screen.getByLabelText("Release sort"), { target: { value: "quality" } });

    expect(await screen.findByText("Big.Buck.Bunny.2160p.mkv")).toBeInTheDocument();
    expect(screen.getByText(/2160p 1/)).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("min_quality=2160p"),
        expect.anything(),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("sort=quality"),
        expect.anything(),
      );
    });
  });

  it("raises the limit with Show more without dropping the filters", async () => {
    const fetchMock = stubApi((url) => {
      if (url === "/api/releases/options") {
        return jsonResponse(optionsPayload());
      }
      if (url.includes("/releases")) {
        return jsonResponse({ ...findPayload([RELEASE]), total: 78, total_unfiltered: 78 });
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    expect(await screen.findByText("Showing 1 of 78")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("limit=50"),
        expect.anything(),
      );
    });
  });

  it("keeps the current rows mounted while Show more is loading", async () => {
    let resolveMore: ((value: Response) => void) | undefined;
    const fetchMock = stubApi((url) => {
      if (url === "/api/releases/options") {
        return jsonResponse(optionsPayload());
      }
      if (url.includes("limit=50")) {
        return new Promise<Response>((resolve) => {
          resolveMore = resolve;
        });
      }
      if (url.includes("/releases")) {
        return jsonResponse({ ...findPayload([RELEASE]), total: 78, total_unfiltered: 78 });
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("limit=50"),
        expect.anything(),
      );
    });
    // The previous page stays on screen (so scroll is not lost) until the next one arrives.
    expect(screen.getByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();

    resolveMore?.(jsonResponse({ ...findPayload([RELEASE]), total: 78, total_unfiltered: 78 }));
    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();
  });

  it("remembers provider selection and refuses an empty set", async () => {
    const localStorage = installMemoryStorage();
    localStorage.setItem("daku.releaseProviders.override.movie", JSON.stringify(["yts"]));
    const fetchMock = stubApi((url) => {
      if (url === "/api/releases/options") {
        return jsonResponse(optionsPayload());
      }
      if (url.includes("/releases")) {
        return jsonResponse(findPayload([RELEASE]));
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    const yts = await screen.findByLabelText("Provider yts");
    expect(yts).toBeChecked();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("providers=yts"),
        expect.anything(),
      );
    });

    fireEvent.click(yts);
    expect(await screen.findByText("Choose at least one provider.")).toBeInTheDocument();
    expect(localStorage.getItem("daku.releaseProviders.override.movie")).toBe(JSON.stringify(["yts"]));

    fireEvent.click(screen.getByRole("button", { name: "Use defaults" }));
    expect(localStorage.getItem("daku.releaseProviders.override.movie")).toBeNull();
  });

  it("names the active minimum in the empty state and resets", async () => {
    stubApi((url) => {
      if (url === "/api/releases/options") {
        return jsonResponse(optionsPayload());
      }
      if (url.includes("min_quality=2160p")) {
        return jsonResponse({
          ...findPayload([]),
          total: 0,
          total_unfiltered: 78,
          returned: 0,
          min_quality: "2160p",
        });
      }
      if (url.includes("/releases")) {
        return jsonResponse(findPayload([RELEASE]));
      }
      return undefined;
    });

    renderPage(<ReleasesSection title={makeTitle()} />);

    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Minimum quality"), { target: { value: "2160p" } });

    expect(await screen.findByText(/No releases match a minimum of 2160p/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));

    expect(await screen.findByText("Big.Buck.Bunny.1080p.mkv")).toBeInTheDocument();
    expect(screen.getByLabelText("Minimum quality")).toHaveValue("");
  });
});
