import { fireEvent, render, screen, within } from "@testing-library/react";
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
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={vi.fn()} />);

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
        onRemove={vi.fn()}
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
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText("Whole pack")).toBeInTheDocument();
  });

  it("lists every episode this torrent has already downloaded", () => {
    render(
      <DownloadRow
        download={record({
          status: "complete",
          progress: 1,
          downloaded_files: [
            {
              id: "m1",
              episode_id: "e1",
              episode_label: "S01E01",
              file_name: "Cyberpunk.S01E01.mkv",
              size_bytes: 900_000_000,
              is_primary: false,
            },
            {
              id: "m2",
              episode_id: "e2",
              episode_label: "S01E02",
              file_name: "Cyberpunk.S01E02.mkv",
              size_bytes: 1_100_000_000,
              is_primary: true,
            },
          ],
        })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText("Downloaded")).toBeInTheDocument();
    expect(screen.getByText("S01E01")).toBeInTheDocument();
    expect(screen.getByText("S01E02")).toBeInTheDocument();
    expect(screen.getByText("S01E01").getAttribute("title")).toContain("Cyberpunk.S01E01.mkv");
  });

  it("confirms before removing and defaults the file-delete checkbox off", () => {
    const onRemove = vi.fn();
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));

    const dialog = screen.getByRole("dialog", { name: "Remove download?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByText(
        "This removes the queue entry. Files already in your library stay where they are.",
      ),
    ).toBeInTheDocument();
    const checkbox = screen.getByRole("checkbox", {
      name: /Also delete the downloaded files/i,
    });
    expect(checkbox).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(onRemove).toHaveBeenCalledWith("d1", false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("passes delete_files when the checkbox is selected", () => {
    const onRemove = vi.fn();
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Also delete the downloaded files/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(onRemove).toHaveBeenCalledWith("d1", true);
  });

  it("closes the remove dialog with Escape or Cancel", () => {
    const onRemove = vi.fn();
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onRemove).not.toHaveBeenCalled();
  });

  it("hides the file-delete option for direct downloads", () => {
    render(
      <DownloadRow
        download={record({ engine: "aria2", source: "direct" })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
