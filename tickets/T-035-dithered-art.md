# T-035 — Poster-derived dithered banner and library backdrop

**Status: reverted.** The dithered engine (`dither.ts`, `DitherCanvas`), the library
backdrop and the same-origin poster proxy (`/api/posters/proxy`) were removed. The title
page now renders wide banner art from metadata directly as an `<img>` with a gradient
fallback. The original scope below is kept for history.

**Phase:** 9 · **Depends on:** T-018, T-024, T-033 · **Estimate:** M

## Goal

Give the title page and the library a distinctive Daku surface without adding an image service:
turn an existing poster into a coarse dithered/ASCII banner **in the browser**, and use one
randomly chosen library poster as a very quiet backdrop. No new API, no stored images, no
external image processing.

## Scope

### Shared engine (`web/src/lib/dither.ts` + `DitherCanvas.tsx`)

- Input: poster URL, target width/height, palette mode (`kind` or neutral), optional seed.
- Pipeline:
  1. load the image with `crossOrigin="anonymous"` and a 10 s timeout;
  2. draw to an offscreen canvas at a small resolution (e.g. 96–160 px wide, aspect preserved);
  3. convert to grayscale, apply an **ordered Bayer 4×4/8×8 dither** (or a 1-bit threshold with
     deterministic noise) into the Tokyo Night palette;
  4. render as a `<canvas>` with `image-rendering: pixelated`, or as a data URL used by CSS
     `background-image`.
- Palette: `--bg-deep`, `--bg-panel`, `--text-muted` plus the title's kind colour at low
  intensity. Never use volt (reserved) and never introduce a new brand colour.
- Output is deterministic for the same poster + size + seed so React re-renders do not flicker.
- Cache results in memory keyed by `posterUrl|width|height|palette`, max 24 entries, evict LRU.
  Do not write to `localStorage`/IndexedDB in this ticket.
- Performance: run the pixel loop in `requestIdleCallback` (fallback `setTimeout(0)`), cap the
  canvas at `devicePixelRatio = 2`, and never block the first paint.

### Fallbacks (mandatory)

- If the image fails to load, has no poster, or decoding fails: render a deterministic
  **procedural ASCII pattern** from the title + year (no image data). Never show a broken image
  or a blank block.
- **CORS is the common case, not the exception.** Poster hosts such as
  `images.metahub.space` serve images without `Access-Control-Allow-Origin`, so reading pixels
  from a direct URL taints the canvas and `getImageData` throws. When the direct load cannot be
  read:
  1. retry through a **same-origin read-only poster proxy** (below);
  2. if the proxy is unavailable or over quota, use the procedural fallback.
- If the browser lacks canvas support, use a static CSS gradient + grain fallback.
- If `prefers-reduced-motion: reduce`, the art is still allowed (it is static); no animated
  scanlines or flicker in any mode.

### Same-origin poster proxy (only if CORS blocks pixel reads)

- `GET /api/posters/proxy?url=<https-url>` returns the image bytes with
  `Content-Type: image/*` and a short cache header.
- Allowlist HTTPS image hosts (at minimum `images.metahub.space`, plus the AniList/TVDB image
  hosts already returned by metadata). Reject everything else with `400 POSTER_HOST_NOT_ALLOWED`;
  resolve DNS and reject private/link-local ranges (no SSRF).
- Cap at 8 MB and stream or reject early; no disk write, no caching server-side, no logging of
  the full URL beyond hostname.
- The proxy is **not** an image generator: all dithering still happens in the browser. It exists
  only so the canvas is same-origin and untainted.
- If the user prefers no proxy, set it aside and the feature still works via the procedural
  fallback; the ticket is not blocked on it.

### Title page banner

- A wide banner strip (160–220 px tall) behind the title header, spanning the content width:
  dithered poster on the right/behind, a bottom-up scrim
  `linear-gradient(to top, rgba(12,14,20,0.95), transparent)`, title and actions on top.
- The original poster remains visible in the hero; the banner does not replace it.
- Contrast: all text over the banner must be at least AA against the darkest part; the scrim is
  not optional.
- If the banner would make the page taller than the current hero, collapse gracefully at
  < 720 px (banner becomes a 96 px strip or is hidden; the poster carries identity).
- `aria-hidden="true"` on decorative canvases; no text is rendered only inside the art.

### Library backdrop

- Choose one poster from the current library page (client-side, from loaded rows) with a
  deterministic seed per day or per session, so reloads do not shuffle every time.
