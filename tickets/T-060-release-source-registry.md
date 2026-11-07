# T-060 — Release sources become a registry with per-kind settings

**Phase:** 16 · **Depends on:** T-059, T-052 · **Estimate:** M

## Goal

Replace the hardcoded `if "torrentio"` / `if kind == "anime" and "animetosho"` chain in
`services/torrents/sources.py` with a registry so that adding a source is one file and one
entry, not edits in three places. Expose the source list per media kind in settings, and let
a keyword source answer without an IMDb id.

This is the modularity prerequisite for T-061. No new network source lands here.

## Scope

### Registry (`services/torrents/registry.py`, new)

- `@dataclass(frozen=True, slots=True) class SourceSpec`:
  - `name: str` — stable id stored in settings and in `releases.metadata_json.resolved_from`;
  - `kinds: frozenset[str]` — subset of `{"movie", "series", "anime"}`;
  - `requires_imdb: bool` — `True` for `torrentio`, `False` for keyword sources;
  - `keyword: bool` — `True` when the source accepts a title query instead of an id.
- `SOURCES: dict[str, SourceSpec]` with `torrentio` (all three kinds, IMDb required),
  `animetosho` (anime only), and `torrents-csv` (movie/series, keyword). `torrents-csv` is
  registered here but its adapter lands in T-061; until then it must not be offered in the
  options payload or accepted by settings validation.
- `def available_sources(kind: str) -> tuple[str, ...]` and
  `def is_known_source(name: str, kind: str) -> bool`.
- `KeywordQuery` dataclass (in `base.py` alongside `Release`): `title: str`,
  `year: int | None`, `season: int | None`, `episode: int | None`, `pack: bool`,
  `aliases: tuple[str, ...]`.

### Aggregation (`services/torrents/sources.py`)

- `search_all_sources` keeps its signature but resolves each selected name through the
  registry. Missing id / wrong kind is a selection-time decision, not a branch inside the
  function.
- Torrentio keeps its current task handling. AnimeTosho keeps its query plan and matcher.
  Keyword sources receive a `KeywordQuery` and return `Release` rows plus `stale`/`cached_at`.
- Per-source failures stay isolated: one degraded source never fails the request, and the
  existing `sources_degraded` contract is preserved. `TorrentioError` remains the only
  all-sources-down error.
- `SourceSearchOutcome.queries` / `aliases` keep their meaning; add
  `per_source: dict[str, int]` (source name → row count) for the doctor and tests.

### Settings (`repositories/settings.py`, `routers/settings.py`)

- Keys become `sources_movie`, `sources_series`, `sources_anime`. The legacy `anime_sources`
  key stays readable: `get_sources(session, "anime")` falls back to it and normalises
  `nyaa` → `animetosho` exactly as today. No destructive migration; the next `PATCH` writes
  the new key.
- `default_settings_map` seeds `sources_movie=["torrentio"]`,
  `sources_series=["torrentio"]`, `sources_anime=["torrentio","animetosho"]`.
  `torrents-csv` is **not** seeded on upgrade (T-061 decides that); new installs after
  T-061 seed `["torrentio","torrents-csv"]` for movie/series.
- `normalize_sources(kind, values)` uses `available_sources(kind)`; unknown names and an
  empty selection are rejected with `INVALID_SOURCES` (422).
- `effective_sources(session, kind) -> list[str]` mirrors `effective_release_defaults`.
- `PATCH /api/settings` accepts `sources: {"movie": [...], "series": [...], "anime": [...]}`
  in addition to the legacy `anime_sources` field.

### Router (`routers/releases.py`)

- `sources` query parameter applies to every kind, validated with `available_sources(kind)`.
- Build the `KeywordQuery` from the title row (`title.title`, `title.year`, `aliases_json`
  entries) plus `season`/`episode`/`pack`.
- IMDb handling: `_media_query` is called only when `torrentio` is selected and
  `title.imdb_id` exists. If `torrentio` is selected without an IMDb id it is dropped from
  the selection; if that leaves nothing, raise `RELEASE_SOURCE_UNAVAILABLE` (422) with the
  existing message, now naming the selected sources.
- `/api/releases/options` returns `sources` (available for the kind) and
  `selected_sources`, and keeps `anime_sources` as a legacy alias.

### UI (`web/src/components/ReleasesSection.tsx`, `web/src/lib/api.ts`)

- Replace the anime-only source control with a per-kind source multi-select driven by
  `options.sources`; persist per kind in `localStorage` like the provider select.
- `SourceBadge` gains `torrents-csv` → `TORRENTS-CSV`; the degraded notice names the failed
  source generically (`"<Source> unavailable; showing remaining results."`).
- Settings page lists source toggles per kind with a one-line note per source.

### Doctor (`services/doctor.py`)

- Report each configured source as `ok | not_configured | disabled` without issuing a
  search, matching the existing AnimeTosho check style.

## Out of scope

The Torrents-CSV adapter (T-061), source-specific ranking boosts, per-source limits,
private trackers, Prowlarr/Jackett/Torznab, and any change to the `Release` shape.

## Acceptance criteria

- [x] Adding a source requires one `SourceSpec` entry plus one adapter module; no `if name ==`
      branches remain in `sources.py`.
- [x] `sources_movie`, `sources_series`, `sources_anime` persist per kind and survive restart.
- [x] A stored legacy `anime_sources` value is honoured and normalised on read.
- [x] A movie/series with no IMDb id still returns keyword-source results; a selection that
      contains only IMDb-required sources returns 422 `RELEASE_SOURCE_UNAVAILABLE`.
- [x] Disabling `torrentio` for a kind makes no Torrentio request for that kind.
- [x] One failing source yields `sources_degraded=["<name>"]` and the other rows, never a 502.
- [x] `GET /api/releases/options` reflects the available/selected sources per kind.
- [x] The SPA persists the per-kind source selection and renders the source badge.
- [x] The doctor lists every configured source without performing a search.

## Test plan

- `tests/torrents/test_registry.py`: available sources per kind, unknown names rejected,
  keyword/id capability flags.
- `tests/routers/test_releases.py`: per-kind source selection, no-IMDb keyword path, 422 when
  only IMDb sources remain, degraded source, options payload.
- `tests/routers/test_settings.py`: new keys, legacy `anime_sources` fallback, empty selection.
- `tests/test_health.py`: one doctor row per configured source.
- `web/src/pages/__tests__/releases.test.tsx`: source toggle and badge copy.

## Notes

Implemented together with T-061, so `torrents-csv` ships selectable for movie/series from the
start. Torrentio is silently dropped when a title has no IMDb id; a selection left with no
usable source returns 422 `RELEASE_SOURCE_UNAVAILABLE`. The doctor lists each configured source.
