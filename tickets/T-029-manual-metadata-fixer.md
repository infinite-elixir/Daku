# T-029 — Manual metadata fixer

**Phase:** 7 · **Depends on:** T-021, T-022, T-028 · **Estimate:** L

## Goal

Some entries will always be wrong: a series added as a movie, an anime that missed the AniBridge
mapping, a TVDB season offset, or a duplicate identity. Today the only repair is deleting and
re-adding, which loses episode ids, saved releases and download history.

Add a per-title **Fix metadata** flow that edits identity and episode numbering, persists the
result as an override, and survives every later metadata refresh.

## Scope

### Schema (next free `user_version`, append-only)

```sql
CREATE TABLE title_overrides (
  title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
  field TEXT NOT NULL,              -- kind|title|year|imdb_id|tvdb_id|anilist_id|metadata_source
  value_json TEXT NOT NULL,         -- JSON scalar (string|int|null)
  updated_at TEXT NOT NULL,
  PRIMARY KEY (title_id, field)
);

CREATE TABLE title_episode_overrides (
  id TEXT PRIMARY KEY NOT NULL,
  title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
  season INTEGER NOT NULL,
  episode INTEGER NOT NULL,
  target_season INTEGER NOT NULL,
  target_episode INTEGER NOT NULL,
  absolute INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (title_id, season, episode)
);

CREATE TABLE title_season_offsets (
  title_id TEXT NOT NULL REFERENCES titles (id) ON DELETE CASCADE,
  provider_season INTEGER NOT NULL,
  library_season INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (title_id, provider_season)
);
```

- `title_overrides` is the durable "the user said so" record. `titles.*` is still updated so
  list queries and existing readers keep working; the override row exists so a refresh knows
  what not to overwrite.
- `title_season_offsets` maps a provider season to a library season (e.g. provider season 2 is
  library season 1 because of a split cour). `title_episode_overrides` handles one-off
  renumbering and absolute numbers.
- Deleting a title cascades all three; media files are untouched.

### Semantics

- Editable fields: `kind` (`movie|series|anime`), `title`, `year`, `imdb_id`, `tvdb_id`,
  `anilist_id`, `metadata_source`.
- Identity changes must keep at least one usable identity for the kind:
  - `movie|series` requires `imdb_id` or `tvdb_id`;
  - `anime` requires `anilist_id` (or `tvdb_id` as the T-022 fallback).
- Uniqueness is enforced by the existing partial indexes. A collision returns
  `409 IDENTITY_CONFLICT` naming the existing title, never a raw SQLite error.
- Changing `kind` is a structural change:
  - `movie → series/anime` creates the episode list through the refresh path; it never invents
    episodes locally;
  - `series/anime → movie` is blocked with `409 KIND_HAS_EPISODES` when episodes, downloads or
    media files exist. `?force=true` after an explicit confirmation removes episode rows only
    (cascading their releases), never files on disk.
- A refresh (T-022) must apply overrides after fetching:
  1. ignored provider values for overridden fields;
  2. season offsets before upserting episodes;
  3. episode overrides after upserting, without deleting the original rows unless the user
     explicitly renumbers (the UI shows old → new).
- Removing an override restores the provider value on the next refresh, not immediately.

### API

- `GET /api/titles/{id}/overrides` → `{fields: {...}, season_offsets: [...], episodes: [...]}`
  plus `provider_values` for the same fields so the UI can show "provider says X".
- `PATCH /api/titles/{id}/identity` body:
  `{kind?, title?, year?, imdb_id?, tvdb_id?, anilist_id?, metadata_source?, confirm?}`.
  Validates, writes the title + override rows in one transaction, emits
  `title.identity_updated`, returns the updated title payload.
- `PUT /api/titles/{id}/episode-map` body:
  `{season_offsets: [{provider_season, library_season}], episodes: [{season, episode,
  target_season, target_episode, absolute}]}`. Replaces the override set atomically.
- `DELETE /api/titles/{id}/overrides/{field}` removes one field override.
- Reuse `POST /api/titles/{id}/metadata/refresh` after an identity change; the response reports
  `overridden_fields` so the user sees what was preserved.
