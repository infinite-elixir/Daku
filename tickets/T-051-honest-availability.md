# T-051 — Availability must reflect files that actually exist

**Phase:** 14 · **Depends on:** T-050, T-013 · **Estimate:** M

## Goal

`media_files` currently means "Daku once recorded this file". The API treats every row as
present, so a title keeps showing episodes as available after the file was deleted, and a
re-download of the same episode is refused as a duplicate. Make availability mean "a file at
`lib_path` (or `src_path`) exists on disk", and let the user recover from a missing file.

## Root causes (verified 2026-10-30)

- `src/daku/repositories/titles.py:170-233` derives `none|partial|complete` and the available
  episode ids purely from `COUNT(media_files.*)`. No path is ever checked.
- `src/daku/routers/titles.py:176-179` marks an episode `available` when its id is in that
  set. A title detail for Cyberpunk still reports 3/3 downloaded episodes and
  `availability: partial` after the folder was deleted.
- `src/daku/routers/downloads.py:245-266` (`_requested_files_already_present`) also trusts
  rows by filename. With the stale Cyberpunk rows it returns 409
  `DOWNLOAD_ALREADY_TRACKED` ("already in your library") even though there is no file to
  open. The user cannot re-download without editing SQLite by hand.
- Detaching media rows on queue removal (`downloads_repo.detach_media_files`) is correct.
  The missing piece is that nothing re-checks `lib_path`/`src_path` afterwards.

### Incident evidence

`media_files` for Cyberpunk S01E01–E03 has `src_path` under
`/home/abhi/Downloads/Daku/Cyberpunk...PSA/`, `lib_path = NULL`, and `download_id = NULL`.
`/home/abhi/Downloads/Daku` is empty. `GET /api/titles/9e4b...` still returns
`availability: partial`, `available_episode_count: 3`, and all three episodes as
`available`.

## Scope

### A single path-existence helper

Add `media_files.usable_path(row) -> Path | None` and `media_files.is_present(row) -> bool`:

- Prefer `lib_path`; fall back to `src_path`.
- Present when the path is a regular file.
- When `size_bytes` is set and `st_size` differs, treat it as missing (truncated download).
- Catch `OSError` and return false; never raise from an availability read.

### Availability

- `availability_map`, `availability_for`, `available_episode_ids` and the title detail use
  only present rows. Fetch all relevant `media_files` rows for the requested title ids in one
  query, then stat them in the service/repository layer; do not run one query per title.
- A title with rows but no present file derives `none`. A series with 3 of 10 episodes
  present stays `partial`; with 0 present it is `none`.
- Expose the truth per file rather than inventing a new title state:
  - `media_files` payloads get `"present": true|false`.
  - Episode payloads get `"present": bool` and keep `status` in the existing
    `missing|queued|downloading|available` vocabulary; a missing file maps to `missing`.
- Add `missing_file_count` to the title detail so the UI can show "3 files missing" without
  scanning in the browser.

### Re-download

- `_requested_files_already_present` ignores rows where `is_present(row)` is false.
  A request for a deleted episode is a normal new download (202/201), not a 409 duplicate.
- A finished download row with no present files may also be retried: `_restart_finished`
  must not assume the files survived.
- `check_disk_space` still runs as today.

### Repair action (small)

- `POST /api/titles/{title_id}/media/reconcile` deletes only the `media_files` rows that are
  not present and returns `{removed: N, kept: M}`. It never touches disk and never deletes a
  title or episode. This is the explicit way to clear stale rows after a manual deletion; it
  is not called automatically on every page load.
- The title page shows a compact "N files missing · Clean up" action next to the library
  state when `missing_file_count > 0`.

### Doctor (optional but cheap)

- `/api/system/doctor` adds a `library` check: media rows with no present path, capped at a
  count, status `warn` when non-zero. Read-only.

## Out of scope

- Implementing the organiser (T-013).
- Watching library folders for changes; availability is computed when read.
- Background reconciliation jobs, notifications, or deleting titles automatically.
- A new `availability = "missing"` title state; `none|partial|complete` stays.

## Acceptance criteria

- [x] Deleting a file under `lib_path` or `src_path` makes its episode `missing` and removes
      it from `available_episode_ids` on the next read; no restart is needed.
- [x] The Cyberpunk title reports `available_episode_count: 0` and either `partial` (episodes
      exist but none present → `none`) or `none` until the files are re-downloaded, and the
      detail shows `missing_file_count: 3`.
- [x] Requesting a download for an episode whose stale media row has no file returns 202/201;
      it is not rejected as `DOWNLOAD_ALREADY_TRACKED`.
- [x] A row with a present file is still `available` and still blocks a duplicate download.
- [x] A row whose `size_bytes` disagrees with the file on disk counts as missing.
- [x] `POST /api/titles/{id}/media/reconcile` removes only missing rows and leaves present
      rows, releases, episodes and the title untouched.
- [x] Availability for 200 titles does not issue more than a constant number of SQL queries
      (batch query + stat loop), and no request hits the network.
- [x] Doctor reports a non-zero missing count as `warn`, never `error`.

## Test plan

- `tests/repositories/test_titles.py` (or the existing library tests):
  - create real files in `tmp_path`, assert `complete`/`partial`;
  - unlink one, assert `partial`;
  - unlink all, assert `none`;
  - `lib_path` present while `src_path` is gone → still available;
  - size mismatch → missing.
- `tests/routers/test_library.py`: detail reports `present`, `missing_file_count`, and
  episode `status: missing`.
- `tests/routers/test_downloads.py`: duplicate check with a missing file → new/requeued
  download; with a present file → 409 unchanged.
- `tests/routers/test_titles.py`: reconcile removes 3 stale rows, keeps 1 present row, and a
  second call is a no-op.
- Regression: the existing `/tmp/...` fixtures in the library tests must create the file
  (or use `tmp_path`) so they exercise the new contract rather than relying on fake paths.

## Implementation notes

- Stat once per row; do not call `is_file()` and then `stat()` again.
- Use `Path.resolve(strict=False)` only for reporting, not for existence checks.
- `available_episode_ids` is used by the release and download paths as well as the UI; keep
  the function name and type, only change its result set.
- A missing `lib_path` with a present `src_path` is still usable: the pre-T-013 library is
  the download folder. T-050 prevents that copy from being deleted.
- Do not silently delete stale rows on read; a GET must stay side-effect free.
