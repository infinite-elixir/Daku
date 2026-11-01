# T-052 — AniList identity must be an anime, and artwork must never stay broken

**Phase:** 15 · **Depends on:** T-004, T-022, T-028, T-029, T-037 · **Estimate:** M

## Why

A live library entry (`Steel Ball Run: JoJo's Bizarre Adventure`, `tt38268282`) was saved
with `anilist_id=31706`. AniList `31706` is a **MANGA** entry, not an anime. The consequences
compound:

1. `Media(id: 31706, type: ANIME)` returns no media, so every detail refresh fails with a
   `404`/GraphQL error.
2. `refresh_title` falls through to the next metadata source, TVDB, which is not configured
   (`TVDB API key not configured`). The refresh aborts and **no** field is updated.
3. The title keeps the Cinemeta artwork URLs it was created with. For `tt38268282` those
   Metahub URLs are `404`, so the UI shows a blank poster and banner forever.
4. `title_seasons` and `episodes.absolute` stay empty, which breaks the release matcher
   (see T-053): AnimeTosho names the episode `S06E07` while the library requests `S01E07`.
5. `update_identity` commits the manual `anilist_id` override **before** anything validates
   it. A bad id therefore becomes a permanent override that a later refresh cannot repair.

The correct AniList identities are split cours, not one entry:

| AniList id | Title | Format | Episodes |
|---|---|---|---|
| `190327` | `JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE` | ONA | 1 |
| `210482` | `JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE` | ONA | 11 |

AniBridge only maps `tvdb_show:262954:s6 → anilist:190327`. There is no
`imdb_show:tt38268282` mapping, and one AniList entry cannot describe all 12 library
episodes.

## Goal

An anime title may only store an AniList id that resolves to a real `type: ANIME` entry.
Split-cour anime are represented explicitly so episode naming, provider season numbers and
artwork come from the correct entry. A refresh failure must be visible and must never leave
a known-dead artwork URL in place.

## Scope

### 1. Validate before committing an identity

- `PATCH /api/titles/{id}/identity` and `PUT /api/titles/{id}/overrides` must resolve a new
  `anilist_id` through `anilist.fetch_detail` **before** `update_identity` writes the
  override.
- Reject with `422 ANILIST_NOT_ANIME` when AniList returns no media or `Media.type != ANIME`.
  The error detail carries `{anilist_id, type, format, title}` when AniList returned a row,
  so the UI can say "31706 is a manga entry".
- Reject ids that only exist as `idMal` unless the caller explicitly passes `anilist_id`;
  resolve the MAL id to `Media(idMal:)` and return the AniList id in the error/suggestion.
- The metadata-fixer preview must show `type`, `format`, `episodes` and `seasonYear` for an
  AniList candidate. A manga row is rendered disabled with the reason, never selectable.
- `_validate_identity` stays a cheap schema check; the network validation belongs in the
  router/service call before the commit so a failed validation writes no override row.

### 2. Split-cour identity and provider seasons

- Add a durable mapping for "this library season/episode range comes from that AniList
  entry". Migration v10 adds `title_cours`:

  | column | notes |
  |---|---|
  | `title_id` | FK, cascade delete |
  | `library_season` | the season shown in the library |
  | `provider_season` | the season used by release names (e.g. JoJo overall `6`) |
  | `start_episode` / `end_episode` | inclusive library episode range |
  | `anilist_id` | the cour's AniList id |
  | `absolute_offset` | cumulative episode offset for absolute numbering |

  `titles.anilist_id` stays the primary cour so existing readers keep working. When
  `title_cours` is empty the existing `title_seasons` + single `anilist_id` behaviour is
  unchanged.
- `refresh_title` populates `title_cours` for an anime whose AniList relations contain
  `SEQUEL`/`PREQUEL` entries with the same franchise, an anime `format`, and an episode
  count, using the AniBridge season mapping when available. For Steel Ball Run the result is
  `S01 E01 → 190327`, `S01 E02–E12 → 210482`, `provider_season=6`.
- `episodes.absolute` is populated from the cour ranges, so absolute-numbered releases can
  be matched without guessing.
- Episode names/artwork are fetched per cour. A cour that fails to load degrades to the
  aggregate entry, not to a blank title.

### 3. Tolerant, still conservative auto-detection

`match_exact` requires normalized title equality, which can never succeed for
`Steel Ball Run: JoJo's Bizarre Adventure` vs either AniList title. Add an alias match:

- Accept when every significant token of the IMDb title appears in one AniList title or
  synonym (punctuation/case-insensitive), the AniList entry is `type: ANIME`,
  `countryOfOrigin=JP`, `format ∈ {TV, MOVIE, OVA, ONA, SPECIAL, TV_SHORT}`, and
  `abs(year - seasonYear) <= 1`.
- The AniList title may contain extra tokens (cour/stage suffixes, romaji particles). The
  IMDb title may not be a strict prefix of an unrelated franchise.
- If two or more candidates match, prefer the one with an AniBridge season mapping, then
  the earliest `seasonYear`, then the lowest id. Never merge two entries silently; store a
  `title_cours` group when they are split cours of the same franchise.
