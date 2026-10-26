# T-011 — Live progress: SSE and downloads UI

**Phase:** 2 · **Depends on:** T-009, T-010 · **Estimate:** M

## Goal

The downloads screen updates in real time without polling the server from the browser, and
survives a dropped connection.

## Scope

- `events.py`: in-process `asyncio` pub/sub, bounded per-subscriber queues, drop-oldest on
  overflow, event ids for `Last-Event-ID` replay of the last 100 events.
- `GET /api/events`: `text/event-stream`, 15 s heartbeat, `retry: 3000`.
- Event types: `download.created`, `download.updated`, `download.removed`,
  `title.updated`, `organize.completed`, `system.health`, `heartbeat`.
- UI `/downloads`: active list with progress bar, speed, ETA, size, pause/resume/cancel;
  a completed section; per-item error with retry.
- Client reconnect with `EventSource` and a 3 s polling fallback after two failures.

## Out of scope

Notifications, browser push, websockets.

## Acceptance criteria

- [x] Connecting to `/api/events` yields events within 1 s of a state change.
- [x] The stream sends a heartbeat at least every 15 s and never buffers behind a proxy
      (`Cache-Control: no-cache`, `X-Accel-Buffering: no`).
- [x] A client that disconnects and reconnects with `Last-Event-ID` receives missed events
      (up to 100) or a `resync` event that forces a refetch.
- [x] A slow client cannot grow memory without bound: queue is capped and drops oldest.
- [x] The downloads page updates progress at least 1×/s and does not re-render the whole list
      on every tick (row-level subscription or memoised rows).
- [x] Pause/resume/cancel from the UI reflect within 500 ms optimistically and correct
      themselves on the next event.
- [x] With JavaScript's EventSource blocked, the page still works via polling.
- [x] No event payload contains a magnet, token or absolute host path.

## Implementation notes

Keep one global `EventBus` with `subscribe() -> AsyncIterator[Event]`. The worker publishes;
routers do not. Each SSE connection gets its own `asyncio.Queue(maxsize=64)`.

Event ids are monotonic integers. Keep a ring buffer of the last 100 events with their ids;
on `Last-Event-ID` older than the buffer, emit `event: resync` and let the client refetch.

Progress events should be throttled to 1/s per download; state changes are immediate.

UI: use `useSyncExternalStore` or a small `useEventSource` hook that writes into the TanStack
Query cache with `queryClient.setQueryData(["downloads"], ...)`. Do not duplicate server state
in a separate store.

## Test plan

- `tests/test_events.py`: publish/subscribe, overflow drop, replay by id, resync.
- `tests/routers/test_sse.py`: headers, heartbeat, disconnect cleanup.
- `web/src/hooks/__tests__/useEventSource.test.ts`: reconnect, fallback, cache update.

## Completion notes

- `events.py` hosts one process-wide `EventBus`: monotonic ids, a 100-event ring, and a
  separate bounded queue (64) per connection. Overflow drops the oldest event so a slow
  browser always converges on the newest state.
- `GET /api/events` sends `retry: 3000`, a 15 s heartbeat without an id (so it never enters
  the replay ring) and the `no-cache` / `X-Accel-Buffering: no` headers. `Last-Event-ID`
  replays the ring or emits `event: resync` when the client is too far behind.
- The worker is the only publisher of progress: state changes are immediate, progress-only
  updates are throttled to 1/s per download. Payloads are sanitised on the way out, which
  strips magnets, tokens and absolute paths (the REST payload still includes `save_path`).
- `useEventSource` merges each event into the TanStack Query `["downloads"]` cache by id and
  keeps every other row's object identity, so memoised `DownloadRow`s do not re-render.
  After two `EventSource` failures (or when `EventSource` is absent) it polls every 3 s.
- `/downloads` groups active, completed and failed rows and applies pause/resume/cancel/retry
  optimistically, then reconciles with the API response and the next SSE event.
- Deferred: `organize.completed` / `system.health` publishers belong to T-013/T-020; the bus
  and client already accept those event names.
