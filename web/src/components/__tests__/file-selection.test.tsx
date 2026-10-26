import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EpisodeDownload } from "../EpisodeDownload";
import { MagnetFiles } from "../MagnetFiles";
import type { ReleaseItem } from "../../lib/api";

function renderWithClient(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

function jsonResponse(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

const METADATA = {
  info_hash: "a".repeat(40),
  name: "Show S02",
  total_size_bytes: 3_000,
  piece_length: 262144,
  private: false,
  files: [
    { index: 0, path: "Show.S02E05.1080p.mkv", size_bytes: 1_400 },
    { index: 1, path: "Show.S02E06.1080p.mkv", size_bytes: 1_600 },
  ],
  fetched_at: "2026-10-26T00:00:00Z",
  cached: false,
  source: "probe" as const,
  release_id: "release-1",
};

const RELEASE: ReleaseItem = {
  info_hash: "a".repeat(40),
  filename: "Show.S02.1080p.mkv",
  quality: "1080p",
  size_bytes: 3_000,
  seeders: 10,
  provider: "torrentio",
  magnet: `magnet:?xt=urn:btih:${"a".repeat(40)}`,
  metadata_json: {
    source: null,
    video_codec: null,
    audio_hint: null,
    languages: null,
    release_group: null,
    edition: null,
  },
  release_id: "release-1",
  is_saved: false,
  dead: false,
  saved_at: null,
  last_checked_at: null,
  resolved_from: "torrentio",
};

function downloadsCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([url]) => String(url) === "/api/downloads");
}

describe("file selection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tracks selected files and disables Download selected until one is ticked", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/files")) {
        return jsonResponse(METADATA);
      }
      return jsonResponse({ id: "download-1", status: "queued" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <MagnetFiles magnet={RELEASE.magnet} releaseId="release-1" episodeId="episode-5" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Files" }));

    const checkboxes = await screen.findAllByRole("checkbox", {
      name: "Select Show.S02E05.1080p.mkv",
    });
    const checkbox = checkboxes[0]!;
    const button = screen.getByRole("button", { name: "Download selected" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/Tick at least one file/)).toBeInTheDocument();

    fireEvent.click(checkbox);
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);

    await waitFor(() => expect(downloadsCalls(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(downloadsCalls(fetchMock)[0]![1]!.body));
    expect(body).toMatchObject({
      release_id: "release-1",
      episode_id: "episode-5",
      file_paths: ["Show.S02E05.1080p.mkv"],
    });
  });

  it("confirms an auto-matched episode before queueing it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/downloads/preview") {
        return jsonResponse({
          selected_files: [0],
          match: {
            episode_id: "episode-5",
            file_index: 0,
            confidence: 100,
            reason: "S02E05",
            file_paths: ["Show.S02E05.1080p.mkv"],
          },
          files: METADATA.files,
          total_files: 2,
        });
      }
      return jsonResponse({ id: "download-1", status: "queued" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <EpisodeDownload
        release={RELEASE}
        episodeId="episode-5"
        episodeLabel="S02E05"
        ensureRelease={async () => "release-1"}
        onQueued={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Download S02E05" }));

    expect(await screen.findByText(/Will download:/)).toHaveTextContent(
      "Show.S02E05.1080p.mkv",
    );
    fireEvent.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => expect(downloadsCalls(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(downloadsCalls(fetchMock)[0]![1]!.body));
    expect(body.file_paths).toEqual(["Show.S02E05.1080p.mkv"]);
    expect(body.episode_id).toBe("episode-5");
  });

  it("shows an ambiguous chooser with reasons and requires confirmation for the pack", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/downloads/preview") {
        return jsonResponse(
          {
            code: "FILE_SELECTION_REQUIRED",
            message: "More than one file matches S02E05.",
            detail: {
              episode_id: "episode-5",
              episode_label: "S02E05",
              candidates: [
                {
                  index: 0,
                  path: "Show.S02E05.1080p.mkv",
                  size_bytes: 1_400,
                  reason: "S02E05",
                },
                {
                  index: 1,
                  path: "Show.S02E05.720p.mkv",
                  size_bytes: 900,
                  reason: "S02E05",
                },
              ],
              files: [
                {
                  index: 0,
                  path: "Show.S02E05.1080p.mkv",
                  size_bytes: 1_400,
                },
                {
                  index: 1,
                  path: "Show.S02E05.720p.mkv",
                  size_bytes: 900,
                },
              ],
            },
          },
          409,
        );
      }
      return jsonResponse({ id: "download-1", status: "queued" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <EpisodeDownload
        release={RELEASE}
        episodeId="episode-5"
        episodeLabel="S02E05"
        ensureRelease={async () => "release-1"}
        onQueued={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Download S02E05" }));

    const dialog = await screen.findByRole("dialog", { name: "Choose a file for S02E05" });
    expect(dialog).toHaveTextContent("Show.S02E05.1080p.mkv");
    expect(dialog).toHaveTextContent("S02E05");
    expect(
      screen.getByRole("radio", { name: "Choose Show.S02E05.720p.mkv" }),
    ).toBeInTheDocument();

    const packButton = screen.getByRole("button", { name: "Download whole pack" });
    expect(packButton).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: /I understand/ }));
    expect(packButton).toBeEnabled();
    fireEvent.click(packButton);

    await waitFor(() => expect(downloadsCalls(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(downloadsCalls(fetchMock)[0]![1]!.body));
    expect(body.allow_pack).toBe(true);
  });

  it("downloads one chosen candidate from the chooser", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/downloads/preview") {
        return jsonResponse(
          {
            code: "FILE_SELECTION_REQUIRED",
            message: "More than one file matches S02E05.",
            detail: {
              episode_label: "S02E05",
              candidates: [
                {
                  index: 0,
                  path: "Show.S02E05.1080p.mkv",
                  size_bytes: 1_400,
                  reason: "S02E05",
                },
                {
                  index: 1,
                  path: "Show.S02E05.720p.mkv",
                  size_bytes: 900,
                  reason: "S02E05",
                },
              ],
              files: [
                {
                  index: 0,
                  path: "Show.S02E05.1080p.mkv",
                  size_bytes: 1_400,
                },
                {
                  index: 1,
                  path: "Show.S02E05.720p.mkv",
                  size_bytes: 900,
                },
              ],
            },
          },
          409,
        );
      }
      return jsonResponse({ id: "download-1", status: "queued" }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <EpisodeDownload
        release={RELEASE}
        episodeId="episode-5"
        episodeLabel="S02E05"
        ensureRelease={async () => "release-1"}
        onQueued={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Download S02E05" }));
    await screen.findByRole("dialog", { name: "Choose a file for S02E05" });

    fireEvent.click(screen.getByRole("radio", { name: "Choose Show.S02E05.720p.mkv" }));
    fireEvent.click(screen.getByRole("button", { name: "Download selected file" }));

    await waitFor(() => expect(downloadsCalls(fetchMock)).toHaveLength(1));
    const body = JSON.parse(String(downloadsCalls(fetchMock)[0]![1]!.body));
    expect(body.file_paths).toEqual(["Show.S02E05.720p.mkv"]);
  });
});