- Add `confidence="alias"` and a reason string. `mapped` and `exact` keep their current
  precedence. The signal is still a classification hint: the user can override it.

### 4. Artwork never stays broken

- `refresh_title` keeps the previous poster/banner only when the new detail has none **and**
  the existing URL is not a known-dead provider URL. Introduce a single `artwork_ok(url)`
  check (cache result for 24 h): a `200`/`206`/`304` is usable, `404`/`410` is dead.
- Fallback order for an anime poster: AniList current cour `coverImage.extraLarge` → other
  cour → stored value if alive → `None` (the UI renders its title-card placeholder).
  For a banner: AniList `bannerImage` → stored value if alive → `None`; the UI already
  falls back to the poster, so never synthesise a URL.
- A refresh that produces no usable poster must report `refresh_error`/`partial=true` to
  the caller and must not silently claim success. The title page shows a non-blocking
  "artwork unavailable" notice with a "Refresh metadata" action.
- Never rewrite a `404` Metahub URL back into the row during a failed refresh.

### 5. Repair the live entry

After the code ships, the Steel Ball Run row is repaired through the normal API (not by
editing SQLite):

1. `PATCH /api/titles/453ad3ba-f44d-4a75-a92a-4efcd9522e5a/identity` with
   `anilist_id=190327`, `confirm=true`.
2. Refresh metadata; the split-cour resolver adds `210482` and the `provider_season=6`
   mapping.
3. Confirm poster/banner are non-null and return `200`, `title_seasons`/`title_cours` has
   `provider_season=6`, and `episodes.absolute` is `1..12`.
4. Re-run the episode 7 release search; it must return the AnimeTosho `S06E07` rows
   (T-053 provides the tolerant matching).

Document the repair in the ticket when done. Until then the row is left untouched.

**Repair log (2026-10-31).** The live row was repaired through the running API after the
code passed `scripts/check.sh --web`:

1. `PATCH /api/titles/453ad3ba-.../identity {"anilist_id":190327,"confirm":true}` →
   `200`; the stored override became `anilist_id=190327`.
2. `POST /api/titles/453ad3ba-.../metadata/refresh {"source":"auto"}` → `200`,
   `partial=false`, `report.updated=12`.
3. `GET /api/titles/453ad3ba-...` now reports
   `poster=https://s4.anilist.co/.../bx190327-riJNCFL7w9y4.jpg` (`200`) and
   `banner=https://s4.anilist.co/.../190327-bXr8tFZUdxOw.jpg` (`200`).
   `title_cours` is `S01 E01 → 190327`, `S01 E02–E12 → 210482`, both
   `provider_season=6`; `title_seasons` has `provider_season=6`; and
   `episodes.absolute` is `1..12`.
4. The episode 7 release search is still empty. That is the tolerant `S06E07` matching
   owned by T-053, so this final check is deferred to that ticket.

The schema lands as migration **v11** (`title_cours`), because v10 was already taken by
the banner-artwork migration.

## Out of scope

- Scraping AniList HTML, MAL or TVDB.
- Accepting manga/Light Novel entries as anime identities.
- Changing Jellyfin naming or library folders.
- Downloading missing artwork, caching images on disk, or adding an image proxy.

## Acceptance criteria

- [x] Assigning AniList `31706` to an anime is rejected with `422 ANILIST_NOT_ANIME` and
      writes no override row.
- [x] The metadata-fixer preview exposes type/format/episodes and disables non-anime rows.
- [x] A valid anime id is fetched once before commit; an unreachable AniList leaves the
      previous identity intact and returns an actionable error.
- [x] `title_cours` migration is append-only, cascades on title delete, and older rows work
      unchanged.
- [x] Refresh resolves `190327` + `210482` into one library season with `provider_season=6`
      and absolute episodes `1..12`.
- [x] `resolve_candidate_detail` still works for non-split anime and never selects a manga.
- [x] The alias matcher classifies `Steel Ball Run: JoJo's Bizarre Adventure` as anime
       with an AniList id, and does not classify a same-year unrelated series.
- [x] A dead stored poster URL is not kept; the row ends with a live AniList URL or `NULL`.
- [x] A failed refresh returns `refresh_error` and the UI surfaces it.
- [x] Tests cover the manga rejection, split-cour grouping, alias matching and artwork
      fallback with `respx`.

## Test plan

- `tests/metadata/test_anilist_identity.py`: `Media(id:31706, type:ANIME)` empty payload →
  validation error; manga-looking search row is disabled.
- `tests/metadata/test_cours.py`: relations payload for 190327/210482; season/episode range
  mapping; provider season 6; absolute offsets.
- `tests/metadata/test_classification_alias.py`: exact match still wins; alias match for
  Steel Ball Run; negative case with an unrelated 2026 series; ambiguous pair resolves
  deterministically.
- `tests/metadata/test_refresh_artwork.py`: stored 404 not preserved; AniList cover used;
  no live URL → `None` + `partial=true`.
- `tests/routers/test_titles.py`: identity PATCH rejects a manga id before commit; valid id
  commits once.
- `web/src/pages/__tests__/metadata-fixer.test.tsx`: non-anime candidate is disabled and
  explains why.
