# T-027 — Nyaa.si as a second release source

**Phase:** 7 · **Depends on:** T-007, T-023, T-026 · **Estimate:** L

## Goal

Anime releases are poorly covered by Torrentio's movie/TV providers. Add **nyaa.si** as an
independent anime release source, merged into the same release list, without pretending it is a
Torrentio provider. Nyaa is used through its **RSS feed only** — no HTML scraping, no login.

Nyaa results must look identical to every other release downstream: same `Release` shape, same
magnet, same save/download path. The only new concepts are a source badge and a source filter.

## Scope

### Adapter (`services/torrents/nyaa.py`)

- Base: `https://nyaa.si/`.
- Search URL:
  `https://nyaa.si/?page=rss&q=<query>&c=<category>&f=0&s=seeders&o=desc`
  - default category `1_2` (Anime — English-translated); allowed configuration
    `1_1` (raw), `1_2`, `1_3` (non-English), comma list.
  - `q` is URL-encoded; keep spaces, drop control characters, cap at 120 characters.
- Send a descriptive `User-Agent` (`daku/<version> (+self-hosted; contact in README)`).
  One request per second maximum per process; on `429`/`503` back off and serve stale cache.
- Parse RSS `<item>`:
  - `title` → filename; `nyaa:infoHash` → info hash (verify 40 hex / 32 base32);
  - `nyaa:seeders`, `nyaa:leechers`, `nyaa:downloads`, `nyaa:size`, `nyaa:categoryId`,
    `pubDate`;
  - `link` (torrent file) is **not** downloaded.
- Build the magnet locally from `infoHash` + `dn=<title>` + a fixed tracker list in the module
  (`nyaa.tracker.wf:7777` plus the standard public UDP/HTTP trackers already used by Torrentio
  magnets). The feed does not carry trackers; do not invent per-item trackers.
- Quality via the existing `detect_quality`; size via `parse_size_bytes`.
- `provider="nyaa"`; `metadata_json` gains `{source: "nyaa", category, trusted?, remake?}` when
  the feed exposes them. Never assume a field exists.

### Queries and matching

- Anime only. The router decides eligibility: `title.kind == "anime"`, or a classification
  signal from T-028. Movies/TV never hit nyaa.
- Query construction uses the title plus up to three AniList `synonyms`, then filters results
  against the requested `(season, episode)` or pack:
  - recognised episode tokens: `SxxEyy`, `xxXyy`, ` - yy`, `Eyy`, `Episode yy`, `[yy]`;
  - absolute numbering uses the episode's `absolute` value when present;
  - season packs are allowed only with `pack=true` or when the user downloads a pack
    explicitly;
  - batches (`01-12`) are marked as packs and never silently attached to one episode.
- Title matching is case-insensitive and punctuation-tolerant; require at least one title or
  synonym token overlap. Drop results with no overlap.
- Cap to 3 upstream queries per search, dedupe by info hash, then hand off to the shared
  rank/filter pipeline.

### Aggregation

- New `services/torrents/sources.py` (or extend the existing search orchestration):
  `search_all_sources(...) -> SourceSearchOutcome` with per-source state
  (`ok | stale | unavailable | disabled`).
- For anime, query the enabled sources concurrently with a shared deadline; merge releases,
  dedupe by lowercased `info_hash` (keep highest seeders, as T-023), then apply the existing
  quality/min-quality/sort/slice order. Record `sources` and `sources_degraded` in the response.
- Setting `anime_sources` in the `settings` table (from T-026) defaults to
  `["torrentio", "nyaa"]`. An empty value is rejected. The Settings page gets a source toggle
  row for anime with a short note about coverage/rate limits.
- If one source fails, the response is still `200` with the other source's results and
  `sources_degraded: ["nyaa"]`. Only fail with `RELEASES_UNAVAILABLE` when every source fails
  and no stale cache exists.

### Cache and doctor

- Cache raw RSS payloads for 1 h keyed by the full URL; on failure serve stale and set
  `stale=true`. Cache failures for 5 min.
- A per-process request throttle (`asyncio.Lock` + minimum interval) is mandatory.
- `/api/system/doctor` gains `nyaa: ok|unreachable|disabled`, cached for 10 min; no live call on
  every doctor request.

### UI

- Provider/source badge on each row: `NYAA` or `TORRENTIO`, neutral styling, with an accessible
  label. Do not use a kind colour for a source.
- The existing provider popover gains a **Sources** section (Torrentio / Nyaa) for anime only;
  it writes the per-search override, not the saved setting.
- Degraded state: a one-line notice above results when a source failed (`Nyaa unavailable;
  showing Torrentio results.`) and a Retry action.
- Saved releases keep their `provider` value; the Saved tab shows `NYAA` for nyaa-sourced rows.

## Out of scope

Nyaa HTML scraping, authenticated/private trackers, non-anime categories, `sukebei`, torrent
file downloads, submitting comments, per-release comments, and any seeding policy. Adding more
public sources (e.g. AniDex) is a later ticket with the same adapter interface.

## Acceptance criteria

- [x] An anime episode search returns both Torrentio and Nyaa rows when both have releases, and
      each row says which source it came from.
- [x] A movie or TV search never calls nyaa.si.
- [x] Nyaa rows carry a valid `urn:btih` info hash, seeders, size and a magnet built by Daku.
- [x] Duplicate info hashes across Torrentio and Nyaa collapse to one row with the higher
      seeder count.
- [x] `anime_sources=["torrentio"]` makes no nyaa request; disabling both is rejected by
      settings validation.
- [x] A nyaa timeout returns Torrentio results with `sources_degraded=["nyaa"]`, not a 502.
- [x] Repeated searches within 1 h reuse the RSS cache; a stale response is marked `stale`.
- [x] RSS items missing `infoHash` or a usable title are dropped, never rendered as broken rows.
- [x] The Settings toggle persists and survives restart.
- [x] `AGENTS.md` §6 documents the verified RSS contract, and the doctor reports nyaa status.

## Test plan

- `tests/torrents/test_nyaa_parse.py`: fixture feed with single episode, batch, missing hash,
  bad size, non-English category, trusted/remake flags, HTML entities.
- `tests/torrents/test_nyaa_query.py`: synonym queries, episode token matching, absolute
  numbers, pack rejection, punctuation.
- `tests/torrents/test_sources.py`: merge/dedupe, one-source failure, stale, all-fail error.
- `tests/routers/test_releases.py`: anime-only gating, source setting, degraded response.
- `tests/services/test_doctor.py`: nyaa status caching.
- `web/src/pages/__tests__/releases.test.tsx`: source badges, degraded notice, source toggle.

## Implementation notes

- Add fixtures from a recorded RSS response; tests must never call nyaa.si.
- Keep the nyaa adapter behind the same `Release` dataclass. Do not add nyaa-specific fields to
  the API response; source-specific details go into `metadata_json`.
- The RSS feed may include one item per episode of a batch; dedupe and pack detection must be
  deterministic. Do not use the torrent `link`/`.torrent` file to recover metadata.
- Do not put the full magnet in logs; log `info_hash[:12]` only.
- If T-026 has not landed, fall back to a module-level default and leave the setting for T-026;
  do not invent a second settings store.
