# T-053 — Search every alias, in several notations, then match episodes honestly

**Phase:** 15 · **Depends on:** T-037, T-049, T-052 · **Estimate:** L

## Why

Release search currently under-searches and over-filters:

1. `routers/releases.py` calls `search_all_sources` **without `synonyms`**, so only
   `title.title` is ever queried. AniList knows the English, romaji, native and synonym
   spellings; AniBridge knows provider spellings; none of them reach the source adapters.
2. `sources.py` appends the episode number to the title (`"Steel Ball Run 07"`) instead of
   searching the name and matching locally. That narrows an already-small feed and prevents
   the name from matching releases that use a different notation.
3. `matches_episode` only accepts the library's own season (`S01E07`), `E07`, `- 07`,
   `[07]`, or `episodes.absolute`. AnimeTosho publishes Steel Ball Run as JoJo overall
   **`S06E07`**, and because T-052's identity is broken the title has no provider season and
   no absolute numbers, so every valid release is filtered out.
4. `matches_query` accepts **any single token** overlap. Loosening the query set without
   fixing this would fill the panel with unrelated releases.
5. `FEED_BASE` is `https://feed.animetosho.xyz/`; the host now answers `308` to
   `https://feed.animetosho.net/`. The shared client follows redirects, but the cache key
   is the pre-redirect URL, so stale/fresh entries are split per host.

The Steel Ball Run episode 7 case is the reference test: querying `Steel Ball Run` returns
75 rows with `S06E07` releases at the top; the current pipeline discards all of them.

## Goal

Given a title, search every known name in several notations, bounded by a polite request
budget, merge and dedupe the results, and accept a release when its **parsed** episode
number and provider season agree with the library entry — including split-cour season
offsets — without attaching batches to a single episode.

## Scope

### 1. Alias inventory

- Persist an alias list per title (migration v11 adds `titles.aliases_json`, a JSON array of
  strings). Populate it on refresh/create from:
  - the title itself;
  - AniList `title.english`, `title.romaji`, `title.native`, and every `synonyms[]` entry;
  - AniBridge aliases for the mapped provider id when present;
  - a manual `aliases` identity override, which wins over generated values.
- `GET /api/titles/{id}` exposes `aliases` so the UI and the fixer can show/edit them.
- Add `aliases` to the editable identity fields; an override row freezes the user's list
  against later refreshes. Clearing the alias override returns to generated aliases.
- AniList detail is cached. Building aliases must not issue a new network call per release
  search.

### 2. Derived query styles

For each alias, derive a small, deterministic set of spellings:

- trim, collapse whitespace, drop a trailing year (`(2026)`, `2026`);
- drop punctuation that providers rewrite (`:` `'` `&` `.` `!`), keeping a spaced form and
  a compact form (`"JoJo's"` → `"JoJo s"` / `"JoJos"`);
- strip a leading franchise phrase only when the remainder is still ≥ 2 significant tokens
  (`"Steel Ball Run JoJo's Bizarre Adventure"` → `"Steel Ball Run"`);
- acronym of the significant words when the alias has ≥ 3 words (`"Steel Ball Run"` → `"SBR"`);
- native/CJK aliases are sent as-is (AnimeTosho indexes them).

Do **not** generate free-form translations, fan titles or guesses. The alias list is the
source of truth.

### 3. Query plan and budget

- Build the plan from every name, not the first three:
  `{alias, alias + episode, alias + Eyy, alias + SxxEyy (provider season), alias + Episode yy}`.
- Normalize each query (casefold, collapse whitespace, strip punctuation) and dedupe. Keep
  at most `MAX_QUERIES = 8` per search, ordered by:
  1. bare aliases (best recall; the feed caps at 75 rows),
  2. aliases with an explicit provider-season notation (`S06E07`),
  3. episode-only suffixes (`07`, `E07`, `Episode 7`).
- Run the selected queries concurrently as asyncio tasks. The existing per-process 1 req/s
  throttle stays authoritative; concurrency must not bypass or parallelise the throttle.
- Stop issuing new queries when either 40 candidate releases are collected, the 8-query
  budget is spent, or the search deadline (8 s) expires. Cancelled requests are fine.
- Search the bare name first. Append the episode only when the bare name returns fewer than
  20 rows after local matching, so the common case is one request per alias.
