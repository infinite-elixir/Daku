# T-043 — Spacing tokens and app-wide density

**Phase:** 12 · **Depends on:** T-018 · **Estimate:** M

## Problem

Spacing is hand-picked per component. `TitleView` uses `gap-8` between sections, `LibraryPage`
uses `gap-6`, panels use `p-2`/`p-4`/`px-4 py-3` depending on who wrote them, and download
cards pack a title, two pills, an episode label, a link, quality and engine into two lines.
The result reads as cramped, and there is no shared vocabulary to fix it: "more breathing
room" currently means editing arbitrary utilities in every file.

STYLE.md §1.3 mandates density and "tighter vertical rhythm over airy marketing spacing",
which is the rule this ticket replaces.

## Goal

One named spacing scale, applied app-wide, one step looser than today. No type change, no
information removed, no new layout (T-044…T-047 own the layouts).

## Scope

### Tokens (`web/src/styles/theme.css`)

Tailwind v4 `@theme`, `--spacing-*` namespace so `p-panel`, `gap-section`, `py-row` exist as
utilities:

| Token | Value | Use |
|---|---|---|
| `--spacing-grid` | `4px` | The base unit. Nothing may sit off it. |
| `--spacing-tight` | `8px` | Related controls, icon + label |
| `--spacing-panel-compact` | `12px` | Nested blocks, table cells |
| `--spacing-panel` | `16px` | Cards, panels, list rows |
| `--spacing-section` | `32px` | Page sections, hero gaps |
| `--spacing-gutter` | `24px` | Content gutters |

- Utilities in components use these names, not arbitrary values (`p-[13px]`, `gap-[26px]`
  are banned by review). Raw `p-2`/`gap-6` remain legal only where the value is the token.
- Keep the existing 4px approved steps in STYLE §4.1: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`.

### The one-step shift

| Surface | Now | After |
|---|---|---|
| Page section gap | 24px | 32px |
| Panel / card padding | 12–16px | 16px |
| List row vertical padding | 12px | 16px |
| Card grid gap | 16px | 20px |
| Table row min height | 40px | 44px |
| Form control height | 32/36px | 36/40px |
| Related-controls gap | 8px | 8px (unchanged) |

- Type sizes and line heights do **not** change here; T-033 owns those and consumes these
  tokens.
- Nothing shrinks. No page may lose a field to buy the space.

### Where it applies

- Shared primitives first: `PosterArt`/`LibraryCard`, `Pills`, `RatingsRow`,
  `ExpandableText`, `MetadataFacts`, `HealthCard`.
- Page shells: `AppShell`, `LibraryPage`, `SearchPage`, `TitlePage`, `DownloadsPage`,
  `SettingsPage`.
- Dialogs keep 16px padding and pick up the taller controls.

### STYLE.md

- §1.3 rewritten: density is still a feature, but whitespace inside a card is what makes a
  dense page readable. Remove "prefer tighter vertical rhythm over airy marketing spacing".
- §4.1 gains the token table above and the rule that components consume tokens, not raw
  utilities.
- §5.3 card padding becomes 16px; §5.5 table row height 44px; §5.1 control heights update.
- §14 gets an amendment row pointing at this ticket.

## Out of scope

Typography and the Orbitron face (T-033), the downloads card hierarchy (T-044), the title
hero (T-045), the release panel (T-046), file cards (T-047). No colour changes. No motion.

## Acceptance criteria

- [x] `theme.css` defines the spacing tokens; no component uses an arbitrary spacing value.
- [x] Section gaps are 32px, panel padding 16px and list rows 16px vertical on every main page.
- [x] Control height is 36px (sm) / 40px (md) with no control below 32px at 390px.
- [x] Library, Search, Title, Downloads and Settings show more space without dropping a field.
- [ ] No horizontal scroll at 390, 820, 1024 and 1440px; 200% zoom at 1440px still fits. *(Manual browser pass not run in this environment.)*
- [x] STYLE.md no longer contradicts the shipped spacing.
- [x] T-033 records that it consumes these tokens instead of defining its own scale.

## Test plan

- `web/src/App.test.tsx`: shell classes resolve the section and gutter tokens.
- `web/src/pages/__tests__/library.test.tsx`: grid gap token, card padding token.
- `web/src/pages/__tests__/downloads.test.tsx`: row padding token on a download card.
- Manual: 390 / 820 / 1024 / 1440 px, keyboard pass, 200% zoom.

## Implementation notes

- Land this before T-044…T-047 so their tickets describe tokens, not pixels.
- Tailwind v4 `--spacing-*` also feeds `w-*`/`h-*`; give the tokens non-numeric names so
  `p-4` and `p-panel` cannot be confused.
- Do not change `text-*` sizes in this ticket, even where a looser rhythm makes 13px look
  small. That argument belongs to T-033.

## Implemented

Tokens live in `web/src/styles/theme.css` (`grid`, `tight`, `panel-compact`, `panel`, `row`,
`card`, `gutter`, `section`, `control-sm`, `control`, `row-lg`). Section gaps, panel padding,
list-row padding, card-grid gaps and control heights were shifted one step across the shell,
shared primitives and the five main pages. The `min-h-[52px]` mobile-nav hack became `min-h-13`
so no spacing arbitrary value remains. Banner height, dialog max sizes, measures and aspect
ratios stay as sizes, not spacing.
