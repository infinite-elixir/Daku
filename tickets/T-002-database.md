# T-002 — Database: schema, migrations, repositories

**Phase:** 0 · **Depends on:** T-001 · **Estimate:** M

## Goal

Create the full v1 SQLite schema behind a versioned migrator, with repository helpers the
rest of the app can use. No feature code.

## Scope

- `src/daku/db.py`: engine factory (`WAL`, `foreign_keys=ON`, `busy_timeout=5000`),
  `PRAGMA user_version` migrator, session dependency.
- `src/daku/models.py`: `titles` (including `wanted`, `wanted_at`), `episodes`, `releases`
  (including `metadata_json`, `is_saved`, `saved_at`, `last_checked_at`, `last_seen_seeders`,
  `miss_count`, `dead`), `downloads`, `media_files`, `telegram_messages`, `settings`, `events`.
- `src/daku/repositories/`: `titles.py`, `episodes.py`, `downloads.py`, `releases.py`,
  `settings.py`, `events.py`.
- `daku db`: `init`, `migrate`, `dump` CLI subcommands.
- Seed default `settings` rows for keys listed in `AGENTS.md` §4.

## Out of scope

Any HTTP endpoint beyond a health extension, any UI.

## Acceptance criteria

- [x] A fresh DB reaches `user_version = 1` and contains every table and index in
      `AGENTS.md` §5.
- [x] Re-running `daku db migrate` is a no-op and does not lose data.
- [x] `PRAGMA journal_mode` returns `wal`; `PRAGMA foreign_keys` returns `1`.
- [x] Deleting a title cascades to episodes, releases, media files and telegram links.
- [x] A title with zero `downloads`, zero `media_files` and zero `episodes` is a valid row and
      is returned by the library repository without special-casing.
- [x] A `releases` row can exist with `is_saved=1` and no `downloads.release_id` pointing at it.
- [x] `UNIQUE(engine, external_id)` on `downloads` and `UNIQUE(info_hash, episode_id)`
      on `releases` reject duplicates.
- [x] `UNIQUE(channel_id, message_id)` on `telegram_messages`.
- [x] Repository functions are typed and covered by tests.

## Implementation notes

- IDs are UUIDv4 strings generated in Python; `events.id` is the only autoincrement.
- Timestamps: `TEXT` UTC ISO-8601. Store naive UTC, append `Z` at the API boundary.
- Keep migrations as a list of SQL/step functions indexed by version. Append only.
- Add indices: `episodes(title_id, season, episode)`, `downloads(status)`,
  `releases(title_id, episode_id)`, `media_files(download_id)`, `events(ts)`.
- `media_files.is_primary` is not enforced by a constraint; enforce in the repository
  (`set_primary()` clears siblings in one transaction).
- Foreign keys use `ON DELETE CASCADE` except `downloads.release_id` → `SET NULL`.

## Test plan

- `tests/test_db.py`: fresh init, idempotent migrate, WAL/FK pragmas, cascade delete,
  unique constraints, `set_primary` reduces siblings to zero.
- Migration test builds a DB at v1, inserts rows, runs migrate again, asserts row counts.

## Completion notes

- Added `repositories/media_files.py` (needed for the `set_primary()` acceptance criterion).
- `downloads`, `media_files` and `telegram_messages` carry `created_at`; `downloads` also has
  `updated_at` for the worker.
- `releases` keeps the literal `UNIQUE(info_hash, episode_id)` constraint plus a null-safe
  unique index so movie releases (`episode_id` NULL) are deduplicated too.
- `settings` values are stored as JSON; secret fields are deliberately not seeded there and
  remain only in `secrets.json`.
- Schema version is 1. The anime mapping tables (T-005, v2) and `jellyfin_item_id` (T-014, v3)
  will be appended, not added here.
