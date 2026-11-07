import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MagnetFiles } from "../MagnetFiles";

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
  name: "Example Season 1",
  total_size_bytes: 3_000,
  piece_length: 262144,
  private: false,
  files: [
    { index: 0, path: "Season 01/Episode 01.mkv", size_bytes: 1_000 },
    { index: 1, path: "Season 01/Episode 02.mkv", size_bytes: 2_000 },
  ],
  fetched_at: "2026-10-26T00:00:00Z",
  cached: false,
  source: "probe" as const,
  release_id: "release-1",
};

describe("MagnetFiles", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("expands inline beneath the row and shows the real file list", async () => {
    let resolveFetch: ((value: Response) => void) | undefined;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <ul>
        <li data-testid="release-row">
          <span>Release row</span>
          <MagnetFiles magnet={`magnet:?xt=urn:btih:${"a".repeat(40)}`} releaseId="release-1" />
        </li>
      </ul>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Files" }));

    expect(screen.getByText("Reading torrent metadata…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/releases/release-1/files",
      expect.objectContaining({ signal: expect.anything() }),
    );

    await act(async () => {
      resolveFetch?.(jsonResponse(METADATA));
    });

    expect(
      (await screen.findAllByText("Season 01/Episode 01.mkv")).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Example Season 1")).toBeInTheDocument();
    expect(screen.getByText(/2 files/)).toBeInTheDocument();
    expect(screen.getByText("Metadata only — nothing was downloaded")).toBeInTheDocument();
    expect(screen.getByTestId("release-row")).toContainElement(
      screen.getAllByText("Season 01/Episode 02.mkv")[0]!,
    );

    // The desktop table and the 390px stacked list are both present.
    const stacked = document.querySelector("ul.sm\\:hidden");
    expect(stacked).not.toBeNull();
    expect(stacked).toHaveTextContent("Season 01/Episode 01.mkv");
  });

  it("fetches the file list as soon as a defaultOpen release is shown", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(METADATA));
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(
      <MagnetFiles
        magnet={`magnet:?xt=urn:btih:${"f".repeat(40)}`}
        releaseId="release-1"
        defaultOpen
      />,
    );

    expect(
      (await screen.findAllByText("Season 01/Episode 01.mkv")).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "Files" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/releases/release-1/files",
      expect.objectContaining({ signal: expect.anything() }),
    );
  });

  it("shows a single file for a single-file torrent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          ...METADATA,
          name: "Movie.mkv",
          total_size_bytes: 8_000,
          files: [{ index: 0, path: "Movie.mkv", size_bytes: 8_000 }],
        }),
      ),
    );

    renderWithClient(<MagnetFiles magnet={`magnet:?xt=urn:btih:${"b".repeat(40)}`} />);
    fireEvent.click(screen.getByRole("button", { name: "Files" }));

    expect(
      (await screen.findAllByText("Movie.mkv")).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/1 file/)).toBeInTheDocument();
    expect(screen.getByText(/Metadata only/)).toBeInTheDocument();
    expect(screen.getByText(/Metadata only/)).toBeInTheDocument();
  });

  it("shows the stable error code and retries", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ code: "PROBE_TIMEOUT", message: "The magnet did not answer" }, 504),
      )
      .mockResolvedValueOnce(jsonResponse(METADATA));
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<MagnetFiles magnet={`magnet:?xt=urn:btih:${"c".repeat(40)}`} />);
    fireEvent.click(screen.getByRole("button", { name: "Files" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The magnet did not answer");
    expect(screen.getByText("PROBE_TIMEOUT")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(
      (await screen.findAllByText("Season 01/Episode 01.mkv")).length,
    ).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("caps a hung request at the deadline and offers Retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      ),
    );

    renderWithClient(
      <MagnetFiles magnet={`magnet:?xt=urn:btih:${"d".repeat(40)}`} timeoutMs={20} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    expect(screen.getByText("Reading torrent metadata…")).toBeInTheDocument();

    expect(await screen.findByRole("alert")).toHaveTextContent("timed out after 30 seconds");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("cancels a running probe and closes the panel", async () => {
    let aborted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              aborted = true;
              reject(new DOMException("aborted", "AbortError"));
            });
          }),
      ),
    );

    renderWithClient(<MagnetFiles magnet={`magnet:?xt=urn:btih:${"e".repeat(40)}`} />);
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Reading torrent metadata…")).not.toBeInTheDocument();
    await waitFor(() => expect(aborted).toBe(true));
  });

  it("disables Files when there is no magnet or release", () => {
    renderWithClient(<MagnetFiles magnet={null} />);
    expect(screen.getByRole("button", { name: "Files" })).toBeDisabled();
  });
});
