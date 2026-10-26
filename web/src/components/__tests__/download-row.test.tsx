import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DownloadRecord } from "../../lib/api";
import { DownloadRow } from "../DownloadRow";

function record(overrides: Partial<DownloadRecord> = {}): DownloadRecord {
  return {
    id: "d1",
    release_id: "r1",
    source: "torrent",
    status: "active",
    engine: "qbittorrent",
    external_id: "a".repeat(40),
    progress: 0.2,
    downloaded_bytes: 100,
    total_bytes: 1000,
    speed_bps: 50,
    eta_seconds: 18,
    error: null,
    created_at: "2026-10-25T00:00:00Z",
    updated_at: "2026-10-25T00:00:00Z",
    title_id: "t1",
    title: "Cyberpunk",
    episode_id: "e1",
    episode_label: "S02E04",
    quality: "1080p",
    filename: "Cyberpunk.S02.1080p.mkv",
    selection_label: "S02E04 only",
    selection_size_bytes: 1_500_000_000,
    ...overrides,
  };
}

describe("DownloadRow selective downloads", () => {
  it("shows the selected episode and its size", () => {
    render(<DownloadRow download={record()} onAction={vi.fn()} />);

    const label = screen.getByText("S02E04 only · 1.40 GB");
    expect(label).toBeInTheDocument();
    expect(label.getAttribute("title")).toContain("whole torrent");
    expect(screen.getByRole("link", { name: "Files" })).toBeInTheDocument();
  });

  it("replaces a raw selection failure with clear copy", () => {
    render(
      <DownloadRow
        download={record({
          status: "failed",
          error: "QBT_FILE_SELECTION_FAILED: qBittorrent did not apply the file priorities",
        })}
        onAction={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Could not restrict this torrent to S02E04. Nothing is downloading."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/QBT_FILE_SELECTION_FAILED/)).not.toBeInTheDocument();
  });

  it("labels a whole pack", () => {
    render(
      <DownloadRow
        download={record({
          selected_files: null,
          selection_label: "Whole pack",
          selection_size_bytes: null,
        })}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Whole pack")).toBeInTheDocument();
  });
});
