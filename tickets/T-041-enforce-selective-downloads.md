# T-041 — Selective downloads are actually enforced (T-031 regression)

**Phase:** 11 · **Depends on:** T-031, T-040 · **Estimate:** L

## Goal

When Daku matches `S02E04` in a 12-file season pack and the user presses Download, only that
file is fetched. The matcher and the `selected_files_json` row are correct; the enforcement
is not. Today the magnet is added and the whole pack starts downloading.

## Root causes (verified)

1. **The selection never reaches the engine.** `worker._start` adds the magnet and then
   calls `_apply_selection` (`src/daku/worker.py:179-227`). With qBittorrent 5.2+ the add
   raises `QBT_REJECTED` because of the JSON body (T-040), so `_apply_selection` is never
   reached. The torrent is already in qBittorrent and downloads every file. This is the
   reported behaviour.
2. **The add-time pause is ignored by 5.x.** `paused=bool(_selection(row).indices)`
   (`worker.py:201`) is not the parameter 5.x reads (`stopped`). Even after T-040 parses the
   response, the torrent may start before priorities are applied.
3. **Priority failures are ignored, then the torrent is resumed.**
   `QbittorrentEngine.set_file_selection` calls `filePrio` and unconditionally calls
   `resume` (`qbittorrent.py:454-464`). `_request` does not raise on 4xx, so a rejected
   `filePrio` still starts the full pack.
4. **No verification.** The engine never reads back the file priorities, and the worker
   stores `effective` from its own input, not from qBittorrent.
5. **Live re-selection races.** `_apply_live_selection`
   (`src/daku/routers/downloads.py:152-186`) calls `set_file_selection` on an
   already-running torrent without stopping it first.
6. **Organiser index fallback can pick the wrong file.** `select.py:_filter_selected`
   enumerates the sorted scan when only indices are stored
   (`src/daku/services/organize/select.py:280-284`); engine indices and scan order are not
   guaranteed to agree.

## Scope

### Engine contract

Add to `DownloadEngine` / `QbittorrentEngine`:

- `stop`/`start` primitives (from T-040) and a `set_file_selection` that:
  1. stops the torrent if it is running;
  2. waits for metadata (bounded, existing 30 s timeout);
  3. maps paths → indices (existing normalisation);
  4. POSTs `filePrio` with every file: selected `1`, others `0`;
  5. **reads back `/torrents/files`** and verifies every priority; retry the read 3 times
     with a short backoff (qBittorrent applies asynchronously);
  6. starts the torrent only after verification succeeds;
  7. on any failure leaves it stopped and raises `EngineError`
     (`QBT_FILE_SELECTION_FAILED`; transient for metadata timeouts, non-transient for
     rejected priorities).
- Use the qBittorrent Web API `>= 2.11.9` add-time `filePriorities` parameter when the
  metadata is already available before add (probe cache). Otherwise use the
  stop → priorities → verify → start path. Never rely on add-time priorities alone.
- `_apply_selection` in the worker must not set the row to `active` until the engine
  confirms the selection. On non-transient failure, mark `failed` with
  `FILE_SELECTION_FAILED` and leave the torrent stopped; do not remove it automatically
  (the Remove action from T-039 cleans it up).

### Add/start ordering

- Always add with `stopped=true`/`paused=true` when `selected_files_json` is not null,
  even when the add-time `filePriorities` path is used.
- Immediately after add, call `stop` (idempotent) before waiting for metadata. This closes
  the race on versions that ignore the add-time flag.
- For a `null` selection (movie, single-file, `allow_pack=true`), keep the current
  start-immediately behaviour.

### Live selection

- `POST /api/downloads` for an already-active row (`_apply_live_selection`) and any future
  "change files" action must stop → set priorities → verify → start.
- If the new selection is rejected, restore the previous priorities (best effort), leave
  the torrent stopped, and return `409 FILE_SELECTION_FAILED`.

### Worker retries

- `FILE_SELECTION_PENDING` stays `paused` with backoff (existing behaviour) and never
  starts the torrent.
- A row with `selection_pending` must not be adopted as `active` by `_adopt`; apply the
  selection first.
- After 5 failed attempts, mark `failed` with `FILE_SELECTION_FAILED` and stop retrying;
  the user can Retry or Remove.