- Render a single very low-opacity (≤ 6%) dithered band at the top of the library page, behind
  the header and filter chips, fading out by ~320 px. No full-page tiled background.
- Respect `prefers-reduced-data` if available; otherwise skip the backdrop on narrow screens
  (< 480 px) to avoid a useless download. The backdrop must not trigger extra image requests
  when the same poster is already displayed in the grid; reuse the browser cache.
- If one poster is used, it is the same URL already in the grid; do not add a new fetch for a
  hidden title.

## Out of scope

Server-side image processing, stored banner files, AI generation, animated glitch/scanlines,
video backdrops, per-title banner editing, and third-party image CDNs. Uploading custom
artwork.

## Acceptance criteria

- [x] The title page shows a dithered banner derived from its poster at ≥ 720 px, with legible
      text over it.
- [x] The library shows at most one quiet dithered backdrop, stable across a reload within the
      same day/session.
- [x] No new network request is made for the library backdrop beyond posters already in the
      grid.
- [x] A CORS-blocked poster is retried through the same-origin proxy and produces a banner; if
      the proxy is unavailable, it falls back to procedural ASCII with no console error shown to
      the user and no broken image.
- [x] The proxy rejects non-allowlisted hosts and private/link-local addresses with a stable
      `400`, and never writes the image to disk.
- [x] A title with no poster renders the fallback, not an empty box.
- [x] The art is deterministic for the same inputs and does not flicker on re-render.
- [x] Decorative canvases are `aria-hidden`; no information exists only in the art.
- [x] At 390 px the banner does not push the title below the fold or cause horizontal scroll.
- [x] Reduced-motion users see the same static result with no added animation.
- [x] Rendering a grid of 100 posters does not block typing or scrolling for more than a frame.

## Test plan

- `web/src/lib/__tests__/dither.test.ts`: Bayer matrix, palette mapping, deterministic output,
  grayscale thresholds, size caps.
- `web/src/components/__tests__/dither-canvas.test.tsx`: canvas mock, load success, load error,
  tainted-canvas → proxy retry → ASCII fallback, cache hit, aria-hidden.
- `tests/routers/test_poster_proxy.py`: allowlisted host, redirect to a private address, wrong
  content type, oversize body, cache header.
- `web/src/pages/__tests__/library.test.tsx`: one backdrop, stable seed, no extra fetch,
  mobile omission.
- `web/src/pages/__tests__/title-page.test.tsx`: banner presence, scrim, mobile collapse,
  no-poster fallback.
- Manual: a real CORS-allowed poster, a real blocked poster, and a slow network.

## Implementation notes

- Poster hosts differ: `images.metahub.space` does **not** send permissive CORS headers, so the
  proxy path will be the normal path for Cinemeta posters. Keep the direct-load attempt first
  (zero cost when a host does allow it) and treat the proxy as a fallback.
- Use `createImageBitmap` when available, otherwise `new Image()`; revoke object URLs.
- The dither must not encode adult/NSFW content differently; it is purely a visual transform.
- Keep the palette tokens in `theme.css`; no hex in the component.
- Never block render on image decode: show the poster/gradient immediately, swap in the banner
  when ready.
- The proxy is the only new backend surface and must never become a general-purpose fetcher:
  allowlist, HTTPS only, size cap, private-IP resolution check.
- If T-033 changes the kind glow or palette, reuse the same CSS variables; do not fork colours.

Implemented in v1.2. The banner is hidden below 720 px via CSS (no layout height), the
library backdrop is a single 320 px fade at 6% opacity, and the poster proxy is
`GET /api/posters/proxy` with an HTTPS host allowlist, DNS public-address check, 8 MB cap
and no disk writes.

v1.3 follow-up: the title banner is 300 px tall (the poster column is 180 × 270 px) and
prefers real wide art from metadata — Cinemeta `background` for movies/series and AniList
`bannerImage` for anime — persisted as `titles.banner_url` (migration v10). The `/metadata`
display response also carries `banner_url`, so existing rows get a banner before their next
refresh. When no wide art exists the poster remains the source. The library backdrop still
reuses the poster already present in the grid; it never fetches hidden banner art.

Branch `direct-banner-art` (alternative to the dithered version): the title page renders the
wide `banner_url` directly as an `<img>`. No canvas, pixel read or poster proxy is involved
on that branch; the portrait poster stays visible in the hero and remains the fallback when
no wide art exists. The library backdrop is unchanged.
