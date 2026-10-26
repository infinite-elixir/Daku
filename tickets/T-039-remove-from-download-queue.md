# T-039 — Remove a download from the queue

**Phase:** 11 · **Depends on:** T-011, T-031 · **Estimate:** M

## Goal

The Downloads page can pause, resume, cancel and retry, but it cannot remove a row. `Cancel`
only moves a row to `cancelled`; the row stays in "Needs attention" forever, and a failed
qBittorrent add leaves a dead entry that can only be retried. Add an explicit **Remove**
action that deletes the Daku download row (and its engine entry) without touching the
library.

## Root causes (verified)

- `src/daku/routers/downloads.py:429` — `cancel_download` sets `status="cancelled"` and never
  deletes the row.
- `src/daku/repositories/downloads.py:103` — `delete_download` exists but no route uses it.
- `web/src/components/DownloadRow.tsx:120` — only Pause/Resume/Cancel/Retry buttons are
  rendered.
- `web/src/hooks/useEventSource.ts:151` — the client already listens for
  `download.removed`, but the backend never publishes it.
- `media_files.download_id` is `ON DELETE CASCADE` (`src/daku/models.py:195`), so a naive
  row delete would silently remove library availability for a completed download.

## Scope

### API

`DELETE /api/downloads/{download_id}?delete_files=false` → `204 No Content`.

- 404 `DOWNLOAD_NOT_FOUND` when the row is already gone.
- 409 `DOWNLOAD_BUSY` when `status == "organizing"` (the worker owns the row).
- Allowed for `queued`, `active`, `paused`, `complete`, `failed`, `cancelled`, `available`.
- Call the engine's `remove(external_id, delete_files=delete_files)` first:
  - qBittorrent receives `deleteFiles` from the flag.
  - aria2 removes the GID/result; `delete_files` is ignored in v1 because aria2 has no
    per-transfer delete and the file may already be a library source. Say so in the
    docstring.
  - Treat "torrent/GID is already gone" as success. Any other engine error aborts the
    delete with `502 ENGINE_UNAVAILABLE`/the engine code, and the row stays so the user can
    retry.
- **Never** delete an organised library file. `delete_files` only reaches the engine's
  download directory; every `media_files.lib_path` and every path under
  `movies_dir`/`tv_dir`/`anime_dir` is out of bounds.
- Detach media files before deleting the row:

  ```sql
  UPDATE media_files SET download_id = NULL WHERE download_id = :id
  ```

  then `downloads_repo.delete_download`. The library entry, its `lib_path`, and its
  availability survive.
- Publish `download.removed` with `{"id": download_id}` after the commit.
- Add a repo helper `detach_media_files(session, download_id)`; do not call
  `delete_download` from anywhere else without it.

### UI

- Every row gets a **Remove** button. Wide rows show a trash icon + "Remove"; narrow rows
  show the icon with `aria-label="Remove download"`.
- Clicking opens a small accessible confirm dialog (same pattern as
  `MetadataFixerDialog`: `role="dialog"`, `aria-modal`, focus trap, Escape/Cancel):
  - Title: "Remove download?"
  - Body: "This removes the queue entry. Files already in your library stay where they
    are."
  - Checkbox: "Also delete the downloaded files" (default off), only for torrent rows.
    Copy explains that this deletes qBittorrent's copy, not the library file.
  - Buttons: Cancel / Remove (danger).
- Optimistically remove the row from the TanStack cache; restore it on error and show the
  API message.
- `download.removed` from SSE removes the row from the cache (already wired).
- Keep **Cancel**: Cancel stops the transfer but keeps the row; Remove stops it and deletes
  the row. The dialog copy is the only place that needs to explain the difference.

### API client

`removeDownload(id, { deleteFiles = false }): Promise<void>` using
`DELETE /api/downloads/{id}?delete_files=...`.

## Out of scope

Bulk "clear all", undo, deleting library titles, deleting files from the library, changing
the download state machine, a general-purpose confirm-dialog framework.

## Acceptance criteria

- [x] `DELETE /api/downloads/{id}` removes the row and returns 204 for every
      non-`organizing` state.
- [x] The engine `remove` is called with the row's external id and the requested
      `delete_files` flag; an already-missing engine entry does not fail the delete.
- [x] Removing an `available` download leaves its `media_files` rows and the library
      availability intact (`download_id` is NULL, `lib_path` unchanged).
- [x] Removing an `active` torrent stops/removes it in qBittorrent; no further progress
      events are emitted for the deleted row.
- [x] `download.removed` is published with the id and the row disappears without a refetch.
- [x] `organizing` returns 409 `DOWNLOAD_BUSY` and the row stays.
- [x] The UI shows Remove on every row, confirms before deleting, defaults the file-delete
      checkbox off, and restores the row if the API fails.
- [ ] At 390 px the dialog fits, the buttons are reachable, and long file names wrap.
      (responsive layout implemented; manual viewport pass still pending)
- [x] `delete_files=true` never removes a `lib_path` or anything under a Jellyfin library
      root.

## Test plan

- `tests/routers/test_downloads.py`: delete each status, 404, `organizing` 409, engine
  called, `delete_files` forwarded, `media_files` detached, `download.removed` event.
- `tests/releases/test_download_later.py`: delete a cancelled row, then download the same
  release again (the unique `(engine, external_id)` row is gone and a new row is created).
- `web/src/components/__tests__/download-row.test.tsx`: Remove button, dialog copy,
  checkbox default, optimistic removal, error restore, keyboard/Escape.
- Manual: remove an available movie and confirm the title still shows Available and Play.

## Implementation notes

- Return 204 rather than the deleted payload; the client already handles 204.
- Do not emit `download.status`/`download.updated` for a deleted row.
- Use `session.execute(update(MediaFile).where(...))` and commit before
  `session.delete(row)`.
- If the engine returns a non-transient error, keep the row and surface the error. Only
  tolerate "not found"/"already removed".
- The `downloads` list is capped at 50 rows; removing the oldest entries is what makes
  room for new ones.
- Update `AGENTS.md` §5 with the detach invariant: deleting a download row never deletes a
  library file and never cascades `media_files`.
