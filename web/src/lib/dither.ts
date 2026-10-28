/**
 * Deterministic poster dithering and procedural ASCII fallbacks.
 *
 * Everything here is pure or browser-only: no network calls, no storage. The canvas
 * result is cached in memory by the caller so re-renders do not flicker.
 */

import type { Kind } from "./api";

export type PaletteMode = Kind | "neutral";

export interface DitherPalette {
  /** --bg-deep */
  deep: string;
  /** --bg-panel */
  panel: string;
  /** --text-muted */
  muted: string;
  /** Kind colour, mixed down so it stays a low-intensity highlight. */
  accent: string;
}

export interface DitherOptions {
  width: number;
  height: number;
  palette: PaletteMode;
  seed?: number;
}

export const MAX_DITHER_WIDTH = 160;
export const MAX_DITHER_HEIGHT = 160;
export const DITHER_CACHE_SIZE = 24;

// Ordered Bayer matrices. Values are the classic 0..(n²-1) sequence.
export const BAYER_4X4: readonly (readonly number[])[] = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
];

export const BAYER_8X8: readonly (readonly number[])[] = [
  [0, 48, 12, 60, 3, 51, 15, 63],
  [32, 16, 44, 28, 35, 19, 47, 31],
  [8, 56, 4, 52, 11, 59, 7, 55],
  [40, 24, 36, 20, 43, 27, 39, 23],
  [2, 50, 14, 62, 1, 49, 13, 61],
  [34, 18, 46, 30, 33, 17, 45, 29],
  [10, 58, 6, 54, 9, 57, 5, 53],
  [42, 26, 38, 22, 41, 25, 37, 21],
];

const KIND_TOKENS: Record<Kind, string> = {
  movie: "--color-kind-movie",
  series: "--color-kind-series",
  anime: "--color-kind-anime",
};

const NEUTRAL_ACCENT_TOKEN = "--color-fg-comment";

