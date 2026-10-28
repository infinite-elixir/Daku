import { beforeAll, describe, expect, it } from "vitest";

import {
  BAYER_4X4,
  BAYER_8X8,
  DITHER_CACHE_SIZE,
  MAX_DITHER_HEIGHT,
  MAX_DITHER_WIDTH,
  bayerMatrix,
  clampDitherSize,
  clearDitherCache,
  daySeed,
  ditherCacheKey,
  ditherPixelRatio,
  ditherPixels,
  getCachedDither,
  hashString,
  luminance,
  paletteFor,
  proceduralAscii,
  selectBackdropIndex,
  setCachedDither,
} from "../dither";

const THEME_TOKENS: Record<string, string> = {
  "--color-canvas-deep": "#0c0e14",
  "--color-panel": "#1a1b26",
  "--color-fg-muted": "#a9b1d6",
  "--color-fg-comment": "#565f89",
  "--color-kind-movie": "#ff9e64",
  "--color-kind-series": "#7dcfff",
  "--color-kind-anime": "#bb9af7",
};

beforeAll(() => {
  for (const [token, value] of Object.entries(THEME_TOKENS)) {
    document.documentElement.style.setProperty(token, value);
  }
});

function channels(hex: string): [number, number, number] {
  const raw = hex.replace("#", "");
  return [
    Number.parseInt(raw.slice(0, 2), 16),
    Number.parseInt(raw.slice(2, 4), 16),
    Number.parseInt(raw.slice(4, 6), 16),
  ];
}

function solid(width: number, height: number, value: number): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let index = 0; index < pixels.length; index += 4) {
    pixels[index] = value;
    pixels[index + 1] = value;
    pixels[index + 2] = value;
    pixels[index + 3] = 255;
  }
  return pixels;
}

describe("Bayer matrices", () => {
  it("contains every threshold exactly once", () => {
    for (const [matrix, size] of [
      [BAYER_4X4, 4],
      [BAYER_8X8, 8],
    ] as const) {
      const values = matrix.flat().sort((left, right) => left - right);
      expect(values).toEqual(Array.from({ length: size * size }, (_, index) => index));
    }
  });

  it("returns the requested matrix", () => {
    expect(bayerMatrix(4)).toBe(BAYER_4X4);
    expect(bayerMatrix(8)).toBe(BAYER_8X8);
  });
});

describe("size caps", () => {
  it("caps both dimensions at the coarse render limit", () => {
    expect(clampDitherSize(900, 500)).toEqual({
      width: MAX_DITHER_WIDTH,
      height: MAX_DITHER_HEIGHT,
    });
  });

  it("never goes below one pixel", () => {
    expect(clampDitherSize(0, -10)).toEqual({ width: 1, height: 1 });
  });

  it("caps the canvas backing store at 2× device pixels", () => {
    const original = window.devicePixelRatio;
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 3 });
    expect(ditherPixelRatio()).toBe(2);
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 1.5 });
    expect(ditherPixelRatio()).toBe(1.5);
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: original });
  });
});

describe("palette", () => {
  it("uses a low-intensity kind accent and never volt", () => {
    const movie = paletteFor("movie");
    const series = paletteFor("series");
    const anime = paletteFor("anime");

    expect(new Set([movie.accent, series.accent, anime.accent]).size).toBe(3);
    for (const palette of [movie, series, anime, paletteFor("neutral")]) {
      expect(palette.deep.toLowerCase()).not.toBe("#c8ff3c");
      expect(palette.accent.toLowerCase()).not.toBe("#c8ff3c");
    }
  });

  it("weights luminance towards green", () => {
    expect(luminance(0, 255, 0)).toBeGreaterThan(luminance(255, 0, 0));
    expect(luminance(0, 0, 255)).toBeLessThan(luminance(255, 0, 0));
    expect(luminance(0, 0, 0)).toBe(0);
  });
});

describe("ditherPixels", () => {
  it("is deterministic for the same pixels, size, palette and seed", () => {
    const pixels = solid(16, 12, 128);
    const first = ditherPixels(pixels, 16, 12, paletteFor("series"), 42);
    const second = ditherPixels(pixels, 16, 12, paletteFor("series"), 42);

    expect(first).toEqual(second);
    expect(first).toHaveLength(16 * 12 * 4);
  });

  it("maps black to the deep token and white to the accent", () => {
    const palette = paletteFor("movie");
    const black = ditherPixels(solid(4, 4, 0), 4, 4, palette, 1);
    const white = ditherPixels(solid(4, 4, 255), 4, 4, palette, 1);

    expect(Array.from(black.slice(0, 3))).toEqual(channels(palette.deep));
    expect(Array.from(white.slice(0, 3))).not.toEqual(channels(palette.deep));
    expect(Array.from(white.slice(0, 3))).not.toEqual(channels(palette.panel));
  });

  it("preserves alpha", () => {
    const pixels = solid(2, 2, 90);
    pixels[3] = 10;
    const result = ditherPixels(pixels, 2, 2, paletteFor("neutral"), 0);
    expect(result[3]).toBe(10);
  });
});

describe("procedural fallback", () => {
  it("is deterministic and sized from the requested cell grid", () => {
    const first = proceduralAscii("Big Buck Bunny", 2008, 40, 12, 7);
    const second = proceduralAscii("Big Buck Bunny", 2008, 40, 12, 7);

    expect(first).toEqual(second);
    expect(first).toHaveLength(12);
    expect(first[0]).toHaveLength(40);
    expect(first.join("").trim().length).toBeGreaterThan(0);
  });

  it("changes with the title or seed", () => {
    expect(proceduralAscii("Big Buck Bunny", 2008, 24, 6, 1)).not.toEqual(
      proceduralAscii("Big Buck Bunny", 2008, 24, 6, 2),
    );
    expect(proceduralAscii("Big Buck Bunny", 2008, 24, 6, 1)).not.toEqual(
      proceduralAscii("Other Film", 2008, 24, 6, 1),
    );
  });
});

describe("backdrop selection", () => {
  it("uses a stable daily seed and an in-range index", () => {
    expect(daySeed(new Date("2026-10-28T23:00:00Z"))).toBe("2026-10-28");
    const first = selectBackdropIndex(9, daySeed(new Date("2026-10-28T01:00:00Z")));
    const second = selectBackdropIndex(9, daySeed(new Date("2026-10-28T22:00:00Z")));

    expect(first).toBe(second);
    expect(first).toBeGreaterThanOrEqual(0);
    expect(first).toBeLessThan(9);
    expect(selectBackdropIndex(0, "any")).toBe(-1);
  });
});

describe("in-memory cache", () => {
  it("evicts the least recently used entry past the cap", () => {
    clearDitherCache();
    for (let index = 0; index < DITHER_CACHE_SIZE; index += 1) {
      setCachedDither(ditherCacheKey(`poster-${index}`, 160, 90, "movie"), `url-${index}`);
    }
    const firstKey = ditherCacheKey("poster-0", 160, 90, "movie");
    expect(getCachedDither(firstKey)).toBe("url-0");

    setCachedDither(ditherCacheKey("poster-new", 160, 90, "movie"), "url-new");

    expect(getCachedDither(firstKey)).toBe("url-0");
    expect(getCachedDither(ditherCacheKey("poster-1", 160, 90, "movie"))).toBeUndefined();
    expect(getCachedDither(ditherCacheKey("poster-new", 160, 90, "movie"))).toBe("url-new");
    clearDitherCache();
  });

  it("hashes the same string to the same number", () => {
    expect(hashString("daku")).toBe(hashString("daku"));
  });
});
