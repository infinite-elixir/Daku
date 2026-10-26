# T-033 — Visual refresh: typography, cards and rating colour

**Phase:** 9 · **Depends on:** T-018, T-024 · **Estimate:** M

## Goal

Make Daku read bigger and more distinct without turning every screen into a poster:

- a display face for headings, a larger base size for body copy;
- remove the coloured top bars from cards;
- replace them with a hover glow in the title's kind colour;
- give IMDb, Rotten Tomatoes and Metacritic their brand colours.

This ticket **amends STYLE.md**. Until it lands, the current rules (Caskaydia only, 14 px base,
mandatory 2 px poster edge, no coloured glows) are still normative.

## Scope

### Typography (STYLE.md §3)

- Add **Orbitron** (SIL OFL) as a **headings-only** display face, bundled as a Latin subset at
  `web/public/fonts/orbitron-latin.woff2`, `font-display: swap`, `local()` first if installed.
- Orbitron applies to: the wordmark, `h1`, `h2` page/section headings, empty-state headline and
  dialog titles. It does **not** apply to body copy, buttons, labels, badges, tables, inputs,
  metadata, IDs or paths.
- Keep Caskaydia Cove for everything else; keep a monospace fallback. Never fall back to a
  sans-serif for body text.
- Raise the base from 14/22 to **15/23**, and shift the scale by roughly 1 px:

  | Token | Old | New |
  |---|---|---|
  | `text-micro` | 11/16 | 12/17 |
  | `text-small` | 12/18 | 13/19 |
  | `text-body` | 14/22 | 15/23 |
  | `text-title` | 16/24 | 17/25 |
  | `text-h2` | 20/28 | 22/30 |
  | `text-h1` | 28/36 | 32/40 |

  `text-display` and `text-mega` stay as they are; do not let the whole app inflate.
- Orbitron headings use `letter-spacing: -0.01em` and uppercase only for the wordmark and
  micro eyebrows; page titles stay sentence case.
- `font-variant-ligatures: none` remains on `.font-data`; Orbitron headings must not use code
  ligatures.
- Check the 390 px layout first: no title wraps to three lines, no control under 32 px, no
  horizontal scroll.

### Card treatment (STYLE.md §2.4, §5.10)

- Remove the 2 px kind-coloured top edge from `PosterArt`/`LibraryCard`/`SearchResultCard` and
  the poster edge rule. The kind badge remains the identity carrier.
- Add a **hover/focus-visible glow** behind clickable poster cards:
  - a soft `box-shadow` in the kind colour at low alpha (e.g.
    `0 0 0 1px color-mix(...), 0 12px 32px -12px <kind>`);
  - use semantic CSS variables per kind, not hardcoded hex in components;
  - transition `box-shadow`/`border-color` only, 120 ms, no lift, no scale;
  - hover only on devices with `hover: hover`; touch gets a focus ring, not a sticky glow;
  - disabled entirely under `prefers-reduced-motion: reduce` (keep the border/ring).
- The detail-page 2 px left rule stays (one per page); this ticket only removes the card bars.
- Keep the poster as the largest colour field; the glow must not tint the card surface.

### Rating pills (T-024)

- Use brand colours, with a 14% tint background and full-strength text/icon:
  - IMDb `#f5c518` (yellow);
  - Rotten Tomatoes `#fa320a` (red);
  - Metacritic `#66cc33` (green).
- Check contrast against `--bg-panel`; if a brand colour fails AA at the chosen size, use it
  for the icon/border and keep the score in `--text-primary`. Document the measured ratios.
- Add `--rating-imdb`, `--rating-rt`, `--rating-metacritic` tokens to `theme.css`.
- Keep the existing accessible labels and the attribution line. Colour is reinforcement, not
  the only signal.
- Missing ratings are still omitted entirely; no grey dash.

### Documentation and tests

- Update STYLE.md §2.1/§2.4/§3.1/§3.2/§4.4/§5.10 with the new rules and mark the old ones as
  replaced.
- Add a short note in `plan.md` if typography becomes a product decision.
- Tests assert the absence of the top-edge class and the presence of the glow/rating tokens
  (jsdom cannot compute shadows, so assert the class/token contract, not pixels).

## Out of scope

A full redesign, new page layouts (T-034), banner/dither art (T-035), icon changes, changing
the Tokyo Night palette, adding a second accent colour, animation beyond hover transitions.

## Acceptance criteria

- [ ] Orbitron loads for headings only; body, controls and metadata remain Caskaydia.
- [ ] The base body size is 15 px and the scale is applied consistently; no layout overflow at
      390 px or 1440 px.
- [ ] Library, search and saved cards have no coloured top bar.
- [ ] Cards show a soft kind-coloured glow on hover and a visible focus ring on keyboard focus;
      touch devices do not get a sticky glow.
- [ ] IMDb, RT and Metacritic pills use the brand tokens with readable contrast and keep their
      aria labels.
- [ ] Missing ratings are omitted, not recoloured dashes.
- [ ] `prefers-reduced-motion: reduce` removes the glow transition without removing focus
      visibility.
- [ ] STYLE.md no longer contradicts the shipped UI.
- [ ] No hardcoded hex values in components; colours come from `theme.css` tokens.

## Test plan

- `web/src/components/__tests__/ratings.test.tsx`: tokens/classes, omission, labels.
- `web/src/components/__tests__/poster-card.test.tsx`: no top-edge class, hover/focus classes,
  kind variable.
- `web/src/pages/__tests__/library.test.tsx`: base typography class contract, no overflow
  assumptions.
- Manual: 390 px, 820 px, 1440 px, keyboard-only pass, reduced-motion pass.

## Implementation notes

- Prefer CSS custom properties and Tailwind v4 `@theme` tokens over arbitrary values.
- Do not remove the kind badge when removing the bar; that would make mixed grids unreadable.
- Glow colour is `--kind-*`; do not introduce green/red as a kind colour.
- If the Orbitron subset is missing a glyph used in a heading, fall back to Caskaydia rather
  than showing tofu.
- Update the font subset recipe in STYLE.md and commit the generated woff2 plus its licence.