function cssToken(name: string): string {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") {
    return "";
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Read the art palette from ``theme.css`` so a T-033 colour change is inherited here.
 * Canvas needs concrete colours, so the tokens are resolved once per call.
 */
export function paletteFor(mode: PaletteMode): DitherPalette {
  return {
    deep: cssToken("--color-canvas-deep"),
    panel: cssToken("--color-panel"),
    muted: cssToken("--color-fg-muted"),
    accent: cssToken(mode === "neutral" ? NEUTRAL_ACCENT_TOKEN : KIND_TOKENS[mode]),
  };
}

export function bayerMatrix(size: 4 | 8): readonly (readonly number[])[] {
  return size === 4 ? BAYER_4X4 : BAYER_8X8;
}

/** Keep the coarse render small; the CSS layer scales it back up. */
export function clampDitherSize(width: number, height: number): { width: number; height: number } {
  return {
    width: Math.max(1, Math.min(MAX_DITHER_WIDTH, Math.round(width))),
    height: Math.max(1, Math.min(MAX_DITHER_HEIGHT, Math.round(height))),
  };
}

export function luminance(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

export function hashString(input: string): number {
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function parseHex(color: string): [number, number, number] {
  const raw = color.replace("#", "");
  const value = Number.parseInt(raw.length === 3 ? raw.replace(/(.)/g, "$1$1") : raw, 16);
  return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

function mixHex(from: string, to: string, amount: number): string {
  const [r1, g1, b1] = parseHex(from);
  const [r2, g2, b2] = parseHex(to);
  const channel = (a: number, b: number) => Math.round(a + (b - a) * amount);
  return `#${[channel(r1, r2), channel(g1, g2), channel(b1, b2)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("")}`;
}

function paletteLevels(palette: DitherPalette): [number, number, number][] {
  return [
    parseHex(palette.deep),
    parseHex(palette.panel),
    parseHex(palette.muted),
    // Keep the kind colour as a low-intensity highlight, not a brand block.
    parseHex(mixHex(palette.panel, palette.accent, 0.32)),
  ];
}

/**
 * Ordered-dither an RGBA buffer into the four-colour palette.
 *
 * The same pixels, size, palette and seed always produce the same bytes. The Bayer
 * threshold supplies the texture; a tiny seeded noise only breaks up flat gradients.
 */
export function ditherPixels(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  palette: DitherPalette,
  seed = 0,
  matrixSize: 4 | 8 = 8,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(width * height * 4);
  const levels = paletteLevels(palette);
  const matrix = bayerMatrix(matrixSize);
  const denominator = matrixSize * matrixSize;
  const random = mulberry32(hashString(`${seed}|${width}x${height}`));

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const source = (y * width + x) * 4;
      const red = pixels[source] ?? 0;
      const green = pixels[source + 1] ?? 0;
      const blue = pixels[source + 2] ?? 0;
      const alpha = pixels[source + 3] ?? 255;
      const matrixRow = matrix[y % matrixSize] ?? BAYER_8X8[0] ?? [];
      const threshold = ((matrixRow[x % matrixSize] ?? 0) + 0.5) / denominator - 0.5;
      const noise = (random() - 0.5) * 0.035;
      const value = luminance(red, green, blue) / 255 + threshold * 0.28 + noise;
      const level = Math.max(0, Math.min(levels.length - 1, Math.floor(value * levels.length)));
      const colour = levels[level] ?? levels[0] ?? [0, 0, 0];
      const target = (y * width + x) * 4;
      output[target] = colour[0];
      output[target + 1] = colour[1];
      output[target + 2] = colour[2];
      output[target + 3] = alpha;
    }
  }

  return output;
}

const ASCII_GLYPHS = " .:-=+*#%@";

/** A deterministic glyph field derived only from the title, year and seed. */
export function proceduralAscii(
  title: string,
  year: number | null,
  width: number,
  height: number,
  seed = 0,
): string[] {
  const columns = Math.max(8, Math.round(width));
  const rows = Math.max(3, Math.round(height));
  const base = hashString(`${title}|${year ?? ""}|${seed}`);
  const random = mulberry32(base);
  const rowsOut: string[] = [];

  for (let y = 0; y < rows; y += 1) {
    let line = "";
    for (let x = 0; x < columns; x += 1) {
      const wave = Math.sin((x + (base % 17)) * 0.32) + Math.cos((y + (base % 13)) * 0.61);
      const value = Math.max(0, Math.min(0.999, (wave + 2) / 4 + (random() - 0.5) * 0.22));
      line += ASCII_GLYPHS[Math.floor(value * ASCII_GLYPHS.length)] ?? " ";
    }
    rowsOut.push(line);
  }
  return rowsOut;
}

/** Stable daily seed: reloads on the same day pick the same backdrop. */
export function daySeed(date: Date = new Date()): string {
  return date.toISOString().slice(0, 10);
}

export function selectBackdropIndex(count: number, seed: string): number {
  if (count <= 0) {
    return -1;
  }
  return hashString(seed) % count;
}

const ditherCache = new Map<string, string>();

export function ditherCacheKey(
  posterUrl: string,
  width: number,
  height: number,
  palette: PaletteMode,
  seed = 0,
): string {
  return `${posterUrl}|${width}|${height}|${palette}|${seed}`;
}

export function getCachedDither(key: string): string | undefined {
  const value = ditherCache.get(key);
  if (value !== undefined) {
    // Refresh insertion order for LRU eviction.
    ditherCache.delete(key);
    ditherCache.set(key, value);
  }
  return value;
}

export function setCachedDither(key: string, value: string): void {
  ditherCache.delete(key);
  ditherCache.set(key, value);
  while (ditherCache.size > DITHER_CACHE_SIZE) {
    const oldest = ditherCache.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    ditherCache.delete(oldest);
  }
}

export function clearDitherCache(): void {
  ditherCache.clear();
}

export function hasCanvasSupport(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  try {
    const canvas = document.createElement("canvas");
    return typeof canvas.getContext === "function" && canvas.getContext("2d") !== null;
  } catch {
    return false;
  }
}

/** The canvas backing store never exceeds 2× device pixels. */
export function ditherPixelRatio(): number {
  const ratio =
    typeof window !== "undefined" && typeof window.devicePixelRatio === "number"
      ? window.devicePixelRatio
      : 1;
  return Math.min(Math.max(ratio, 1), 2);
}

function drawCover(
  context: CanvasRenderingContext2D,
  source: CanvasImageSource,
  sourceWidth: number,
  sourceHeight: number,
  width: number,
  height: number,
): void {
  const sourceRatio = sourceWidth / Math.max(1, sourceHeight);
  const targetRatio = width / height;
  let sx = 0;
  let sy = 0;
  let sw = sourceWidth;
  let sh = sourceHeight;
  if (sourceRatio > targetRatio) {
    sw = Math.max(1, Math.round(sourceHeight * targetRatio));
    sx = Math.max(0, Math.round((sourceWidth - sw) / 2));
  } else if (sourceRatio < targetRatio) {
    sh = Math.max(1, Math.round(sourceWidth / targetRatio));
    sy = Math.max(0, Math.round((sourceHeight - sh) / 2));
  }
  context.drawImage(source, sx, sy, sw, sh, 0, 0, width, height);
}

/** Draw a loaded image into a small canvas and return a PNG data URL. */
export function renderDitherDataUrl(
  source: CanvasImageSource,
  sourceWidth: number,
  sourceHeight: number,
  options: DitherOptions,
): string {
  const ratio = ditherPixelRatio();
  const { width, height } = clampDitherSize(options.width * ratio, options.height * ratio);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) {
    throw new Error("canvas is unavailable");
  }
  context.imageSmoothingEnabled = true;
  drawCover(context, source, sourceWidth, sourceHeight, width, height);
  const image = context.getImageData(0, 0, width, height);
  const dithered = ditherPixels(
    image.data,
    width,
    height,
    paletteFor(options.palette),
    options.seed ?? 0,
  );
  image.data.set(dithered);
  context.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}
