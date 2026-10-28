import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DitherCanvas } from "../DitherCanvas";
import { clearDitherCache } from "../../lib/dither";

type ImageBehaviour = (url: string) => "load" | "error";

let behaviour: ImageBehaviour = () => "load";
let taintedReads = 0;

class FakeImage {
  static instances: FakeImage[] = [];

  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  crossOrigin: string | null = null;
  decoding = "";
  naturalWidth = 300;
  naturalHeight = 450;
  private source = "";

  constructor() {
    FakeImage.instances.push(this);
  }

  set src(value: string) {
    this.source = value;
    const action = behaviour(value);
    window.setTimeout(() => {
      if (action === "load") {
        this.onload?.();
      } else {
        this.onerror?.();
      }
    }, 0);
  }

  get src(): string {
    return this.source;
  }
}

function stubCanvas(): void {
  const getImageData = vi.fn((_x: number, _y: number, width: number, height: number) => {
    const shouldThrow = taintedReads > 0;
    taintedReads -= 1;
    if (shouldThrow) {
      throw new DOMException("The canvas is tainted", "SecurityError");
    }
    return {
      data: new Uint8ClampedArray(width * height * 4).fill(255),
      width,
      height,
    };
  });
  const context = {
    drawImage: vi.fn(),
    getImageData,
    putImageData: vi.fn(),
    imageSmoothingEnabled: true,
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    context as unknown as CanvasRenderingContext2D,
  );
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,AAAA",
  );
}

function renderCanvas(posterUrl: string | null = "https://images.example/bbb.jpg") {
  return render(
    <DitherCanvas
      posterUrl={posterUrl}
      title="Big Buck Bunny"
      year={2008}
      kind="movie"
      width={160}
      height={90}
      seed={7}
    />,
  );
}

describe("DitherCanvas", () => {
  beforeEach(() => {
    clearDitherCache();
    FakeImage.instances = [];
    behaviour = () => "load";
    taintedReads = 0;
    vi.stubGlobal("Image", FakeImage);
    stubCanvas();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    clearDitherCache();
  });

  it("loads a poster directly and renders the dithered result", async () => {
    const { container } = renderCanvas();

    await waitFor(() => {
      expect(screen.getByTestId("dither-canvas")).toHaveAttribute("data-dither-state", "ready");
    });
    expect(FakeImage.instances).toHaveLength(1);
    expect(FakeImage.instances[0]?.crossOrigin).toBe("anonymous");
    expect(screen.getByTestId("dither-canvas-image")).toHaveStyle({
      backgroundImage: 'url("data:image/png;base64,AAAA")',
    });
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });

  it("retries a tainted CORS poster through the same-origin proxy", async () => {
    // The direct read throws once; the proxy read succeeds.
    taintedReads = 1;
    renderCanvas();

    await waitFor(() => {
      expect(screen.getByTestId("dither-canvas")).toHaveAttribute("data-dither-state", "ready");
    });
    const sources = FakeImage.instances.map((image) => image.src);
    expect(sources).toHaveLength(2);
    expect(sources[1]).toContain("/api/posters/proxy?url=");
    expect(sources[1]).toContain(encodeURIComponent("https://images.example/bbb.jpg"));
  });

  it("falls back to procedural ASCII when both loads fail", async () => {
    behaviour = () => "error";
    renderCanvas();

    const fallback = await screen.findByTestId("dither-fallback");
    expect(fallback.textContent?.trim().length).toBeGreaterThan(0);
    expect(screen.getByTestId("dither-canvas")).toHaveAttribute("data-dither-state", "ascii");
    expect(screen.queryByTestId("dither-canvas-image")).not.toBeInTheDocument();
  });

  it("renders procedural ASCII for a title with no poster", async () => {
    renderCanvas(null);

    const fallback = await screen.findByTestId("dither-fallback");
    expect(fallback.textContent?.trim().length).toBeGreaterThan(0);
    expect(FakeImage.instances).toHaveLength(0);
  });

  it("reuses the in-memory cache instead of loading the poster again", async () => {
    const first = renderCanvas();
    await screen.findByTestId("dither-canvas-image");
    first.unmount();

    renderCanvas();
    await screen.findByTestId("dither-canvas-image");

    expect(FakeImage.instances).toHaveLength(1);
  });

  it("keeps the decorative wrapper out of the accessibility tree", async () => {
    renderCanvas();

    const wrapper = screen.getByTestId("dither-canvas");
    expect(wrapper).toHaveAttribute("aria-hidden", "true");
  });
});