- Respect the existing 120-character query cap and control-character stripping.

### 4. Query matching (precision)

Replace the any-token test with a coverage test:

- Normalize both sides to lowercase alphanumeric tokens; fold `jojos`/`jojo's`/`jojo s`.
- Ignore a small stopword set (`the`, `of`, `a`, `part`, `season`, `movie`, `ova`, `ona`).
- Accept when **all** significant tokens of at least one alias are present in the filename,
  or when a single-token acronym (≥ 3 letters) appears as a whole token.
- A single common token (`run`, `steel`) is not enough on its own; it must belong to a
  2+ token alias or be an acronym.
- This remains case- and punctuation-insensitive. It must match
  `JoJos Bizarre Adventure ... (Steel Ball Run ...)` and reject a different 2026 show.

### 5. Episode matching (tolerant, but exact)

Parse the release filename once into a structure:

```
ReleaseEpisode(season: int | None, episode: int | None, absolute: int | None,
               is_pack: bool, notation: str)
```

Recognise: `S06E07`, `s6e7`, `06x07`, `E07`, `EP07`, `Episode 7`, `- 07`, `[07]`, `07v2`,
`第7話`, ranges (`S01E01-E12`, `01-12`, `1~12`) as packs, and absolute-only numbers
(`- 07` with no season). Version suffixes (`v2`) do not change the episode number.

Accept a release for a single-episode request when:

- `release.episode == requested.episode` **and** one of:
  - `release.season == requested.season`;
  - `release.season == provider_season` from `title_seasons`/`title_cours` (T-052), e.g.
    requested `S01E07` matches JoJo `S06E07`;
  - the request has an absolute episode and `release.episode == absolute` with no season,
    or `release.absolute == absolute`;
  - `release.season is None` and the title match came from a 2+ token alias.
- `release.is_pack` is `False` for an exact episode match. Ranges/batches are never counted as
  the requested episode; they stay available for explicit pack requests. They may still be
  returned **as flagged sources** for the episode (see "Season packs as episode sources").
- Season 0 specials only match `requested.season == 0`.

Reject `S06E07` when the request is episode 8, and reject `S01E07` from a different show even
if the title token overlaps. The absolute fallback never overrides a conflicting explicit
season on a different cour.

### 6. Source and diagnostics

- Change `FEED_BASE` to `https://feed.animetosho.net/` and keep following redirects for old
  cached URLs. The cache key remains the full request URL.
- Pass `synonyms=title_aliases` from `routers/releases.py`; never rely on the adapter to
  rediscover names.
- Add a compact diagnostic to the releases response (no magnets, no payload):
  `search: {queries: <count>, aliases: <count>, degraded: [sources]}` behind the existing
  response model. It is informational, not a new state.
- Log only `info_hash[:12]`, query count and matched notation. Never log raw aliases at
  `INFO` if they contain the title? Titles are not secrets; log at `DEBUG` only.

## Out of scope

- Adding new release sources or providers.
- Bypassing AnimeTosho's rate limit, scraping HTML, or downloading `.torrent` files.
- Automatic download of the "best" release; search still returns a ranked list.
- Guessing a season mapping from filenames; T-052 owns provider season metadata.

## Acceptance criteria

- [x] `routers/releases.py` passes every persisted alias/synonym; a title with 6 aliases
      produces queries for all of them within the budget.
- [x] Alias generation covers AniList english/romaji/native/synonyms and honours a manual
      `aliases` override.
- [x] Queries are normalized and deduped; no more than 8 upstream AnimeTosho requests are
      issued for one search, and the 1 req/s throttle is never bypassed.
- [x] `Steel Ball Run` episode 7 returns the AnimeTosho `S06E07` rows when the title maps
      provider season 6, and returns them even with absolute numbering absent.
- [x] Episode 8 never matches `S06E07`; a batch `S01E01-E12` is never counted as the exact
      episode (it may be returned separately, flagged as a pack — see below).
- [x] A different 2026 show with the word `Run` in the name is rejected by the alias
      coverage rule.
- [x] Duplicate hashes across the expanded queries/sources collapse to the highest seeder
      count, and quality/sort/limit still apply afterwards.
