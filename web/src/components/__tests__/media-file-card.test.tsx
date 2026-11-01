import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MediaFileCard } from "../MediaFileCard";
import { PlayerContext } from "../PlayerProvider";
import type { MediaFileItem, PlaybackState } from "../../lib/api";

const PLAYER = { mpv_enabled: true, mpv_bin: "mpv", mpv_path: "/usr/bin/mpv" };

function makeFile(overrides: Partial<MediaFileItem> = {}): MediaFileItem {
  return {
    id: overrides.id ?? "f1",
    download_id: "d1",
    episode_id: "e1",
    episode_label: overrides.episode_label ?? "S01E01",
    episode_name: overrides.episode_name ?? "Let You Down",
    season: 1,
    episode: 1,
    file_name: overrides.file_name ?? "s01e01.mkv",
    size_bytes: 1024,
    is_primary: true,
    has_episode: overrides.has_episode ?? true,
  };
}

function renderCard(card: ReactNode, enabled = false) {
  return render(
    <MemoryRouter>
      {enabled ? (
        <PlayerContext.Provider value={PLAYER}>{card}</PlayerContext.Provider>
      ) : (
        <PlayerContext.Provider value={null}>{card}</PlayerContext.Provider>
      )}
    </MemoryRouter>,
  );
}

function okPlay(): Response {
  return {
    ok: true,
    status: 202,
    json: async () => ({ media_file_id: "f1", launched: true }),
  } as Response;
}

describe("MediaFileCard", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    // jsdom does not implement PointerEvent; mouse events carry the same coordinates.
    vi.stubGlobal("PointerEvent", MouseEvent);
  });

  it("navigates to the title page when the player is disabled", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderCard(<MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />);

    const card = screen.getByTestId("media-file-card-f1");
    expect(card.tagName).toBe("A");
    expect(card).toHaveAttribute("href", "/title/t1/files#media-file-f1");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("launches mpv from a button when the player is enabled", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okPlay());
    vi.stubGlobal("fetch", fetchMock);

    renderCard(
      <MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />,
      true,
    );

    const card = screen.getByTestId("media-file-card-f1");
    expect(card.tagName).toBe("BUTTON");
    fireEvent.click(card);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/media-files/f1/play",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("ignores a click that moved further than the drag threshold", () => {
    const fetchMock = vi.fn().mockResolvedValue(okPlay());
    vi.stubGlobal("fetch", fetchMock);

    renderCard(
      <MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />,
      true,
    );

    const card = screen.getByTestId("media-file-card-f1");
    fireEvent.pointerDown(card, { clientX: 10, clientY: 10 });
    fireEvent.click(card, { clientX: 40, clientY: 10 });
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.pointerDown(card, { clientX: 10, clientY: 10 });
    fireEvent.click(card, { clientX: 12, clientY: 12 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a busy state and ignores a second click while pending", async () => {
    let resolve!: (value: Response) => void;
    const fetchMock = vi.fn(
      () => new Promise<Response>((done) => {
        resolve = done;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderCard(
      <MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />,
      true,
    );

    const card = screen.getByTestId("media-file-card-f1");
    fireEvent.click(card);

    expect(card).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Opening…")).toBeInTheDocument();
    fireEvent.click(card);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(okPlay());
    await waitFor(() => expect(card).toHaveAttribute("aria-busy", "false"));
    expect(screen.queryByText("Opening…")).not.toBeInTheDocument();
  });

  it("shows the server message as a toast when the launch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          code: "MPV_UNAVAILABLE",
          message: "mpv is not installed or is not on PATH",
        }),
      }),
    );

    renderCard(
      <MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />,
      true,
    );

    fireEvent.click(screen.getByTestId("media-file-card-f1"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "mpv is not installed or is not on PATH",
    );
  });

  it("keeps the hero variant clickable as a button", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okPlay());
    vi.stubGlobal("fetch", fetchMock);

    renderCard(
      <MediaFileCard
        file={makeFile({ has_episode: false, episode_name: null, file_name: "Film.mkv" })}
        titleId="t1"
        titleKind="movie"
        variant="hero"
      />,
      true,
    );

    const card = screen.getByTestId("media-file-card-f1");
    expect(card.tagName).toBe("BUTTON");
    expect(card).toHaveClass("max-w-[560px]");
    fireEvent.click(card);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it("shows a play badge on hover only when the player is enabled", () => {
    vi.stubGlobal("fetch", vi.fn());

    const { unmount } = renderCard(
      <MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />,
      true,
    );
    expect(screen.getByTestId("media-file-play-overlay-f1")).toBeInTheDocument();
    unmount();

    renderCard(<MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />);
    expect(screen.queryByTestId("media-file-play-overlay-f1")).not.toBeInTheDocument();
  });

  it("draws a seek bar from the persisted position", () => {
    vi.stubGlobal("fetch", vi.fn());

    renderCard(
      <MediaFileCard
        file={{
          ...makeFile(),
          playback: { position_s: 150, duration_s: 600, updated_at: null },
        }}
        titleId="t1"
        titleKind="series"
      />,
    );

    const bar = screen.getByTestId("media-file-progress-f1");
    expect(bar).toHaveAttribute("title", "2:30 of 10:00");
    expect(bar.firstElementChild).toHaveStyle({ width: "25.0%" });
  });

  it("prefers the live player position over the persisted one", () => {
    vi.stubGlobal("fetch", vi.fn());
    const live: PlaybackState = {
      media_file_id: "f1",
      position_s: 300,
      duration_s: 600,
      playing: true,
      updated_at: "2026-11-01T10:00:00Z",
    };

    renderCard(
      <MediaFileCard
        file={{
          ...makeFile(),
          playback: { position_s: 150, duration_s: 600, updated_at: null },
        }}
        titleId="t1"
        titleKind="series"
        livePlayback={live}
      />,
    );

    const bar = screen.getByTestId("media-file-progress-f1");
    expect(bar.firstElementChild).toHaveStyle({ width: "50.0%" });
  });

  it("draws no seek bar without a known position", () => {
    vi.stubGlobal("fetch", vi.fn());

    renderCard(<MediaFileCard file={makeFile()} titleId="t1" titleKind="series" />);

    expect(screen.queryByTestId("media-file-progress-f1")).not.toBeInTheDocument();
  });
});
