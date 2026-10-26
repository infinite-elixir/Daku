# T-014 — Jellyfin: scan, resolve, play

**Phase:** 3 · **Depends on:** T-013 · **Estimate:** M

## Goal

After a file is organised, Jellyfin picks it up, Daku knows the Jellyfin item id, and the
"Play" button opens the right thing on the phone.

## Scope

- `services/jellyfin.py`: API-key client, connection test, library scan, item lookup by path,
  direct-play URL builder, deep link builder.
- Worker: after `organize.completed`, trigger a scan (debounced to 1 per 60 s), then poll
  `/Items` for up to 2 min to resolve the new path to an item id.
- `POST /api/jellyfin/refresh`, `GET /api/jellyfin/status`.
- `titles.jellyfin_item_id` + `media_files.jellyfin_item_id` (migration v4; v3 was used by
  T-006 for `folder_name` and title identity indexes).
- UI: Play button on title and episode rows; opens
  `<jellyfin_url>/web/index.html#!/details?id=<itemId>` in a new tab.
- Doctor check: reachability, API key validity, library roots exist and are configured.

## Out of scope

Embedding Jellyfin in an iframe, transcoding controls, watch-progress sync.

## Acceptance criteria

- [ ] `GET /api/jellyfin/status` returns server name, version and whether the API key is
      accepted.
- [ ] A successful organise triggers `POST /Library/Refresh` with
      `Authorization: MediaBrowser Token=<key>`.
- [ ] Scans are debounced: 10 files organised in a minute produce one scan, not 10.
- [ ] Daku resolves the organised path to a Jellyfin item id within 2 minutes, retrying with
      backoff; unresolved items are reported, not silently ignored.
- [ ] "Play" opens the correct Jellyfin detail page for the title/episode.
- [ ] A direct-play URL is returned for browser-safe files
      (`/Videos/{id}/stream?static=true&api_key=...`) and is never exposed in logs.
- [ ] A missing or wrong API key produces a `doctor` failure with the exact fix, and organising
      still succeeds (Jellyfin is not a hard dependency for download).
- [ ] No Jellyfin call is made when the integration is disabled.

## Implementation notes

Header: `Authorization: MediaBrowser Token=<api_key>`. `POST /Library/Refresh` requires an
admin key and returns `204`.

Resolve by path: `GET /Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path,ProviderIds&Limit=500`
then compare `os.path.normcase(os.path.normpath(path))`. Cache the path→item map for 60 s.
Jellyfin may need a second scan; poll until found or timeout.

Deep link: `{base}/web/index.html#!/details?id={itemId}`. Direct stream:
`{base}/Videos/{itemId}/stream?static=true&api_key={key}` — only for `.mp4`/`.webm` with
H.264/AAC/VP9/Opus, otherwise omit it and use the deep link only.

Store the item id on the title for movies and on the media file for episodes, so a title with
many episodes does not collapse to one link.

## Test plan

- `tests/test_jellyfin.py` with `respx`: status ok, 401 key, refresh debounce, item resolution
  by path, timeout, disabled mode.
- `tests/worker/test_jellyfin_after_organize.py`: organise → scan → resolve → play URL.
