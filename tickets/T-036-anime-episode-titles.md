# T-036 — Anime episode titles and air dates

**Phase:** 10 · **Depends on:** T-004, T-021, T-028, T-029 · **Estimate:** M

## Goal

Anime episodes added or refreshed through AniList currently render as **Untitled episode**.
AniList's GraphQL detail only exposes an episode count, so `_anime_episodes()` invents
`EpisodeRef(season=1, episode=N)` with `title=None`. The title page then falls back to
`"Untitled episode"` (`TitleView.tsx`), and release search shows the episode number with no name.

Populate real episode names and air dates for anime, persist them in `episodes.name` /
`episodes.air_date`, and keep adding a title working when the secondary source is down.

## Scope

### Episode enrichment service

Add `services/metadata/jikan.py` (T-004 deferred Jikan) with:

- `GET https://api.jikan.moe/v4/anime/{mal_id}/episodes?page=N`
- parse `data[].title`, `data[].aired`, `data[].filler`, `data[].recap`, `pagination.last_visible_page`;
- cap pagination (e.g. 20 pages / 2,000 episodes) and stop when the provider count is reached;
- cache the parsed list by MAL id for 6 h, serve stale on failure, and respect Jikan's
  3 requests/second limit with a per-process throttle;
- never raise into Add/refresh: a 429/5xx/timeout returns an empty list and is logged once.

Add `streamingEpisodes { title thumbnail url site }` to the AniList detail query as a
**fallback** source. Strip the leading `Episode N - ` prefix; keep the thumbnail out of scope.

### Precedence

Use the first source that returns a usable title for a given episode number:

1. **TVDB** episodes when the title has a TVDB id (T-022 path), because specials and split
   seasons are already correct there.
2. **Jikan** by `anilist_id → idMal`, matching only on the episode number inside the page.
3. **AniList `streamingEpisodes`**, matching on the number in the title.
4. No name: leave `title=None` and let the UI show `Episode N` instead of
   `Untitled episode`.

Air dates come from the same source (TVDB `aired`, Jikan `aired`, AniList lacks air dates —
keep `None` rather than guessing).

### Wiring

- `resolve_candidate_detail()` enriches anime `detail.episodes` before returning, so both
  **preview** and **Add** show names. Bound the enrichment to the existing request deadline;
  a slow Jikan call must not delay Add by more than a few seconds.
- `refresh_title()` enriches `detail.episodes` before `sync_episodes()`. A refresh fills
  missing names and updates changed provider names, but never writes an empty string over an
  existing name.
- Episode names are not editable in T-029; do not add an override for them.
- The existing `sync_episodes()` path already updates `name`/`air_date`; extend it only if a
  matched episode is missing an update.
- Change the UI fallback in `TitleView.tsx` from `Untitled episode` to `Episode {episode}`
  (and the preview row likewise). A missing name is normal for anime, not an error.

## Out of scope

Translated/localised episode titles, filler/recap badges in the UI, manual episode-title
editing, OP/ED segment data, Jikan for movies or non-anime, backfilling names for titles that
are never refreshed (the user can refresh from the title page).

## Acceptance criteria

- [x] An anime added from search (e.g. Frieren, Edgerunners) stores real episode names for
      episodes Jikan or TVDB knows, and the title page no longer shows `Untitled episode`
      for them.
- [x] The preview shows the same episode names before Add.
- [x] Names survive a metadata refresh; a refresh with no usable title source does not erase
      names that already exist.
- [x] A Jikan 429, 500 or timeout never fails Add or refresh with a 500; the title is still
      created and the missing names can be filled by a later refresh.
- [x] At most one Jikan request per page per title, cached by MAL id; no per-episode requests.
- [x] An AniList entry without `idMal` falls back to `streamingEpisodes`, then to
      `Episode N`, without an error.
- [x] Specials sourced from TVDB keep their names and stay in `Season 00`.
- [x] Tests use `respx` fixtures; no live Jikan or AniList calls in CI.

## Test plan

- `tests/metadata/test_jikan.py`: parsing, pagination, malformed rows, 429 retry/stale cache.
- `tests/metadata/test_anilist.py`: `streamingEpisodes` fallback and prefix stripping.
- `tests/routers/test_add_title.py`: anime add with Jikan titles; Jikan failure still creates
  the title; no `idMal` fallback.
- `tests/services/test_refresh.py` (or the existing refresh tests): names filled on refresh,
  existing names not erased.
- `web/src/pages/__tests__/title-episodes.test.tsx`: fallback renders `Episode 3`, and a real
  name renders instead when present.

## Implementation notes

- Keep provider parsing in its adapter; `resolve.py` should only choose a precedence and
  merge by episode number.
- Do not block the event loop: Jikan is already async via `httpx`; the throttle uses
  `asyncio.sleep`.
- If Jikan and TVDB disagree, prefer TVDB for titles that have a TVDB id; otherwise prefer
  Jikan. Never merge two lists positionally when counts differ.
- Log `anime episode titles enriched` with `{title_id, source, found, missing}` and no episode
  titles.
- This ticket is about names only. Season structure is T-037.
