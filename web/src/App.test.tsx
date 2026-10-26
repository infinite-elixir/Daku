import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the downloads navigation entry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "ok",
          version: "0.1.0",
          uptime_s: 12,
          data_dir: "/tmp/daku",
        }),
      }),
    );

    renderApp();

    const links = await screen.findAllByRole("link", { name: "Downloads" });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/downloads");
    }
  });

  it("renders the health card with the API response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "ok",
          version: "0.1.0",
          uptime_s: 12,
          data_dir: "/tmp/daku",
        }),
      }),
    );

    renderApp();

    expect(await screen.findByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("/tmp/daku")).toBeInTheDocument();
    expect(screen.getByText("System health")).toBeInTheDocument();
  });
});
