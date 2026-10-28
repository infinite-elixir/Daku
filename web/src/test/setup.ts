import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom does not implement a 2D canvas. Default to "unsupported" so components take
// their gradient fallback; individual tests can replace these with a working stub.
if (typeof HTMLCanvasElement !== "undefined") {
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => null),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "toDataURL", {
    configurable: true,
    value: vi.fn(() => "data:image/png;base64,"),
  });
}
