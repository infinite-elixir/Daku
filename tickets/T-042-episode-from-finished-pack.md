# T-042 — Download another episode from an already-finished pack

**Phase:** 11 · **Depends on:** T-031, T-039, T-041 · **Estimate:** S

## Problem

After one episode of a season pack finished (Cyberpunk Edgerunners S01E01), asking Daku for
the next episode from the same torrent answered:

> This release is already in your library. Open Downloads to see it.

`downloads` is keyed by `UNIQUE(engine, external_id)` (the info-hash), so the second episode
resolved to the existing `complete` row and `POST /api/downloads` returned
`409 DOWNLOAD_ALREADY_TRACKED` before any file selection was applied. The torrent was already
in qBittorrent with only the first episode at priority 1, so there was nothing to re-add — the
request only needed to reuse the row.

A second bug sat behind the first: `inspect_download` calls
`delete_by_download` before persisting the new media rows. If the row had been reused, that
would have deleted the first episode's `media_files` row and flipped it back to `missing`.

## Scope

- `POST /api/downloads` treats a finished (`complete`/`available`) row as a duplicate only
  when the requested file or episode is already in `media_files` for that title.
  - explicit `file_paths`: compare basenames against `media_files.src_path`.
  - episode request: compare `media_files.episode_id`.
  - otherwise: any media row for the title is a duplicate.
- When it is not available, the same row is reused (`202`): the selection JSON is replaced by
  the new match and the row goes back to `queued`.
- A finished row keeps **every** `media_files` row it has produced. Re-inspection replaces
  only the rows for the files it is inspecting now, so earlier episodes stay available.
- `GET /api/downloads` exposes `downloaded_files` (episode label, file name, size) so the
  Completed section can list every episode the torrent fetched. Rows written before this
  ticket detached the earlier episode; its orphaned media row is recovered when it sits in the
  same folder as a file the download still owns.
- The engine entry is left in place. The worker's `_adopt` path re-applies the file selection
  (stop → `filePrio` → verify → start) instead of removing and re-adding the magnet.
- No schema change, no new download state.

## Out of scope

- Downloading several episodes from one pack in a single action (still T-041).
- Retrying an episode whose media row exists for a different reason; use Retry/Re-inspect.
- Seeding policy for a partially selected pack.

## Acceptance criteria

- [x] A `complete` row whose first episode is available accepts a second episode from the
      same release, returns `202`, keeps one row, and stores the new `selected_files` /
      `file_match_json`.
- [x] The previous episode's `media_files` row stays attached and still reports that episode
      as available.
- [x] Requesting the episode that is already available still returns
      `409 DOWNLOAD_ALREADY_TRACKED` and does not change the stored selection.
- [x] The worker adopts the existing qBittorrent torrent and calls `set_file_selection` with
      the new indices; it does not add the magnet again.
- [x] Re-inspection after the new episode finishes does not delete the earlier episode's row.
- [x] `GET /api/downloads` returns `downloaded_files` with both episodes, and the Completed row
      renders them as chips (with file name/size in the tooltip).
- [x] A row created before this ticket (episode media row detached) still lists the earlier
      episode when both files share the torrent folder.

## Test plan

- `tests/routers/test_downloads.py`:
  `test_a_finished_pack_can_download_the_next_episode`,
  `test_downloading_an_episode_that_is_already_available_is_a_duplicate`,
  `test_downloads_payload_lists_every_episode_the_torrent_downloaded`,
  `test_downloads_payload_recovers_episodes_detached_before_t042`.
- `tests/worker/test_download_selection.py`:
  `test_a_finished_pack_requeued_for_another_episode_is_adopted_and_reselected`.
- `tests/organize/test_selection.py`:
  `test_a_later_episode_does_not_delete_the_earlier_episode_rows`.
- `web/src/components/__tests__/download-row.test.tsx`:
  `lists every episode this torrent has already downloaded`.

## Implementation notes

- The duplicate check runs before `queue_release` so a duplicate never rewrites
  `selected_files_json`.
- `_restart_finished` is deliberately separate from `_requeue_existing`: the latter removes
  the engine entry, which is correct for `failed`/`cancelled` but would force a re-add here.
- `inspect_download` no longer calls `delete_by_download`; it deletes only rows whose
  `src_path` is in the current inspection set, which keeps re-inspection idempotent without
  dropping older episodes.
- `download_payload` builds `downloaded_files` from the attached rows, plus orphaned rows of
  the same title in the same parent folder (pre-T-042 rows). Deduped by `src_path`.
