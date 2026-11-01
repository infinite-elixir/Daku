# T-050 — Removing a download must not delete the only copy of a library file

**Phase:** 14 · **Depends on:** T-039, T-013 · **Estimate:** M

## Goal

A finished download is still the only copy of the library file until T-013 organises it.
`DELETE /api/downloads/{id}?delete_files=true` currently hands that folder to qBittorrent,
which deletes it. The `media_files` rows are then detached, so the title keeps reporting the
episode as available. This ticket makes destructive removal refuse to run when it would
destroy the only copy, and makes the UI say so before the click.

## Root causes (verified 2026-10-30)

- The organiser from T-013 is not implemented in this tree. There is no
  `services/organize/organizer.py`, no writer for `media_files.lib_path`, and no
  `complete → organizing → available` worker stage. Every finished download therefore lives
  only in `downloads_dir` and `lib_path` is always `NULL`.
- `src/daku/routers/downloads.py:611-643` (`remove_download`) forwards the caller's
  `delete_files` flag straight to `engine.remove(..., delete_files=True)` with no check of
  what is on disk.
- `src/daku/services/engines/qbittorrent.py:444-462` sends
  `deleteFiles=true` to `/api/v2/torrents/delete`, which removes the torrent's complete
  folder — for a season-pack row that is every episode Daku ever selected from it.
- `downloads_repo.detach_media_files` (`src/daku/repositories/downloads.py:103`) keeps the
  `media_files` rows with `download_id = NULL`, so availability is preserved even though the
  file is gone. That is correct for an organised file and wrong for the current state.
- `web/src/components/RemoveDownloadDialog.tsx:78` tells the user "Files already in your
  library stay where they are". That sentence is false until T-013 ships.

### Incident evidence

qBittorrent log, 2026-10-29 19:20:19 local:

```
(N) Torrent removed. Torrent: "Cyberpunk.Edgerunners.S01E01...-PSA.mkv"
(N) Torrent content removed. Torrent: "Cyberpunk.Edgerunners.S01E01...-PSA.mkv"
```

Daku log, 2026-10-29T13:50:19Z (same instant, UTC) shows Daku's own
`POST /api/v2/torrents/delete`. The three `media_files` rows for
`Cyberpunk.Edgerunners.S01E01..E03` are still present with `download_id = NULL`,
`lib_path = NULL`, and their `src_path` points at a folder that no longer exists. The title
still reports three available episodes.

The deletion was not caused by adding *The Devils*; it was a destructive queue removal. The
bug is that the app allowed it and then lied about the result.

## Scope

### Preflight (backend)

Before calling the engine with `delete_files=true`:

1. Enumerate what the engine would delete. For qBittorrent use
   `engine.files(external_id)` and resolve each path under `row.save_path`; for aria2
   `delete_files` is already ignored and needs no preflight.
2. Load every `media_files` row whose `src_path` resolves to one of those paths — both rows
   attached to this download and rows detached by an earlier queue removal.
3. A row counts as safely duplicated only when:
   - `lib_path` is set and exists,
   - it is not inside `settings.downloads_dir`,
   - it is under `movies_dir`, `tv_dir` or `anime_dir`,
   - its size matches `size_bytes` when that value is known.
4. If any file has no safe copy, do **not** call the engine at all. Return
   `409 DOWNLOAD_DELETE_FILES_UNSAFE` with a message naming how many files are still
   Daku's only copy and telling the user to remove the queue entry without deleting files.
5. When every file is safely duplicated, delete as today. Write an `events` row
   (`kind="download.files_deleted"`) with the download id, engine, info-hash prefix (12 chars
   only), file count and reason `"library_copy_verified"`. Never log a magnet, tracker or
   full path.

Keep `DELETE /api/downloads/{id}` with `delete_files=false` unchanged: it must still remove
the row and detach `media_files`.

### Payload and UI

- Add `can_delete_source_files: bool` and `delete_files_reason: str | null` to the download
  payload. True only when every recorded file has a verified library copy; false with
  `reason="no_library_copy"` in the current pre-T-013 state.
- `RemoveDownloadDialog` shows the "Also delete the downloaded files" checkbox only when
  `can_delete_source_files` is true.
- When it is false, replace it with a read-only line: "These files are still your only
  copy — Daku has not organised them into the library yet. Removing the queue entry will
  keep them."
- When the engine reports `DELETE_FILES_UNSAFE`, restore the row and show the API message
  (the existing error path already does this).

### Deferred

- Moving source files to the desktop trash instead of permanent deletion is out of scope.
  `deleteFiles` stays qBittorrent's own behaviour.

## Acceptance criteria

- [x] `delete_files=true` on a download whose only copies are under `downloads_dir` returns
      409 `DOWNLOAD_DELETE_FILES_UNSAFE`; the engine `remove` is never called and the row
      stays.
- [x] The same request succeeds once every recorded file has an existing `lib_path` outside
      `downloads_dir` and inside a configured library root.
- [x] A file with a stale or missing `lib_path` is treated as unsafe, not as a library copy.
- [x] `delete_files=false` still removes the row and detaches `media_files` in every
      non-`organizing` state.
- [x] `can_delete_source_files` is false for the existing Cyberpunk rows (no `lib_path`) and
      the Downloads row no longer offers the delete-files checkbox for them.
- [x] The dialog copy no longer promises that unorganised files stay in a library.
- [x] Destructive deletes produce a `download.files_deleted` event containing no paths,
      magnets or trackers.
- [x] `tests/routers/test_downloads.py` covers safe, unsafe, missing-`lib_path`, and
      `delete_files=false` paths; `web/src/components/__tests__/download-row.test.tsx`
      covers the hidden checkbox and the replacement line.

## Test plan

- `tests/routers/test_downloads.py`:
  - row with a media row, `src_path` under `downloads_dir`, `lib_path=None` +
    `delete_files=true` → 409, no engine call, row still present.
  - same row + a real file at `movies_dir` in `lib_path` → 204, engine called with `True`.
  - `lib_path` points at a missing file → 409.
  - `lib_path` points inside `downloads_dir` → 409.
  - `delete_files=false` with the same rows → 204, `media_files.download_id` is NULL.
- `web/src/components/__tests__/download-row.test.tsx`:
  - `can_delete_source_files=false` → no checkbox, explanatory line, confirm sends `false`.
  - `can_delete_source_files=true` → checkbox appears and sends `true`.

## Implementation notes

- Put the decision in `src/daku/services/queue.py` or a new
  `src/daku/services/safety.py`; the router should not grow a second copy of the rules.
- Resolve paths with `Path.resolve(strict=False)` and compare with
  `Path.is_relative_to` on Linux. Never string-prefix a path.
- If `engine.files()` is unavailable (engine offline), treat the deletion as unsafe rather
  than guessing.
- The preflight is read-only; it must not add, stop or resume a torrent.
- Until T-013 lands, `can_delete_source_files` is expected to be false for every completed
  download. That is the correct behaviour, not a regression.