- [x] A partial upstream failure still returns merged results and marks the source degraded.
- [x] `feed.animetosho.net` is used directly; a cached `.xyz` URL still resolves.
- [x] The UI shows no regression in the release panel, and the diagnostic count is not
      user-visible noise.
- [x] A season/batch pack that contains the requested episode is returned in the same ranked
      list with `is_pack=true` and a `pack_label` (`S06`, `E01-E12`, `Complete`), while an
      ordinary exact row keeps `is_pack=false`.
- [x] Packs with no quality token survive the default `unknown` drop; ordinary unknown rows
      are still dropped.
- [x] The UI renders a `PACK <label>` badge for pack rows and nothing for exact rows.

## Test plan

- `tests/torrents/test_animetosho_query.py`: alias/style generation, dedupe, budget cap,
  stop-at-40 behaviour, 120-char cap.
- `tests/torrents/test_episode_matching.py`: `S06E07` vs `S01E07` with provider season 6;
  absolute-only `07`; `07v2`; `[07]`; `Episode 7`; batch ranges; wrong episode; wrong show.
- `tests/torrents/test_animetosho_parse.py`: filename with both franchise and `SBR` tokens
  matches; a single-token false positive does not.
- `tests/routers/test_releases.py`: synonyms passed from the router; 8-query ceiling; merged
  and deduped results; diagnostic shape.
- `tests/routers/test_releases_anime_smoke.py` (or fixture): Steel Ball Run `S06E07` fixture
  returns results for `season=1, episode=7` once T-052's provider season is present.
- `web/src/pages/__tests__/releases.test.tsx`: unchanged rendering for the merged payload.

## Implementation notes

- Aliases ship as migration **v12** (`titles.aliases_json`): v11 is T-052's `title_cours`.
- AniBridge does not expose title aliases in the current dataset, so generation uses the
  stored title, AniList english/romaji/native/synonyms and cour extras. A manual `aliases`
  override is stored in `title_overrides` and wins; clearing it restores generated values.
- The AnimeTosho throttle lock is per event loop, so tests that create a new loop per
  `TestClient` still share the single in-process 1 req/s budget.
- Alias styles strip trailing stage/cour/season/part qualifiers and keep the romanized
  base name (before a colon or spaced dash) plus the distinctive subtitle. This stops a
  long AniList title such as `STEEL BALL RUN JoJo's Bizarre Adventure 1st STAGE` from
  spending the whole query budget on narrow spellings.

### Season packs as episode sources (follow-up to the exact-match rule)

Exact matching alone hid the only releases that exist for many airing anime: groups publish
a whole cour as `(Season 2) (Batch)` or `01 ~ 10`. This follow-up keeps the exact-match rule
but adds packs as *sources* for the requested episode:

- `animetosho.parse_release_episode` recognises `(Season N)`, `(Batch)`, `COMPLETE`,
  `全集`/`合集` and numeric ranges (`01 ~ 12`, `S01E01-E12`) as packs, and returns
  `ReleaseEpisode.end` for a range. Stray numbers in a pack title are never guessed into an
  episode.
- `pack_covers_episode()` accepts a pack for the episode when its provider season is the
  requested season or a `provider_season` (T-052), a season-less range contains the episode,
  or a bare batch matches the title. `pack_label()` renders `S06`, `E01-E12` or `Complete`.
- `sources.search_all_sources` returns these in `SourceSearchOutcome.packs` (deduped by
  info-hash), never in `releases`. `routers/releases.py` merges both lists before ranking,
  so packs are visible in the normal result list.
- `Release` gains `is_pack`/`pack_label`; the API and `ReleaseItem` expose them and the UI
  shows a `PACK <label>` badge with a tooltip explaining that only the selected episode's
  file is downloaded.
- A pack carrying no video-quality token parses as `unknown`. Packs are exempt from the
  default `unknown` drop (the organiser inspects the real file later); ordinary rows are
  still filtered. `min_quality` still applies to packs because it is an explicit user floor.
- Pack rows are not counted in `search` diagnostics, whose shape stays
  `{queries, aliases, degraded}`.

## Rollout

1. Ship T-052 first; without provider seasons/aliases this ticket cannot match split cours.
2. Ship T-053 behind the existing `anime_sources` setting; no migration of saved releases.
3. Verify on the live Steel Ball Run entry: episode 7 returns rows, episode 8 does not, and
   the diagnostics show the expected query count.
