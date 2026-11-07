import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DownloadFile, DownloadRecord } from "../../lib/api";
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
    can_delete_source_files: false,
    delete_files_reason: "no_library_copy",
    ...overrides,
  };
}

function file(overrides: Partial<DownloadFile> = {}): DownloadFile {
  return {
    id: "f1",
    episode_id: "e1",
    episode_label: "S01E01",
    file_name: "Cyberpunk.S01E01.mkv",
    size_bytes: 900_000_000,
    is_primary: false,
    ...overrides,
  };
}

describe("DownloadRow hierarchy", () => {
  it("shows the title, one meta line, progress, numbers and labelled fetching size", () => {
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={vi.fn()} />);

    const title = screen.getByRole("heading", { level: 4, name: "Cyberpunk" });
    expect(title).toHaveClass("text-title");
    expect(title).toHaveAttribute("title", "Cyberpunk");
    expect(screen.getByText("S02E04")).toBeInTheDocument();
    expect(screen.getByText("1080p")).toBeInTheDocument();
    expect(screen.getByText("Torrent")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /Cyberpunk progress/i })).toBeInTheDocument();
    expect(screen.getByText("Fetching S02E04 · 1.40 GB")).toBeInTheDocument();
    expect(screen.getByText("50 B/s")).toBeInTheDocument();
    expect(screen.getByText("ETA 18s")).toBeInTheDocument();
  });

  it("does not render the dead Files anchor", () => {
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={vi.fn()} />);

    expect(screen.queryByRole("link", { name: "Files" })).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="#release-files-"]')).not.toBeInTheDocument();
  });

  it("renders the library file labels as muted text, never accent pills", () => {
    render(
      <DownloadRow
        download={record({
          episode_label: "S01E03",
          selection_label: "S01E03 only",
          selection_size_bytes: 244_000_000,
          downloaded_files: [
            file({ id: "f1", episode_label: "S01E01" }),
            file({ id: "f2", episode_label: "S01E02" }),
            file({ id: "f3", episode_label: "S01E03" }),
          ],
        })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    const library = screen.getByText(/In library:/);
    expect(library).toHaveClass("text-fg-muted");
    expect(library).not.toHaveClass("bg-accent/15");
    expect(screen.getByText("S01E01")).toBeInTheDocument();
    expect(screen.getByText("S01E02")).toBeInTheDocument();
    expect(screen.getByText("S01E03", { selector: "span > span" })).toBeInTheDocument();
    expect(library.textContent).toContain("· 3 files");
    expect(library.textContent).not.toContain("Downloaded");
  });

  it("truncates the library line to three names plus the remainder", () => {
    render(
      <DownloadRow
        download={record({
          status: "complete",
          progress: 1,
          selection_label: null,
          selection_size_bytes: null,
          downloaded_files: [
            file({ id: "f1", episode_label: "S01E01" }),
            file({ id: "f2", episode_label: "S01E02" }),
            file({ id: "f3", episode_label: "S01E03" }),
            file({ id: "f4", episode_label: "S01E04" }),
            file({ id: "f5", episode_label: "S01E05" }),
          ],
        })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    const library = screen.getByText(/In library:/);
    expect(library.textContent).toContain("S01E01, S01E02, S01E03 +2");
    expect(library.textContent).not.toContain("S01E04");
  });

  it("collapses a single file into one line without an In library figure", () => {
    render(
      <DownloadRow
        download={record({
          status: "complete",
          progress: 1,
          episode_label: "S01E01",
          selection_label: null,
          selection_size_bytes: null,
          downloaded_files: [file({ id: "f1", episode_label: "S01E01" })],
        })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.queryByText(/In library:/)).not.toBeInTheDocument();
    expect(screen.getByText(/Downloaded .* 100%/)).toBeInTheDocument();
  });

  it("drops unknown quality and still describes the engine", () => {
    render(
      <DownloadRow
        download={record({ quality: null, episode_label: null, selection_label: null })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText("Torrent")).toBeInTheDocument();
    expect(screen.queryByText(/unknown/)).not.toBeInTheDocument();
  });

  it("labels a whole pack and shows no size when none is known", () => {
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

    expect(screen.getByText("Fetching Whole pack")).toBeInTheDocument();
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

  it("shows downloaded episodes only in the library line while the pack is active", () => {
    render(
      <DownloadRow
        download={record({
          status: "active",
          episode_label: "S01E03",
          selection_label: "S01E03 only",
          selection_size_bytes: 1_100_000_000,
          downloaded_files: [file({ id: "f1", episode_label: "S01E01" })],
        })}
        onAction={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText(/Fetching S01E03/)).toBeInTheDocument();
    expect(screen.getByText(/In library:/)).toBeInTheDocument();
    expect(screen.getByText("S01E01")).toBeInTheDocument();
    expect(screen.queryByText("Downloaded")).not.toBeInTheDocument();
  });
});

describe("DownloadRow actions", () => {
  it("confirms before removing and defaults the file-delete checkbox off", () => {
    const onRemove = vi.fn();
    render(
      <DownloadRow
        download={record({ can_delete_source_files: true })}
        onAction={vi.fn()}
        onRemove={onRemove}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));

    const dialog = screen.getByRole("dialog", { name: "Remove download?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByText(
        "This removes the queue entry. Files Daku has already organised into your library stay where they are.",
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
    render(
      <DownloadRow
        download={record({ can_delete_source_files: true })}
        onAction={vi.fn()}
        onRemove={onRemove}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Also delete the downloaded files/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(onRemove).toHaveBeenCalledWith("d1", true);
  });

  it("explains why files cannot be deleted and keeps them by default", () => {
    const onRemove = vi.fn();
    render(<DownloadRow download={record()} onAction={vi.fn()} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove download" }));

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "These files are still your only copy — Daku has not organised them into the library yet. Removing the queue entry will keep them.",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(onRemove).toHaveBeenCalledWith("d1", false);
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
