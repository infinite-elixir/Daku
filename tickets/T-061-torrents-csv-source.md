# T-061 — Torrents-CSV replaces nothing and adds DHT-wide coverage

**Phase:** 16 · **Depends on:** T-060 · **Estimate:** M

## Goal

Add **Torrents-CSV** (`https://torrents-csv.com`) as a third release source for movies and
TV, alongside Torrentio. It is a DHT-wide index, so it sees releases the curated public
sites Torrentio scrapes never list. It has a plain JSON endpoint, no login, no API key and no
torrent-file download — the same shape as AnimeTosho.

Torrents-CSV is a **source**, never a Torrentio provider and never a download engine. The
`Release` dataclass does not change.

## Scope

### Shared helpers (small, behaviour-preserving)

- `services/torrents/magnets.py` (new): move `PUBLIC_TRACKERS` and `build_magnet(info_hash,
  name)` out of `animetosho.py`; `animetosho` imports and re-exports them so existing tests
  and imports keep working.
- `services/torrents/throttle.py` (new): move the per-loop lock + 1 req/s logic out of
  `animetosho.py` into `async def throttle(key: str, interval: float = 1.0) -> None`.
  AnimeTosho switches to it with no behaviour change; each source uses its own key.
- `services/torrents/matching.py` (new): move `matches_query`, `matches_episode`,
  `pack_covers_episode`, `pack_label`, `parse_release_episode` and their helpers out of
  `animetosho.py` (repo-export from the old module). They are already generic; nothing about
  their behaviour changes.

These three moves are mechanical and must not alter any AnimeTosho test.

### Adapter (`services/torrents/torrentscsv.py`, new)

- `SEARCH_BASE = "https://torrents-csv.com/service/search"`.
- `build_search_url(query)`: strip control characters, cap at 120 characters, `quote_plus`,
  append `size=100`, e.g. `…/service/search?size=100&q=Severance+S02E05`.
- Send the shared `User-Agent` (`daku/<version> (+self-hosted; contact in README)`).
  One request per second through `throttle("torrents-csv")`.
- Read the JSON object and only fields that exist:
  - `infohash` → 40-hex, case-insensitive; invalid or missing is dropped;
  - `name` → filename, sanitised with the shared `sanitize_filename`;
  - `size_bytes` → int (already bytes, never a human string);
  - `seeders` → int, missing means `0`;
  - `leechers`, `completed`, `created_unix`, `id` → metadata hints only.
- Build the magnet locally from the validated hash and the shared public tracker list.
  Never trust a remote magnet string; there is none in the payload.
- `provider="torrents-csv"`; `quality=detect_quality(filename)`; `metadata=extract_hints(...)`
  plus `{"leechers": …, "completed": …, "published_at": …}`.
- Sort parsed rows by seeders descending before returning; cap at the 100 rows requested.
  Dedupe, blocked labels, min-quality, sort and slice stay in the shared pipeline.
- Payload cap 8 MiB, deadline 8 s, raw payload cached 1 h keyed by full URL, stale payload
  served with `stale=true` on failure. Reuse `PayloadCache` exactly as the other adapters do.
- `TorrentsCsvError(url, detail)` mirrors `TorrentioError`/`AnimeToshoError`. A failure is
  degraded, not fatal, while any other source returned rows.

### Queries and matching

- Movies/series only; never anime (AnimeTosho already owns anime).
- One query per request, no pagination, no parallel queries:
  - movie → `"<title> <year>"` when `year` is known, else `"<title>"`;
  - episode → `"<title> S<season:02d>E<episode:02d>"`;
  - pack with `season` → `"<title> Season <n>"`; with no season → `"<title>"`.
- Gate every row before it becomes a `Release`:
  - `matches_query(primary_title, filename, aliases)` must pass;
  - when the request has a `year` and the filename carries a four-digit year, the years must
    differ by at most 1;
  - episodes use `matches_episode(...)` with the existing provider-season/fallback arguments;
  - a pack is only attached when `pack=true` or when `pack_covers_episode(...)` says it covers
    the requested episode, and then it is flagged `is_pack=True` with `pack_label(...)`.
- No match ⇒ drop the row. Never "best effort".

### Wiring

- `registry.SOURCES` is the only place the name appears; the adapter exposes
  `fetch_releases(client, cache, query: KeywordQuery) -> TorrentsCsvSearch`, mirroring
  `AnimeToshoSearch` (`releases`, `stale`, `cached_at`, `url`).
- `services/releases/metadata.py`: `resolved_from` maps `torrents-csv` → `torrents-csv`;
  unknown values still fall back to `manual`.
- `AGENTS.md` §6 gains a "Torrents-CSV JSON API" contract block with the endpoint, fields,
  throttle, cache and the local-magnet rule.

## Out of scope

Torznab/Prowlarr/Jackett, private trackers, pagination, alias expansion for movies/TV,
torrent-file downloads, and any UI beyond the source badge/selector landed in T-060.

## Acceptance criteria

- [x] A recorded Torrents-CSV payload parses into valid `Release` rows with a local
      `urn:btih` magnet and byte sizes.
- [x] Rows with a missing/invalid info hash, empty name or non-positive size are dropped.
- [x] Movie searches query `"<title> <year>"`; episodes query `"<title> SxxEyy"`; packs query a
      season only when `pack=true`.
- [x] Rows failing the title/year gate never reach the result list.
- [x] Episodes with no exact match may surface a covering pack flagged `is_pack=true`.
- [x] Anime searches never call Torrents-CSV; movie/TV searches never call AnimeTosho.
- [x] Duplicate hashes across Torrentio and Torrents-CSV collapse to the higher seeder count.
- [x] A Torrents-CSV timeout returns the other sources with `sources_degraded=["torrents-csv"]`.
- [x] Repeated searches within 1 h reuse the cached payload; stale payloads are marked `stale`.
- [x] One request per second is enforced and the adapter never issues more than one query per search.
- [x] `AGENTS.md` §6 documents the endpoint and the local-magnet rule.

## Test plan

- `tests/fixtures/torrents_csv_search.json`: recorded response with a 2160p movie, a 1080p
  episode, a season pack, a malformed hash, a missing name and a zero-seeder row.
- `tests/torrents/test_torrentscsv_parse.py`: field mapping, sanitisation, magnet build,
  malformed rows, optional-field defaults.
- `tests/torrents/test_torrentscsv_query.py`: URL encoding, control-character stripping, the
  three query shapes and the year gate.
- `tests/torrents/test_torrentscsv_fetch.py`: cache reuse, stale fallback, oversized payload,
  HTTP error, throttle spacing (fake clock, no sleeping in CI).
- `tests/routers/test_releases.py`: movie/TV merge with Torrentio, dedupe, degraded source,
  anime exclusion.
- `tests/torrents/test_animetosho_*.py`: unchanged after the helper moves.

## Notes

Implemented. The adapter builds magnets locally, sorts rows by seeders, caps at 100, throttles
one request per second and serves stale cached payloads on upstream failure. Tests cover parsing,
query shapes, the title/year gate, cache/stale/oversize/timeout behaviour and router merging.
