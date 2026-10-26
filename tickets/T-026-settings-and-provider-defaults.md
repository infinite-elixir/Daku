# T-026 — Settings menu and per-kind release defaults

**Phase:** 7 · **Depends on:** T-023 · **Estimate:** M

## Goal

Give Daku a real settings surface, starting with the thing that varies most by media type:
**which release providers are searched for movies, TV and anime**. Today there is one global
`providers` list, and the UI remembers a provider selection in `localStorage`, so a phone and a
desktop can silently search different sets and a fresh browser loses the choice.

This ticket adds a persisted, server-side default per kind, plus a Settings page to edit it.
It deliberately does **not** expose paths, secrets or engine credentials — those stay in
`config.json` / `secrets.json` for now.

## Scope

### Settings keys (DB `settings` table, no migration)

| Key | Type | Default |
|---|---|---|
| `providers_movie` | `list[str]` | current `providers` (or Torrentio config) |
| `providers_series` | `list[str]` | current `providers` |
| `providers_anime` | `list[str]` | current `providers` |
| `default_min_quality` | `str \| null` | `null` (any) |
| `default_sort` | `seeders \| quality \| size` | current `sort` |
| `default_limit` | `int` (1–200) | current `limit` |

Resolution order for a release search:

1. explicit request query (`providers=`, `min_quality=`, `sort=`, `limit=`);
2. per-kind value from the `settings` table;
3. global `Settings.providers` / `sort` / `limit` / `torrentio_config`;
4. built-in defaults.

The keys are seeded by `seed_default_settings` from current config on first start, so an
existing install behaves exactly as before until the user changes something.

Add a small repository helper, e.g.
`repositories/settings.effective_release_defaults(session, kind) -> ReleaseDefaults`, and use it
from the releases router and `/api/releases/options`. Do not read the DB in every release row.

### API

`GET /api/settings` returns only the release section, never paths or secrets:

```json
{
  "release": {
    "providers": {"movie": [...], "series": [...], "anime": [...]},
    "min_quality": null,
    "sort": "seeders",
    "limit": 20
  },
  "sources": {"database": true, "config_keys": ["providers", "sort", "limit"]}
}
```

`PATCH /api/settings` accepts a partial payload of the same shape. Validation:

- providers: trimmed, lowercased, deduplicated, `^[a-z0-9._-]+$`, 1–20 entries when present;
  an empty list is allowed and means "no provider constraint" only if the kind is explicitly
  set to empty — prefer rejecting empty with `422 INVALID_SETTING` to avoid an accidental
  blank search. Document the choice in the response.
- `min_quality`: one of the existing `MIN_QUALITIES` values or `null`.
- `sort`, `limit`: existing enums/ranges.
- Unknown keys are rejected with `422 UNKNOWN_SETTING`; nested junk is rejected, not ignored.
- Every accepted change emits a `settings.updated` event; the response returns the full
  resolved settings so the UI does not refetch.

`GET /api/releases/options` gains `kind` (or `title_id`) and returns the **effective kind
defaults** plus a provider catalogue for checkboxes. Keep `defaults.providers` equal to the
kind default, not always `[]`; the existing per-search override still wins.

The release search response echoes the effective provider set and `defaults_source`
(`request | settings | config`), so the UI can say where a list came from.

### UI

- New route `/settings`, linked from the desktop nav and the mobile tab bar (replace System
  with a **More** sheet if four tabs are too tight; keep the nav honest at 390 px).
- Sections:
  1. **Default providers** — three rows (Movies, TV, Anime) of checkbox chips for
     `yts`, `eztv`, `1337x`, `thepiratebay`, `torrentgalaxy`, plus a free-text input for a
     provider Torrentio adds later. "Reset to config" per row.
  2. **Release search defaults** — minimum quality, sort, page size.
  3. **Integrations** — read-only doctor rows (qBittorrent, Jellyfin, TVDB, OMDb, Telegram,
     mapping, magnet probe once T-030 lands). Link to the existing System page instead of
     duplicating actions.
- Save is explicit (one primary action), shows pending and inline errors, and does not reload
  the page. A successful save invalidates the release-options query.
- `ReleasesSection` keeps its popover, but the initial selection comes from the kind default;
  a manual selection is still a per-search override and is **not** written back to settings.
  Rename the localStorage key or namespace it as an override (`daku.releaseProviders.override`)
  so a stale override cannot masquerade as the default. Provide "Use defaults" in the popover.

## Out of scope

Editing `config.json`, `secrets.json`, media paths, organise mode or engine URLs. Per-title
provider defaults (a title can still override per search only). Nyaa source toggles — T-027
adds its own settings row. Auto-download rules.

## Acceptance criteria

- [x] An install that never opens Settings searches exactly the same providers as before.
- [x] Changing the anime provider list affects only anime release searches; movie/series
      searches are unchanged.
- [ ] The choice survives a restart and is visible from a second browser without
      `localStorage`.
- [x] A per-search override in the popover wins for that search but leaves the saved default
      untouched.
- [x] "Use defaults" restores the saved kind defaults without a reload.
- [x] Invalid provider characters, empty selections, unknown keys and out-of-range values
      return stable `422` codes, never a Torrentio error.
- [x] `GET /api/settings` never contains a secret, path or credential.
- [x] The Settings page is usable and keyboard accessible at 390 px and 1440 px.
- [x] The mobile navigation can still reach Library, Search, Settings and System.

## Test plan

- `tests/routers/test_settings.py`: defaults from config, per-kind patch, validation errors,
  unknown key, secret exclusion, `settings.updated` event.
- `tests/routers/test_releases.py`: per-kind provider resolution, request override precedence,
  options endpoint defaults, `defaults_source`.
- `tests/db/test_settings_repo.py`: seed defaults, effective defaults fallback chain.
- `web/src/pages/__tests__/settings.test.tsx`: three provider rows, save, error, reset,
  navigation at mobile width.
- `web/src/pages/__tests__/releases.test.tsx`: default selection, override persistence,
  "Use defaults".

## Implementation notes

- Reuse `_provider_list` validation; do not add a second provider regex.
- Store JSON in the existing `settings` table; no new table and no migration.
- The settings page must not send a PATCH for unchanged fields.
- Do not cache settings in `app.state` without invalidation: a PATCH from one tab must affect
  the next search from another. Reading six rows per release search is cheap in SQLite.
- Update `AGENTS.md` §4 when the keys land, and `plan.md`'s decision log if the per-kind
  default becomes a product rule.
