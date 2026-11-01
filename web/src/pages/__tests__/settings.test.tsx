import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReleaseSettings } from "../../lib/api";
import { SettingsPage } from "../SettingsPage";

function makeSettings(overrides: Partial<ReleaseSettings["player"]> = {}): ReleaseSettings {
  return {
    release: {
      providers: { movie: ["yts"], series: ["eztv"], anime: ["torrentio"] },
      config_providers: { movie: ["yts"], series: ["eztv"], anime: ["torrentio"] },
      min_quality: null,
      sort: "seeders",
      limit: 20,
    },
    sources: {
      database: true,
      config_keys: ["providers", "sort", "limit"],
      anime_sources: ["torrentio"],
    },
    player: {
      mpv_enabled: true,
      mpv_bin: "mpv",
      mpv_path: "/usr/bin/mpv",
      ...overrides,
    },
  };
}

function renderPage(payload: ReleaseSettings) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as {
        player?: { mpv_enabled: boolean };
      };
      return {
        ok: true,
        status: 200,
        json: async () =>
          makeSettings({ mpv_enabled: body.player?.mpv_enabled ?? true }),
      };
    }
    return { ok: true, status: 200, json: async () => payload };
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, fetchMock };
}

describe("SettingsPage player", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the mpv toggle as editable and saves it", async () => {
    const { fetchMock } = renderPage(makeSettings());

    const toggle = await screen.findByRole("checkbox", {
      name: /Launch downloaded files in mpv/i,
    });
    expect(toggle).toBeChecked();
    expect(toggle).not.toBeDisabled();
    expect(screen.getByText("mpv")).toBeInTheDocument();
    expect(screen.getByText("/usr/bin/mpv")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH"),
      ).toBe(true),
    );
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({
      player: { mpv_enabled: false },
    });

    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it("reports an enabled player whose binary is not on PATH", async () => {
    renderPage(makeSettings({ mpv_path: null }));

    expect(
      await screen.findByText("Not found on PATH or as an absolute path"),
    ).toBeInTheDocument();
  });
});
