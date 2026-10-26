import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CopyMagnet, shortMagnet } from "../CopyMagnet";

const MAGNET = `magnet:?xt=urn:btih:${"a".repeat(40)}&dn=Example&tr=udp%3A%2F%2Ftracker.example%3A80`;

describe("CopyMagnet", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("copies the full magnet through the Clipboard API and announces success", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    render(<CopyMagnet magnet={MAGNET} />);

    const button = screen.getByRole("button", { name: "Copy magnet link" });
    expect(button).toBeEnabled();
    expect(screen.getByText("magnet:?xt=urn:btih:aaaa…")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(MAGNET);

    fireEvent.click(button);

    expect(await screen.findByRole("status")).toHaveTextContent("Copied");
    expect(writeText).toHaveBeenCalledWith(MAGNET);
  });

  it("falls back to execCommand when the Clipboard API is denied", async () => {
    const writeText = vi.fn(async () => {
      throw new Error("NotAllowedError");
    });
    const execCommand = vi.fn(() => true);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(<CopyMagnet magnet={MAGNET} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy magnet link" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Copied");
    expect(writeText).toHaveBeenCalled();
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(document.body.textContent).not.toContain(MAGNET);
  });

  it("clears Copied after two seconds", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    render(<CopyMagnet magnet={MAGNET} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy magnet link" }));
    });

    expect(screen.getByRole("status")).toHaveTextContent("Copied");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders a disabled button with a hint when the magnet is missing", () => {
    render(<CopyMagnet magnet={null} />);

    const button = screen.getByRole("button", { name: "Copy magnet link" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "No magnet is stored for this release");
    expect(screen.getByText("no magnet")).toBeInTheDocument();
  });

  it("never renders the full magnet in a label", () => {
    expect(shortMagnet(MAGNET)).toBe("magnet:?xt=urn:btih:aaaa…");
    expect(shortMagnet(null)).toBe("magnet missing");
  });
});
