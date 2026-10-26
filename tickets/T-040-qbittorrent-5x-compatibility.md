# T-040 — qBittorrent 5.x add response and start/stop compatibility

**Phase:** 11 · **Depends on:** T-009 · **Estimate:** M

## Goal

A torrent qBittorrent accepts must become an `active` Daku download with live progress, and
Pause/Resume must work, on qBittorrent 5.0 through 5.3. Today a successful add is reported
as a failure with the raw JSON response, so the row shows **Failed**, `0 B / …`, and no
progress.

## Root causes (verified)

1. **Add response shape.** qBittorrent 5.2+ returns
   `{"success_count":1,"failure_count":0,"pending_count":0,"added_torrent_ids":["…"]}`
   from `POST /api/v2/torrents/add`. `QbittorrentEngine.add` only accepts the literal
   `Ok.` and raises `EngineError("QBT_REJECTED", body[:200])` for anything else
   (`src/daku/services/engines/qbittorrent.py:239-244`). The screenshot in the report is
   exactly that body. The torrent is already in qBittorrent, but Daku marks the row
   `failed`, so no progress is ever polled.
2. **Pause/Resume endpoints.** qBittorrent 5.0 (Web API 2.11.0) renamed
   `/torrents/pause|resume` to `/torrents/stop|start`. Daku still calls the old names
   (`qbittorrent.py:305-317`). `_request` only raises for `>=500` and `403`
   (`qbittorrent.py:214-221`), so a `404` is swallowed and the button silently does
   nothing.
3. **Add `paused` parameter.** 5.x reads `stopped` for the add-time state. Sending only
   `paused` can start the torrent immediately. This is the bridge to T-041.
4. **Silent mutation failures.** `_request` returns every other 4xx to the caller; `add`,
   `pause`/`resume`, `remove` and `filePrio` never inspect the status.

## Scope

### Add

- Accept every documented success shape:
  - `200` with body `Ok.` (4.x/5.0/5.1);
  - `200`/`201`/`202` with an empty body;
  - `200`/`202` with JSON containing `success_count`, `failure_count`, `pending_count`,
    `added_torrent_ids`.
- JSON is success when `success_count >= 1` or `pending_count >= 1` with
  `failure_count == 0`. A `202` with `pending_count > 0` is success (metadata still
  pending).
- JSON is failure when `success_count == 0` and (`failure_count > 0` or the body is
  `Fails.`). Raise `QBT_REJECTED` with a human message such as "qBittorrent rejected the
  torrent"; never put the raw JSON in `download.error`.
- If `added_torrent_ids` is present and the expected info-hash is not in it, log a warning
  (do not fail; qBittorrent may normalize the hash).
- Send both `paused` and `stopped` with the same boolean so 4.x, 5.0 and 5.2+ all honour
  the requested state.
- Check the HTTP status: a non-2xx is an error even if the body is `Ok.`.

### Start/stop

- Probe `/api/v2/app/webapiVersion` once per process and cache the parsed version:
  - `>= 2.11.0` → `POST /torrents/start` and `/torrents/stop`;
  - `< 2.11.0` → `POST /torrents/resume` and `/torrents/pause`.
- If the version probe fails, try `start`/`stop` first and fall back to `resume`/`pause`
  on `404` (one retry, cached for the process).
- Raise `EngineError` when both fail.

### Error handling

- `_request` gains a `raise_for_status: bool = True` (default) and raises
  `QBT_REJECTED` (non-transient) for unexpected `4xx`, with the endpoint name and status,
  not the body. `files()` keeps its explicit `404 -> []` behaviour.
- `add`, `start`/`stop` (and the `pause`/`resume` aliases), `remove` and `filePrio` all
  check their responses.

### State mapping

- `stoppedDL`/`stoppedUP` are already in `PAUSED_STATES`; add regression tests so a future
  refactor does not drop them.
- Keep `uploading`/`stalledUP` at `progress >= 1` → `complete`.

## Out of scope

qBittorrent 4.x parity beyond the documented login/add shapes, the file-selection workflow
(T-041), a generic engine-version framework, qBittorrent preferences.

## Acceptance criteria

- [x] The exact JSON body from the report is treated as success: the download row becomes
      `active`, progress updates from `/torrents/info`, and `error` is `None`.
- [x] `Fails.` and a JSON body with `success_count=0` are failures with a short human
      message; the raw JSON never appears in `download.error`.
- [x] A `202` add with `pending_count > 0` is accepted.
- [x] The add form contains both `paused` and `stopped` with the requested value.
- [x] Pause and Resume call `stop`/`start` on Web API `>= 2.11.0` and `pause`/`resume` on
      older versions; both work against a mocked 5.2 server.
- [x] A `404` from a mutation raises `QBT_REJECTED` instead of being ignored.
- [x] `stoppedDL`/`stoppedUP` map to `paused`.
- [x] The doctor still reports the qBittorrent version and `ok` on 5.2.
- [x] Existing 4.x tests keep passing.

## Test plan

- `tests/engines/test_qbt.py`: JSON success (the report payload), JSON failure, `Fails.`,
  empty 200, 202 pending, both add params, start/stop vs resume/pause by version, 4xx
  mutation, 5.x state mapping.
- `tests/worker/`: a queued row plus a JSON add becomes `active` after one tick; the SSE
  payload has progress.
- `web/src/pages/__tests__/downloads.test.tsx`: an active row renders
  `role="progressbar"` and advances when a `download.updated` event arrives; the raw add
  JSON is never rendered as an error.
- Manual against qBittorrent 5.2: start a movie download and watch the bar move.

## Implementation notes

- Compare API versions as integer tuples; `packaging` is not a dependency and should not
  become one for this.
- Cache the version on the engine instance; a qBittorrent restart does not change it.
- Keep the `QBT_LOGIN_FAILED` cooldown untouched.
- `QBT_REJECTED` stays non-transient so the worker marks the row `failed` instead of
  retrying a rejected magnet forever.
- Update `AGENTS.md` §6 with the 5.2 add response and the `start`/`stop` rename.
