# T-030 — Magnet metadata inspection, file list and copy link

**Phase:** 8 · **Depends on:** T-007, T-008 · **Estimate:** L

## Goal

A magnet title is not enough to judge a release. Season packs, multi-film collections and
individual-episode torrents all look alike in the list today. Add a collapsible **Files** view
that fetches torrent metadata for a magnet and shows its real file list, sizes and total size
before saving or downloading it. Also add **Copy magnet** wherever a magnet is shown.

This is a **metadata probe only**. It must never download payload bytes and it must never become
a second download engine.

> Decision (2026-10-25): use a standalone libtorrent session for probing, not a paused
> qBittorrent torrent. This keeps the feature usable before T-009 and avoids polluting the
> download client with probe torrents.

## Scope

### Dependency and safety

- Add `libtorrent` as an **optional** dependency (`uv add libtorrent` or an extra
  `daku[probe]`), imported lazily. On import failure the API returns
  `503 PROBE_UNAVAILABLE` and the UI shows `File list needs the libtorrent extra`.
- The probe session:

  - is created once per process, lazily, in a worker thread;
  - has no incoming listener (`listen_interfaces` on `127.0.0.1:0` or empty), DHT enabled,
    LSD/UPnP/NAT-PMP disabled;
  - adds the magnet with `upload_mode` (metadata only, no payload) and a temporary
    `save_path` under `data_dir/probes/<hash>`;
  - never sets a media save path, never creates a download row, never touches qBittorrent;
  - removes the torrent and deletes the probe directory on success, timeout, error and
    shutdown;
  - asserts `total_done == 0`; if any payload arrives, abort and mark the probe failed.

- Concurrency: **one active probe**, a FIFO queue of at most 10, a 30 s deadline per probe and
  a per-info-hash lock so two UI clicks do not duplicate work.
- All libtorrent calls run via `asyncio.to_thread`; no blocking call on the event loop.
- Log `info_hash[:12]` only; never log the magnet, trackers or file paths under `data_dir`.

### Probe service

`services/torrents/probe.py`:

```python
@dataclass
class TorrentFile:
    index: int
    path: str
    size_bytes: int


@dataclass
class TorrentMetadata:
    info_hash: str
    name: str
    total_size_bytes: int
    piece_length: int
    private: bool
    files: list[TorrentFile]
    fetched_at: str
```

- `probe(magnet) -> TorrentMetadata` raises `ProbeUnavailable`, `ProbeTimeout`,
  `InvalidMagnet`.
- Success cache: 24 h keyed by lowercased info hash, LRU 200. Failure cache: 5 min. A cached
  result is returned without a network round trip and marked `cached: true`.
- Validate the magnet with the existing magnet parser: exactly one `xt=urn:btih:` (40 hex or
  32 base32) is required; `dn` is optional; unknown parameters are ignored.
- Trackers come from the magnet. If the magnet has none, add the same public tracker list used
  when building Torrentio magnets. DHT handles the rest.
- File paths are sanitised for **display only** (strip control characters, NUL, `/` is allowed
  because these are torrent paths). Never use a probed path as a filesystem path.

### API

- `POST /api/magnets/inspect` body `{magnet: string}` → `TorrentMetadata` or a stable error:
  - `400 INVALID_MAGNET`, `503 PROBE_UNAVAILABLE`, `504 PROBE_TIMEOUT`,
    `429 PROBE_BUSY` (queue full).
- `GET /api/releases/{release_id}/files` → probe the saved/stored release magnet. Works for a
  release that has never been downloaded. Returns `{...metadata, cached, source: "probe"}`.
- Neither endpoint writes to the DB. The released response is read-only and safe to cache in the
  client.
- The doctor gains `magnet_probe: ok|unavailable|disabled`, plus queue depth and last failure
  age; no probe is started by the doctor.

### UI

- **Files** button on every release row (find and saved tabs). It expands **inline beneath that
  row** (not a modal, not a new page) and shows:
  - a loading row with a 30 s cap and a **Cancel**;
  - total size, file count, name;
  - a table: path, size, index; long paths wrap or truncate with a tooltip;
  - a short line `Metadata only — nothing was downloaded`;
  - `Retry` on failure, with the stable code in `text-micro`.
