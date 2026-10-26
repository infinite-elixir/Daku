# T-024 — Ratings, richer metadata and expandable description

**Phase:** 6 · **Depends on:** T-021, T-003 · **Estimate:** M

## Goal

Make the preview and title pages feel like a catalogue entry: IMDb and Rotten Tomatoes
ratings, genres, runtime, cast and credits, and a description the user can expand instead of
a wall of clamped text.

IMDb data is already available from Cinemeta. Rotten Tomatoes is only available through
OMDb, so that part is **optional and key-gated** — no key means IMDb-only, never an error.

## Scope

### Metadata model

Extend `services/metadata/models.TitleDetail` with nullable display fields:

- `imdb_rating: float | None`
- `rt_rating: int | None` (0–100)
- `metacritic_rating: int | None` (0–100)
- `runtime_minutes: int | None`
- `genres: list[str]`
- `cast: list[str]` (max 10 stored, 5 displayed)
- `directors: list[str]`
- `writers: list[str]`

Cinemeta mapping (`parse_meta`): `imdbRating`, `genres`/`genre`, `runtime` (`"167 min"`),
`cast`, `director`, `writer`. Do not guess a value that is absent.

AniList already supplies `genres`, `studios` and `format`; map those into the same display
fields where the meaning matches. Do not pretend AniList has an RT score.

### OMDb enrichment (optional)

- New secret `omdb_api_key` (`secrets.json`, `DAKU_OMDB_API_KEY`), never logged.
- `services/metadata/ratings.py`:
  - `GET https://www.omdbapi.com/?apikey=<key>&i=<imdb_id>&plot=short`;
  - parse `imdbRating`, and `Ratings[]` entries for `Rotten Tomatoes` (`"92%"`) and
    `Metacritic` (`"89/100"`);
  - cache successful responses for 24 h keyed by IMDb id; cache failures for 5 min;
  - on missing key, return empty without a request;
  - on `401`/`Invalid API key`, disable enrichment for the process, surface a doctor warning,
    and never retry per title;
  - on network error, return empty and let the page render Cinemeta data.
- OMDb is a **secondary** source: Cinemeta's `imdbRating` wins when both exist, and an OMDb
  disagreement is not shown as two IMDb numbers.
- `/api/system/doctor` gains `omdb: configured|unconfigured|invalid-key` (network only when
  configured, and cached).

### API

- `GET /api/titles/{id}/metadata` — read-only enrichment for a library title. Returns the
  display fields above plus `stale` and `sources` (`cinemeta`, `omdb`). It does not write the
  DB and does not change `metadata_source`.
- `GET /api/metadata/preview` (T-021) includes the same fields for a non-library candidate.
- Refresh (T-022) may use the same parser, but ratings are not persisted.
- Every field is optional in the response; the UI never renders `"N/A"`.

### UI

- `RatingsRow` on preview and title page: IMDb (★ 8.4), Rotten Tomatoes (🍅 92%), Metacritic
  (89). Each has an accessible label; missing ratings are omitted, not greyed-out dashes.
- `MetadataFacts`: genre chips, runtime (`167 min`), cast (first 5, `+N` in a `title`
  tooltip), directors and writers. Wrap cleanly at 390 px.
- `ExpandableText` for `overview`: 3-line clamp, **Show more / Show less** button with
  `aria-expanded`, only rendered when the overview exceeds ~320 characters. Expanding does
  not reflow the poster/hero off-screen.
- Ratings and facts appear on the preview and `/title/:id`, **not** on search result cards.
- Attribution line: "Ratings via IMDb, Rotten Tomatoes and Metacritic." shown once when any
  external rating is present.

## Out of scope

Ratings on search cards. Embedded trailers, backdrops/logos, awards, box office and country
(the user did not select them). Persisting ratings in SQLite. RT scraping.

## Acceptance criteria

- [x] A title with Cinemeta data shows its IMDb rating, genres, runtime, cast, director and
      writer without any API key.
- [x] With a valid `omdb_api_key`, Rotten Tomatoes and Metacritic badges appear when OMDb
      returns them.
- [x] Without a key, no OMDb request is made and the page still renders IMDb data.
- [x] An OMDb `401` disables enrichment for the process, the page still renders, and the
      doctor reports `invalid-key`.
- [x] Missing fields are hidden; the UI never prints `N/A`, `null` or an empty chip.
- [x] IMDb rating comes from Cinemeta when OMDb disagrees.
- [x] The description toggles between clamped and full text, is keyboard operable, and does
      not appear when the text is short.
- [x] Enrichment is cached for 24 h and does not run on every keystroke or every list render.
- [x] The OMDb key never appears in logs, API responses or error detail.
- [x] Tests use `respx` fixtures; no live OMDb or Cinemeta calls in CI.

## Test plan

- `tests/metadata/test_ratings.py`: key present, key absent, RT/Metacritic parsing, malformed
  value, 401, 429, cache hit, timeout fallback.
- `tests/metadata/test_cinemeta_rich.py`: runtime parsing, cast/director/writer, missing fields.
- `tests/routers/test_title_metadata.py`: field shape, stale flag, no DB writes, preview parity.
- `web/src/components/__tests__/ratings.test.tsx`: badges, omission, attribution.
- `web/src/components/__tests__/expandable-text.test.tsx`: clamp, toggle, aria.

## Implementation notes

- Reuse the existing `TTLCache` and `fetch_json` client; do not add a second HTTP stack.
- Keep the API shape additive. Existing consumers of `TitleDetail.to_dict()` must not break
  when the new fields are present.
- OMDb is per-title, not per-episode; do not spend a request per episode row.
- If `imdb_id` is absent (some anime), skip OMDb entirely and show AniList facts instead.
