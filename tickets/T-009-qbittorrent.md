# T-009 — qBittorrent engine and download queue

**Phase:** 2 · **Depends on:** T-007 · **Estimate:** L

## Goal

Send a chosen release to qBittorrent and track it to completion without the user touching
qBittorrent's UI.

## Scope

- `services/engines/base.py`: `DownloadEngine` protocol
  (`add`, `poll`, `pause`, `resume`, `remove`, `health`).
- `services/engines/qbittorrent.py`: login/cookie session, add magnet, batch poll, controls.
- `services/queue.py`: create `downloads` rows, dedupe, enqueue.
- `worker.py`: 2 s loop, poll active rows, apply the state machine, publish events.
- `routers/downloads.py`: `POST /api/downloads {release_id}`, `GET /api/downloads`,
  `POST /api/downloads/{id}/pause|resume|cancel|retry`.
- Doctor check for qBittorrent reachability and version.

## Out of scope

aria2 (T-010), progress UI (T-011), file organisation (T-013).

## Acceptance criteria

- [x] Starting a release adds it to qBittorrent with `category=daku`,
      `tags=<title_id>`, absolute `savepath`, `sequentialDownload=false`,
      `firstLastPiecePrio=true`.
- [x] A duplicate start of an already-tracked info-hash returns the existing download with
      `409` semantics, not a second qBittorrent entry.
- [x] Polling updates `progress`, `downloaded_bytes`, `total_bytes`, `speed_bps`,
      `eta_seconds`, and `status` at least every 2 s while active.
- [x] qBittorrent states map to Daku states: `metaDL/downloading/stalledDL/forcedDL` →
      `active`; `queuedDL` → `queued`; `pausedDL` → `paused`; `error/missingFiles` → `failed`;
      `uploading/stalledUP/forcedUP` → `complete`.
- [x] A upload/seed state is not treated as incomplete if `progress >= 1.0`.
- [x] Pause, resume, cancel work and are idempotent.
- [x] If qBittorrent restarts or the cookie expires, Daku re-authenticates once and recovers.
- [x] If qBittorrent is unreachable, downloads stay `active` with a `last_error` and the
      doctor reports `degraded`; no crash and no data loss.
- [x] `GET /api/downloads` returns the last 50 with title and episode context joined.

## Implementation notes

Login: `POST /api/v2/auth/login` form-encoded, expect body `Ok.` and an `SID` cookie.
Send `Referer` equal to the qBittorrent base URL. Use one `httpx.AsyncClient` with a cookie
jar per engine instance. Re-login on `403`.

Add magnet: multipart form with `urls=<magnet>`. The response is `Ok.` / `Fails.`, not JSON.
To obtain the hash of the added torrent, parse the `btih` from the magnet; do not search the
torrent list.

Batch poll: `GET /api/v2/torrents/info?hashes=<h1>|<h2>|...` to avoid N requests. Map by
`hash` (lowercase).

ETA is seconds from qBittorrent; clamp `8640000` (∞) to `null`.

Cancel: `POST /api/v2/torrents/delete?hashes=<h>&deleteFiles=true` only when explicitly
requested; default `deleteFiles=false`.

Do not store the magnet in logs. The download row keeps only `release_id` and `external_id`.

## Test plan

- `tests/engines/test_qbt.py` with `respx`: login, add, poll mapping for every state,
  re-auth on 403, add failure, unreachable timeout.
- `tests/queue/test_dedupe.py`: same info-hash twice → one row.
- `tests/worker/test_transitions.py`: fake clock, active → complete, error → failed, retry.

## Completion notes

- `services/engines/base.py`, `qbittorrent.py`, `worker.py`, `services/queue.py` and
  `routers/downloads.py` implement the ticket.
- A duplicate `POST /api/downloads {release_id}` now returns **409** with the existing
  row; the T-008 test was updated to expect that.
- Pressing Download again for a `failed` or `cancelled` row re-queues that same row
  (`202`, status `queued`, progress reset) instead of returning a bare 409. Duplicate
  `active`/`queued` rows still return 409 and now carry `code=DOWNLOAD_ALREADY_TRACKED`
  plus a human-readable `message` for the UI.
- The worker starts from the FastAPI lifespan unless `worker_enabled=false`
  (`DAKU_WORKER_ENABLED`). Tests disable it and drive `Worker.tick()` directly.
- Unreachable qBittorrent writes the message to `downloads.error` and leaves the row
  `active`; the doctor reports `degraded` via a live version probe.
- A failed login now starts a cooldown (30 s for bad credentials, 120 s for a banned IP)
  instead of retrying on every worker tick, which previously kept the qBittorrent ban alive.
- Status changes are also recorded as `download.status` events for T-011 to consume.