- On a phone, the table becomes a stacked list; no horizontal scroll at 390 px.
- The paste-a-magnet form gets an **Inspect** action after a successful parse, so the user can
  see the file list before saving.
- **Copy magnet** button next to every magnet-bearing row and in the files panel:
  - use `navigator.clipboard.writeText` with a `document.execCommand("copy")` textarea fallback;
  - announce success via the T-025 toast if present, otherwise an inline `Copied` that clears
    after 2 s;
  - `aria-label="Copy magnet link"`; disabled with a hint if the magnet is missing;
  - never render the full magnet in the DOM title/aria-label (it can be long and contains
    trackers); show `magnet:?xt=urn:btih:abcd…`.
- Do not log or send magnets to analytics; the existing "never log magnets" rule applies.

## Out of scope

Choosing files to download (T-031), showing piece maps, trackers, peers or a live swarm,
torrent health checks, fetching `.torrent` files, probing every row automatically, and
replacing qBittorrent as the torrent engine. Private-tracker credentials.

## Acceptance criteria

- [x] Clicking **Files** on a public magnet shows the real file list and total size without
      creating a download or adding a qBittorrent torrent.
- [x] A season pack lists every episode file with its size; a single-file torrent shows one row.
- [x] Probing writes no payload bytes; the probe directory is empty and removed afterwards.
- [x] A second click within 24 h returns the cached result instantly and makes no network
      request.
- [x] A dead magnet times out with `504 PROBE_TIMEOUT`, leaves no torrent in the session, and
      the UI offers Retry.
- [x] A malformed magnet returns `400 INVALID_MAGNET` before any libtorrent call.
- [x] When libtorrent is not installed, the release list still works and Files explains the
      missing optional dependency; the doctor reports `unavailable`.
- [x] The queue rejects the 11th concurrent request with `429 PROBE_BUSY` rather than blocking
      the event loop.
- [x] **Copy magnet** works in Chromium and Firefox, falls back when Clipboard API is denied,
      and announces success.
- [x] No magnet or tracker string appears in logs, and no full magnet is used as a DOM label.
- [x] `AGENTS.md` records libtorrent as metadata-only, not a torrent engine.

## Test plan

- `tests/torrents/test_probe.py`: mocked libtorrent session — metadata received, timeout,
  payload guard, removal on error, queue depth, cache hit, LRU eviction, invalid magnet.
- `tests/routers/test_magnet_inspect.py`: success, 400/503/504/429 shapes, release files path,
  no DB writes.
- `tests/services/test_doctor.py`: probe status without starting a probe.
- `web/src/components/__tests__/magnet-files.test.tsx`: inline expansion, loading, timeout,
  retry, stacked mobile layout.
- `web/src/components/__tests__/copy-magnet.test.tsx`: clipboard success, fallback, aria label,
  missing magnet.
- Manual (not CI): one real public magnet and one dead magnet on a host with libtorrent.

## Implementation notes

- Keep the libtorrent import inside the adapter; the rest of the app must start without it.
- Use `torrent_handle.status()`/`torrent_file()` and the `metadata_received_alert`; do not poll
  the swarm for peer counts.
- The probe is read-only with respect to Daku's DB and media; the only disk writes are under
  `data_dir/probes` and are deleted.
- Reuse `services/releases/magnet.py` for parsing; do not add a second magnet parser.
- The inline expansion state belongs to the release row and must not close when a sibling row
  is opened; only one panel per row.
- Update `AGENTS.md` §1, §4 (optional dependency) and §6 (probe contract) when this lands.

## Implementation notes (2026-10-26)

- Implemented `services/torrents/probe.py` with a lazy `LibtorrentBackend` (optional
  `daku[probe]` extra), `ProbeService` queue/cache and read-only API routes.
- The 11th concurrent request is rejected counting the active probe plus the queue, matching
  the acceptance criterion.
- Automated coverage lives in `tests/torrents/test_probe.py`,
  `tests/routers/test_magnet_inspect.py`, `tests/test_doctor.py`,
  `web/src/components/__tests__/magnet-files.test.tsx` and
  `web/src/components/__tests__/copy-magnet.test.tsx`.
- Manual checks against one live public magnet and one dead magnet on a host with the
  libtorrent extra installed were **not** run in this environment; the adapter is covered by a
  mocked libtorrent session.