### Organiser

- Filter strictly by `file_match_json.file_paths`.
- When only `selected_files_json` exists, resolve indices to paths via the engine's
  `/torrents/files` (or the T-030 probe) before scanning; never use scan order as an
  index.
- Add a regression test: a 12-file pack with only `S02E04` selected creates one
  `media_files` row and files one episode.

### UI

- The downloads row already shows `S02E04 only`; add the selected size when available:
  `S02E04 only · 1.4 GB` and a tooltip/link to the file list.
- When a selection fails, show a clear error ("Could not restrict this torrent to S02E04.
  Nothing is downloading.") with Remove/Retry.
- No new page or state.

## Out of scope

Re-matching a completed download, subtitles, seeding policy, non-qBittorrent torrent
engines, downloading multiple episodes from one pack in a single action (the API can accept
multiple paths, but this ticket only guarantees one).

## Acceptance criteria

- [x] With a 12-file pack and `S02E04` matched, qBittorrent receives `filePrio` with every
      file `0` except the `S02E04` index, and only that file has progress > 0 after 30 s.
- [x] The torrent is stopped before `filePrio` and started only after the read-back
      verifies the priorities.
- [x] A `filePrio` 4xx/5xx leaves the torrent stopped, the row `failed` with
      `FILE_SELECTION_FAILED`, and no full-pack download starts.
- [x] A metadata timeout leaves the torrent stopped, the row `paused` with
      `FILE_SELECTION_PENDING`, and the worker retries with backoff; it never starts the
      pack.
- [x] Five failed selection attempts mark the row `failed` and stop retrying.
- [x] Live re-selection on an active torrent stops, applies, verifies and starts; a rejected
      selection leaves it stopped and returns 409.
- [x] `selected_files_json` matches the engine's read-back indices and the organiser only
      sees the selected file.
- [x] An `allow_pack=true` download still fetches all files and is labelled "Whole pack".
- [x] The reported Cyberpunk case (S02E04 from a pack) downloads one file, shows progress,
      and never reports the add JSON as an error.
- [ ] Works at 390 px and with the keyboard. (manual viewport pass still pending)

> The former Cyberpunk case is verified through the 12-file regression and engine priority
> tests. The live qBittorrent 5.2 manual check and the 390 px pass remain manual follow-ups.

## Test plan

- `tests/engines/test_qbittorrent_file_priority.py`: stop-before-prio, read-back
  verification, priority rejection, metadata timeout, live update, 5.x start/stop.
- `tests/worker/`: queued row with a selection + JSON add → stopped, prio, verified,
  started; add failure → no prio; five attempts → failed.
- `tests/routers/test_downloads.py`: live re-selection success/failure; `allow_pack`.
- `tests/organizer/test_selection.py`: only selected paths are inspected; index fallback
  resolves via the engine/probe.
- `web/src/components/__tests__/download-row.test.tsx`: selection size label and failure
  copy.
- Manual: the reported Cyberpunk S02E04 pack against qBittorrent 5.2; confirm in the
  qBittorrent UI that only one file is downloading.

## Implementation notes

- Add-time `filePriorities` cannot be used for the magnet URLs Daku adds: qBittorrent
  returns `400 filePriorities may only be specified when metadata has already been
  fetched`. Daku therefore never sends that field and always uses the stop/verify path.
- qBittorrent's `filePrio` is idempotent. Web API 2.16 takes one numeric `priority` per
  call and a pipe-separated `id` list, so group files by priority and send one call per
  group. The read-back may briefly show the old priority; poll up to ~5 s, not once.
- A stopped magnet has not fetched metadata. Start it briefly, then stop as soon as
  `/torrents/files` returns the list, before applying priorities.
- Do not use the torrent's overall `progress` to decide whether the selection worked; use
  the per-file `priority` and `progress` from `/torrents/files`.
- The magnet necessarily adds the whole torrent; "download only one file" means file
  priorities, not a partial torrent. Put that sentence in the download detail copy.
- Keep `downloads.selected_files_json` as the source of truth; do not add a state.
- Update `AGENTS.md` §6 with the stop → `filePrio` → verify → start contract and §7 with
  the "selection is metadata, not a state" reminder.