- All changes emit a `metadata.fixed` event with field names, never the old/new values in the
  payload if they include personal paths. Values are fine here; no secrets exist in metadata.

### UI

- **Fix metadata** action on the title page (secondary button, not primary). Opens a dialog
  following STYLE.md §5.7: focus trap, `Esc`, bottom sheet on mobile.
- Sections:
  1. **Identity** — kind segmented control, title, year, IMDb/TVDB/AniList ids, metadata source.
     Inline validation and a "provider says …" hint per field.
  2. **Episodes** — a compact table of season offsets and per-episode overrides, with
     **Add offset**, **Add episode override**, and remove. Show the current episode count.
  3. **Warning panel** — appears only for destructive kind changes, listing affected episodes,
     saved releases and downloads. The confirm button uses `--danger` and requires the word
     `fix` or a checkbox, not a second identical button.
- After save: invalidate title, library, metadata and releases queries; show a result line
  (`Identity updated; 10 episodes kept`). Do not navigate away.
- If the title is in the library with downloaded media, suggest **Duplicate as new title** as
  the safe alternative (out of scope to implement if it needs a new clone endpoint; at minimum
  link to Add from search).

## Out of scope

Bulk editing, merging two library rows, manual artwork/overview, editing individual media
files, changing Jellyfin item ids, cloning a title with its files. Automatic reclassification
without confirmation (T-028).

## Acceptance criteria

- [x] A series mis-added as a movie can be changed to series, gets episodes from the provider,
      and keeps its saved releases.
- [x] A series can be changed to anime by supplying an AniList id; the AniList detail and
      absolute numbers replace the TVDB numbering.
- [x] Changing kind to movie is blocked while episodes or media exist, and the error names the
      blocker.
- [x] A duplicate IMDb/AniList id returns `409 IDENTITY_CONFLICT` with the existing title, not
      a 500.
- [x] A season offset shifts provider season 2 to library season 1 without duplicating
      episodes, and survives a refresh.
- [x] A per-episode override changes the target episode/absolute number and is visible in the
      episode list.
- [x] Removing an override restores the provider value on the next refresh.
- [x] No metadata refresh can overwrite an overridden field.
- [x] No file on disk is deleted by any fixer operation; `keep_files` behaviour is unchanged.
- [x] The dialog is keyboard operable at 390 px and 1440 px and shows a destructive warning.

## Test plan

- `tests/db/test_migrations.py`: new tables, cascade, unique constraints.
- `tests/routers/test_metadata_fixer.py`: each field, kind transitions, conflict 409, forced
  movie conversion, overrides applied on refresh, removal, events.
- `tests/repositories/test_overrides.py`: season offsets, episode remap, no duplicate episodes.
- `web/src/pages/__tests__/metadata-fixer.test.tsx`: dialog, validation, warning, save result.
- Regression: T-022 refresh tests still pass with no overrides present.

## Implementation notes

- Put the apply-overrides logic in one service, `services/metadata/overrides.py`; routers and
  refresh both call it. Do not duplicate the merge rules.
- Update `titles.*` and the override rows in the same transaction; a failed refresh must leave
  both untouched.
- `title_episode_overrides` is intentionally separate from `anime_mapping_overrides` (T-005):
  the latter is a provider-to-provider mapping dataset; the former is a user decision for one
  library title.
- Use `AppError` codes `INVALID_IDENTITY`, `IDENTITY_CONFLICT`, `KIND_HAS_EPISODES`,
  `INVALID_EPISODE_MAP`.
- Update `AGENTS.md` §5 (new tables) and §7 (no new state machine states).

## Completion notes

- `provider_values` reports the current title values for fields without an override and
  `null` for overridden fields, because no provider snapshot is stored. The UI therefore shows
  the "provider says …" hint only while a field is not overridden.
- The destructive warning offers an **add a copy from search** link as the safe alternative;
  a clone endpoint is out of scope and was not added.
- Season offsets are applied before the episode upsert and per-episode overrides after it, so a
  refresh never duplicates episodes. Removing an override restores the provider value on the
  next refresh, not immediately.
- The existing Frieren, Edgerunners and Mushoku Tensei rows are intentionally not bulk-fixed;
  each is corrected per title through this dialog.
