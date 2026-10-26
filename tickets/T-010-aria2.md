# T-010 — aria2 engine for direct downloads

**Phase:** 2 · **Depends on:** T-009 · **Estimate:** M

## Goal

A second engine for non-torrent sources: direct HTTP URLs (and, later, the Telegram file
fallback). It shares the queue and state machine with qBittorrent but has its own adapter.

## Scope

- `services/engines/aria2.py`: JSON-RPC client, process supervision, add/poll/controls.
- Process management: locate `aria2c` (config path → PATH; package `aria2`), spawn with
  `--enable-rpc`, `--rpc-secret`, `--dir`, `--continue=true`, `--max-concurrent-downloads=3`,
  `--rpc-listen-port=6800`; restart on crash with backoff.
- `POST /api/downloads {url, filename?}`.
- Doctor check: RPC reachable, version, secret valid.
- Disk-space preflight: refuse if free space < 2 × expected size.

## Out of scope

Torrents through aria2 (forbidden by `AGENTS.md`), the Telegram UI (T-016/T-017).

## Acceptance criteria

- [x] Daku starts aria2c if it is not already running, and adopts it if it is.
- [x] `POST /api/downloads {url}` creates a `downloads` row with `engine="aria2"` and a GID,
      and returns immediately.
- [x] Polling maps aria2 `active/waiting/paused → active|queued|paused`, `complete → complete`,
      `error/removed → failed|cancelled`.
- [x] Byte counts, speed and ETA are populated from `tellStatus`.
- [x] Pause/resume/cancel map to `aria2.pause`, `aria2.unpause`, `aria2.remove`
      (+ `aria2.removeDownloadResult`).
- [x] aria2 is never used for a `magnet:` or `.torrent` URL; the API rejects it with a clear
      error code.
- [x] If aria2c is missing, the doctor says so and direct downloads are disabled without
      breaking torrent downloads.
- [x] A killed aria2c is restarted within 10 s and in-flight GIDs are re-adopted from
      `aria2.tellActive`; GIDs that no longer exist are marked `failed` with a retry option.

## Implementation notes

RPC: `POST /jsonrpc` with
`{"jsonrpc":"2.0","id":"<uuid>","method":"aria2.addUri","params":["token:<secret>",["<url>"],{"dir":"...","out":"...","continue":"true"}]}`.
All values are strings in aria2's API.

`tellStatus(gid, ["gid","status","totalLength","completedLength","downloadSpeed",
"errorMessage","files"])` — `files[0].path` is the output path.

Supervision: write the aria2 session (`--save-session`, `--save-session-interval=30`) so a
restart can resume. Keep the session file inside `downloads_dir/.daku-aria2.session`.

Do not pass URLs on the command line (leaks tokens); always use RPC.

## Test plan

- `tests/engines/test_aria2.py` with a fake RPC server (`respx` or a local aiohttp stub):
  add, poll, controls, error mapping, missing binary.
- `tests/engines/test_supervisor.py`: spawn/restart/backoff; adoption after restart.
- `tests/routers/test_direct_download.py`: URL accepted, magnet rejected, space guard.

## Completion notes

- `services/engines/aria2.py` and `supervisor.py` implement the adapter + process
  supervision. aria2c is adopted when the RPC port already answers and started on demand
  (first direct download, or the worker tick that owns aria2 rows), never at boot.
- Migration **v5** adds `downloads.url` so a direct transfer can be retried after a
  restart without re-pasting the link. The URL is never logged.
- Direct downloads are added synchronously in the router (the RPC call needs to return a
  GID), then the row starts `active`; the worker owns all later transitions.
- After an aria2c crash the worker re-adopts a transfer by matching `files[].uris[].uri`
  against the stored URL (`aria2.tellActive/tellWaiting/tellStopped`). A GID that exists
  nowhere becomes `failed` and can be retried.
- `POST /api/downloads` rejects `magnet:`, `torrent:`, and `*.torrent` URLs with
  `ARIA2_TORRENT_FORBIDDEN` (422) and returns `ARIA2_UNAVAILABLE` (503) when aria2c is
  missing. The disk preflight uses a best-effort `HEAD` Content-Length.
