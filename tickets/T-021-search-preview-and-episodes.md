# T-021 — Search preview and season/episode navigation

**Phase:** 6 · **Depends on:** T-003, T-004, T-006 · **Estimate:** L

## Goal

A search result card opens the same detail view a library card opens, but read-only, with an
**Add to library** action. On every detail view, a series or anime lists **all** seasons and
episodes. Selecting an episode targets it for the release search instead of hiding it behind
two dropdowns.

The preview is metadata only. Releases, saved magnets and downloads stay keyed to a library
`title_id`, so they become available after Add.

## Scope

### Backend

- `GET /api/metadata/preview?source=&source_id=&kind=` — normalised, read-only view of a
  candidate. Reuses `resolve_candidate`; writes nothing. Returns the `TitleDetail` plus:
  - `library_title_id` when the same IMDb/AniList id is already in the library, else `null`;
  - `stale`;
  - the same `episodes` shape as `GET /api/titles/{id}`.
- `POST /api/titles` gains an optional `metadata_source` so a source chosen on the preview is
  persisted on Add (consumed by T-022).
- No new tables or columns in this ticket.

### Frontend

- Route `/preview/:kind/:source/:sourceId`.
- Extract a shared `TitleView` (hero, poster, metadata block, seasons, episodes) used by both
  `/title/:id` and the preview, so they cannot drift.
- `SearchResultCard` becomes a link to the preview. The quick Add button stays, but calls
  `stopPropagation`; a card with the title already in the library reads **Open**.
- Preview `Add to library` calls `POST /api/titles`, then navigates to `/title/{id}`.
  Errors render inline; the button shows a pending state.
- Title page (`/title/:id`):
  - Lift `season`/`episode` selection out of `ReleasesSection` into the page. The release
    search and the episode list share one selection.
  - Replace the two `<select>` controls with **season tabs** (chips) and a full **episode
    list**. Clicking an episode selects it, updates the URL and scrolls to `#releases`.
  - `?season=2&episode=5` is the source of truth. Invalid values fall back to the first
    non-special episode. Reload and browser back keep the selection.
  - Season `0` is rendered last as **Specials** and is never auto-selected.
  - Anime rows keep the `#absolute` number.
- `/search?q=&kind=` becomes the source of truth so **Back** from a preview restores the
  query and kind filter. Keep the 300 ms debounce and request cancellation.
- Empty episode list: explain *why* and offer the metadata refresh action from T-022
  (disabled until that ticket lands), instead of rendering an empty selector.
- UI details: `role="tablist"` / `role="tab"` with arrow-key navigation, 44 px tap targets,
  selected row visually distinct without relying on colour alone.

## Out of scope

Metadata re-fetch and the source dropdown (T-022). Release quality/provider controls (T-023).
Ratings and extra metadata (T-024). Watched state and toasts (T-025).

## Acceptance criteria

- [x] Clicking anywhere on a search result card opens the preview; the quick Add button still
      works and does not navigate.
- [x] Preview shows poster, title, year, kind, overview, seasons and every episode, with a
      single **Add to library** button.
- [x] Adding from preview creates the same library row as adding from search, then lands on
      `/title/{id}` with releases available. Adding twice does not duplicate.
- [x] A preview for a title already in the library shows **Open** and links to `/title/{id}`.
- [x] Preview never queries Torrentio and never creates a download.
- [x] Title page lists all seasons as tabs and all episodes as collapsible cards; expanding an
      episode loads its Torrentio results inside that card in a scrollable panel, and the
      URL reflects the open `season`/`episode`.
- [x] `?season=&episode=` deep links select the right episode; an invalid pair falls back
      safely instead of breaking the page.
- [x] Specials (season 0) are a separate tab, sorted last, and not auto-selected.
- [x] Browser Back from a preview returns to `/search` with the same query and kind.
- [x] Usable at 390 px and 1440 px; tabs and rows are keyboard operable.
- [x] A title with zero episodes shows an explanatory empty state, not an empty dropdown.

## Test plan

- `tests/routers/test_preview.py`: movie/series/anime preview, already-in-library match,
  unknown id, metadata outage, no DB writes.
- `web/src/pages/__tests__/preview.test.tsx`: add flow, already-in-library state, error.
- `web/src/pages/__tests__/title-episodes.test.tsx`: season tabs, episode click selects and
  calls the release query with the right params, specials excluded from default, deep link.
- `web/src/pages/__tests__/search.test.tsx`: card link, Add button does not navigate, query
  restored from `?q=`.

## Implementation notes

- Preview is intentionally **not** a second title page implementation. If `TitleView` needs a
  prop to render without releases, prefer `releases={null}` over branching on the route.
- Do not upsert a title just to render a preview. The only write is the explicit Add.
- Prevent the preview from being added twice by racing keys; rely on the existing partial
  unique indexes and `POST /api/titles` idempotency.
- If metadata is unreachable, render the candidate fields passed from search and let Add use
  the existing fallback path. T-022 is what repairs the episode list later.

## Implementation notes (T-021)

- `resolve_candidate_detail()` returns `(TitleDetail, stale)`; `resolve_candidate()` is the
  unchanged wrapper used by Add.
- Preview episodes use the library episode shape with synthetic `preview:{season}:{episode}`
  ids because they are not persisted.
- `AddTitleRequest.metadata_source` is accepted and validated now. Persisting it needs the
  `titles.metadata_source` column, which lands with T-022 (migration v5); until then the
  field is deliberately not written.
- Shared UI lives in `web/src/components/TitleView.tsx`; season/specials ordering and
  selection parsing live in `web/src/lib/episodes.ts`.
- The search page reads `?q=`/`?kind=` from the URL (writes with `replace`), so browser Back
  from a preview restores the query without an extra history entry per keystroke.
- Follow-up UX pass: episodes sit above the release tools and are accordion cards. The
  Torrentio results live inside the open card (`ReleasesSection mode="find"`, scrollable);
  the Saved tab and paste-a-magnet form stay below the episode list (`mode="saved"`). Only
  one card opens at a time, a second click closes it, and pages start collapsed unless a
  `?season=&episode=` deep link is present.

