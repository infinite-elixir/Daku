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

- **No display face.** Typography stays Caskaydia-only for every level: wordmark, `h1`, `h2`,
  page/section headings, empty-state headlines and dialog titles included. (An Orbitron
  headings-only face was tried in the first pass and removed on 2026-10-28; see the
  follow-up note at the end.)
- Hierarchy comes from **size and weight**, not a second family: `text-title` 17 for card
  titles, `text-h2` 22 for page sections, dialog titles and empty-state headlines, `text-h1`
  32 for the one page title, wordmark `text-h2` 700.
- Keep a monospace fallback. Never fall back to a sans-serif for body text.
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
- Large headings may use `tracking-tight`; never positive letter-spacing outside `text-micro`
  eyebrow labels. Uppercase is reserved for micro eyebrows; page titles stay sentence case.
- `font-variant-ligatures: none` remains on `.font-data`.
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

- [x] Every level — wordmark, page title, section heading, card title, body — is Caskaydia;
      hierarchy is size and weight only.
- [x] The base body size is 15 px and the scale is applied consistently; no layout overflow at
      390 px or 1440 px.
- [x] Library, search and saved cards have no coloured top bar.
- [x] Cards show a soft kind-coloured glow on hover and a visible focus ring on keyboard focus;
      touch devices do not get a sticky glow.
- [x] IMDb, RT and Metacritic pills use the brand tokens with readable contrast and keep their
      aria labels.
- [x] Missing ratings are omitted, not recoloured dashes.
- [x] `prefers-reduced-motion: reduce` removes the glow transition without removing focus
      visibility.
- [x] STYLE.md no longer contradicts the shipped UI.
- [x] No hardcoded hex values in components; colours come from `theme.css` tokens.

## Test plan

- `web/src/components/__tests__/ratings.test.tsx`: tokens/classes, omission, labels.
- `web/src/components/__tests__/poster-card.test.tsx`: no top-edge class, hover/focus classes,
  kind variable.
- `web/src/pages/__tests__/library.test.tsx`: base typography class contract, no overflow
  assumptions.
- Manual: 390 px, 820 px, 1440 px, keyboard-only pass, reduced-motion pass.

Implemented in v1.2. Headless Firefox screenshots were checked at 390, 820 and 1440 px;
keyboard focus and reduced-motion behaviour are covered by the class/CSS contract in
`theme.css` and the new component tests.

## Implementation notes

- Prefer CSS custom properties and Tailwind v4 `@theme` tokens over arbitrary values.
- Do not remove the kind badge when removing the bar; that would make mixed grids unreadable.
- Glow colour is `--kind-*`; do not introduce green/red as a kind colour.
- Keep the heading ramp deliberate: do not drop a section heading to body size to save space;
  step it down one level (`text-h2` → `text-title`) instead.
- Update the font subset recipe in STYLE.md and commit the generated woff2 plus its licence.

## Follow-up (2026-10-28): Orbitron removed

The Orbitron headings pass shipped in `2bca21a` was reverted. All text — wordmark, headings,
body, controls, metadata, IDs — is Caskaydia again. What survives from T-033 is the **larger
scale** (15/23 base, `text-title` 17, `text-h2` 22, `text-h1` 32), the card glow and the
rating brand colours. Instead of shrinking headings to keep them in Caskaydia, levels were
stepped up: section `h2`/dialog titles/empty-state headlines went from `text-title` to
`text-h2`, card `h3` stayed `text-title`, and the wordmark moved from `text-h1` uppercase to
`text-h2` 700 sentence case so it fits the 56px topbar. `web/public/fonts/orbitron-latin.woff2`
and its licence were deleted along with `--font-display` and `.display-face`.

## Follow-up (2026-10-29): spacing tokens

T-043 owns the spacing scale. T-033's sizes are unchanged, but components now consume the
`--spacing-*` tokens (`gap-section`, `p-panel`, `py-row`, `min-h-control-sm`) instead of
defining their own pixel steps.
