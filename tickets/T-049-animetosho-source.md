# T-049 — AnimeTosho replaces Nyaa as the anime release source

**Phase:** 13 · **Depends on:** T-023, T-026, T-027 · **Estimate:** M

## Goal

Remove **nyaa.si** as Daku's second anime release source and replace it with
**AnimeTosho**'s public JSON feed (`https://feed.animetosho.xyz/json?q=<query>`), the same
endpoint used by the qBittorrent search plugin `animetosho.py` (v1.21).

The adapter stays behind the existing `Release` shape: search, save, download, ranking and
the source badge all keep working. AnimeTosho is a source, never a Torrentio provider and
never a download engine. No HTML scraping, no torrent-file download, no login.

Existing saved releases whose stored origin is `nyaa` remain readable. They are normalised
to the `animetosho` origin on read; no destructive data migration is performed.

## Scope

### Adapter (`services/torrents/animetosho.py`)

- Replace `services/torrents/nyaa.py`; delete the Nyaa adapter and its references.
- Feed URL: `https://feed.animetosho.xyz/json?q=<url-encoded query>`.
- Send a descriptive `User-Agent` (`daku/<version> (+self-hosted; contact in README)`).
  One request per second maximum per process; on `429`/`503` back off and serve stale cache.
- Parse the JSON array and read only fields that exist:
  - `info_hash` → 40-hex info hash (case-insensitive); invalid or missing hash is dropped;
  - `title`, falling back to `torrent_name` → filename, sanitised with the shared helper;
  - `total_size` → size in bytes; `seeders` / `leechers` → integers, missing means `0`;
  - `timestamp` → Unix seconds (metadata only);
  - `link` / `article_url` / `status` / `num_files` → metadata hints;
  - `torrent_url` is never downloaded; `magnet_uri` is not trusted blindly.
- Build the magnet locally from the validated info hash, display title and a fixed public
  tracker list in the module. The feed payload is cached raw for 1 h keyed by full URL;
  on upstream failure serve a stale payload with `stale=true`.
- `provider="animetosho"`; `metadata_json` gains
  `{source: <video source>, category: <article_title>, status, num_files, ...}` from the
  shared hint extractor. Never assume a field exists.

### Queries and matching

- Anime only. Movies and TV never call AnimeTosho.
- Query construction uses the title plus up to three AniList synonyms, then filters results
  against the requested `(season, episode)` or pack with the existing token matcher:
  `SxxEyy`, `xxXyy`, ` - yy`, `Eyy`, `Episode yy`, `[yy]`, and absolute numbering.
- Batches are packs and are never silently attached to one episode.
- Title matching is case-insensitive and punctuation-tolerant; require at least one token
  overlap. Cap to 3 upstream queries per search.
- Dedupe by lowercased `info_hash` (keep highest seeders), then use the shared
  quality/min-quality/sort/slice pipeline.

### Settings and status

- `anime_sources` defaults to `["torrentio", "animetosho"]`; allowed values are
  `torrentio` and `animetosho`; an empty selection is rejected.
- A stored legacy value of `nyaa` is normalised to `animetosho` on read so an existing
  install does not break its anime searches after the upgrade.
- `/api/system/doctor` exposes `animetosho: ok|not_configured` based on the saved setting.
- `AGENTS.md` §6 and `plan.md` decision 16 document the AnimeTosho feed contract instead
  of the Nyaa RSS contract.

### UI

- The anime source toggles and source badges read `AnimeTosho` / `ANIMETOSHO`.
- The degraded notice says `AnimeTosho unavailable; showing Torrentio results.` when
  applicable.
- The Settings note explains that AnimeTosho aggregates anime torrents and is rate-limited.

## Out of scope

Nyaa HTML/RSS scraping, private trackers, `sukebei`, NZB downloads, torrent-file downloads,
submitting comments, per-release comments, seeding policy, and adding more anime sources.

## Acceptance criteria

- [x] AnimeTosho JSON rows parse into valid `Release` objects with a local `urn:btih` magnet.
- [x] Items with a missing/invalid info hash or unusable title are dropped.
- [x] Anime searches query AnimeTosho and Torrentio concurrently and merge their rows.
- [x] Movie and TV searches never call the AnimeTosho feed.
- [x] Duplicate hashes across sources collapse to the higher seeder count.
- [x] `anime_sources=["torrentio"]` makes no AnimeTosho request; an empty selection is 422.
- [x] An AnimeTosho timeout returns Torrentio results with
      `sources_degraded=["animetosho"]`, not a 502.
- [x] Repeated searches within 1 h reuse the feed cache; stale responses are marked `stale`.
- [x] The Settings toggle persists and survives restart.
- [x] No Nyaa adapter, source option, badge or doctor check remains; legacy stored `nyaa`
      origins still render safely.
- [x] `AGENTS.md` §6 documents the AnimeTosho JSON contract and the doctor reports it.

## Test plan

- `tests/torrents/test_animetosho_parse.py`: fixture feed with a valid item, missing hash,
  missing title, invalid size, HTML entities and a missing optional field.
- `tests/torrents/test_animetosho_query.py`: URL encoding, control-character stripping,
  synonym queries, episode token matching and pack rejection.
- `tests/torrents/test_animetosho_fetch.py`: response caching, stale fallback, 429 handling.
- `tests/routers/test_releases.py`: anime-only gating, merged sources, degraded source,
  settings validation.
- `tests/test_health.py`: doctor check name.
- `web/src/pages/__tests__/releases.test.tsx`: badge/notice copy where practical.

## Implementation notes

Implemented 2026-10-30. The adapter lives in `services/torrents/animetosho.py`; the old
`nyaa.py` module and its RSS parsing are gone. Legacy `nyaa` settings and saved-release origins
are normalised to `animetosho` on read. The UI was rebuilt (`pnpm -C web build`).

- Tests use `respx`; CI never calls `feed.animetosho.xyz`.
- Keep the `Release` dataclass unchanged. Source-specific details go in `metadata_json`.
- Log `info_hash[:12]` only; never log the magnet, tracker or raw feed payload.
- The upstream payload is untrusted: cap response size and never write it to disk.
