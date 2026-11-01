import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { MediaFileItem, TitleFilesResponse } from "../../lib/api";
import { DownloadedFiles } from "../DownloadedFiles";

function makeFile(overrides: Partial<MediaFileItem> = {}): MediaFileItem {
  return {
    id: overrides.id ?? "file-1",
    download_id: "download-1",
    episode_id: overrides.episode_id ?? "episode-1",
    episode_label: overrides.episode_label ?? "S01E01",
    episode_name: overrides.episode_name ?? "Let You Down",
    season: overrides.season ?? 1,
    episode: overrides.episode ?? 1,
    file_name: overrides.file_name ?? "episode.mkv",
    size_bytes: overrides.size_bytes ?? 1024,
    is_primary: true,
    has_episode: overrides.has_episode ?? true,
  };
}

function makeFiles(overrides: Partial<TitleFilesResponse> = {}): TitleFilesResponse {
  const items = overrides.items ?? [makeFile()];
  return {
    title_id: overrides.title_id ?? "title-1",
    file_count: overrides.file_count ?? items.length,
    total_size_bytes:
      overrides.total_size_bytes ?? items.reduce((total, file) => total + (file.size_bytes ?? 0), 0),
    seasons: overrides.seasons ?? [{ library_season: 1, file_count: items.length, total_size_bytes: 0 }],
    items,
  };
}

function renderFiles(files: TitleFilesResponse, props: { activeSeason?: number } = {}) {
  return render(
    <MemoryRouter>
      <DownloadedFiles files={files} titleKind="series" {...props} />
    </MemoryRouter>,
  );
}

describe("DownloadedFiles", () => {
  it("renders nothing when the title has no files", () => {
    const { container } = renderFiles(makeFiles({ items: [], file_count: 0, seasons: [] }));

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("downloaded-files")).not.toBeInTheDocument();
  });

  it("shows the title total and a view-all link", () => {
    renderFiles(
      makeFiles({
        items: [makeFile(), makeFile({ id: "file-2", episode: 2, episode_label: "S01E02" })],
        total_size_bytes: 2048,
      }),
    );

    expect(screen.getByRole("heading", { name: "Downloaded" })).toBeInTheDocument();
    expect(screen.getByText(/2 files · 2.00 KB/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "View all files (2)" });
    expect(link).toHaveAttribute("href", "/title/title-1/files");
  });

  it("stacks the count under the heading and hides view-all for a single file", () => {
    renderFiles(makeFiles());

    const heading = screen.getByRole("heading", { name: "Downloaded" });
    const meta = screen.getByText(/1 file · 1.00 KB/);
    expect(heading.parentElement).toBe(meta.parentElement);
    expect(screen.queryByRole("link", { name: /View all files/ })).not.toBeInTheDocument();
  });

  it("uses real file thumbnails in the carousel", () => {
    const { container } = renderFiles(
      makeFiles({
        items: [makeFile({ id: "shot", episode: 1 })],
      }),
    );

    const image = container.querySelector('img[src*="/thumbnail"]');
    expect(image).toHaveAttribute("src", "/api/media-files/shot/thumbnail");
    expect(image).toHaveAttribute("loading", "lazy");
  });

  it("does not render season pills for a single-season title", () => {
    renderFiles(makeFiles());

    expect(screen.queryByRole("group", { name: /filter downloaded files/i })).not.toBeInTheDocument();
    expect(screen.getByTestId("media-file-card-file-1")).toBeInTheDocument();
  });

  it("renders season pills and defaults to the newest season for multi-season titles", () => {
    const files = makeFiles({
      items: [
        makeFile({ id: "s1", season: 1, episode_label: "S01E01", file_name: "s01e01.mkv" }),
        makeFile({ id: "s2", season: 2, episode_label: "S02E01", file_name: "s02e01.mkv" }),
      ],
      seasons: [
        { library_season: 1, file_count: 1, total_size_bytes: 0 },
        { library_season: 2, file_count: 1, total_size_bytes: 0 },
      ],
    });

    renderFiles(files);

    const pills = screen.getByRole("group", { name: /filter downloaded files/i });
    expect(pills).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Season 1" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: "Season 2" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByTestId("media-file-card-s2")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-card-s1")).not.toBeInTheDocument();
  });

  it("follows the open episode tab's season", () => {
    const files = makeFiles({
      items: [
        makeFile({ id: "s1", season: 1, episode_label: "S01E01", file_name: "s01e01.mkv" }),
        makeFile({ id: "s2", season: 2, episode_label: "S02E01", file_name: "s02e01.mkv" }),
      ],
      seasons: [
        { library_season: 1, file_count: 1, total_size_bytes: 0 },
        { library_season: 2, file_count: 1, total_size_bytes: 0 },
      ],
    });

    renderFiles(files, { activeSeason: 1 });

    expect(screen.getByTestId("media-file-card-s1")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-card-s2")).not.toBeInTheDocument();
  });

  it("hides extras while keeping the header total", () => {
    const files = makeFiles({
      items: [
        makeFile({ id: "ep", season: 1, episode_label: "S01E01" }),
        makeFile({
          id: "extra",
          season: null,
          episode: null,
          episode_id: null,
          episode_label: null,
          episode_name: null,
          file_name: "behind-the-scenes.mkv",
          has_episode: false,
        }),
      ],
    });

    renderFiles(files);

    expect(screen.getByText("2 files · 2.00 KB")).toBeInTheDocument();
    expect(screen.getByTestId("media-file-card-ep")).toBeInTheDocument();
    expect(screen.queryByTestId("media-file-card-extra")).not.toBeInTheDocument();
  });

  it("features a movie's primary file as one wide hero card", () => {
    const files = makeFiles({
      items: [
        makeFile({
          id: "main",
          season: null,
          episode: null,
          episode_id: null,
          episode_label: null,
          episode_name: null,
          file_name: "Big Buck Bunny.mkv",
          has_episode: false,
          is_primary: true,
        }),
        makeFile({
          id: "sample",
          season: null,
          episode: null,
          episode_id: null,
          episode_label: null,
          episode_name: null,
          file_name: "sample.mkv",
          has_episode: false,
          is_primary: false,
        }),
      ],
      seasons: [],
    });

    render(
      <MemoryRouter>
        <DownloadedFiles files={files} titleKind="movie" />
      </MemoryRouter>,
    );

    const hero = screen.getByTestId("media-file-card-main");
    expect(hero).toHaveClass("max-w-[560px]", "rounded-panel");
    expect(screen.queryByTestId("media-file-card-sample")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Downloaded files" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View all files/ })).toBeInTheDocument();
  });
});
