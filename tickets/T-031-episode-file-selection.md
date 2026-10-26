# T-031 — Episode file matching and selective download

**Phase:** 8 · **Depends on:** T-009, T-012, T-013, T-030 · **Estimate:** L

## Goal

Make season packs and multi-file torrents usable for one episode. When the user asks for
`S02E05` and picks a pack, Daku should identify the matching file, download **only that file**
where the engine supports it, and hand exactly that file to the organiser. If matching is
ambiguous, Daku asks instead of downloading 40 GB and guessing later.

This is the second half of T-030: T-030 shows the files, T-031 lets the user act on them.

## Scope

### Schema (next free `user_version`, append-only)

- `downloads.selected_files_json TEXT NULL` — JSON array of engine file indices, `null` means
  "engine default/all".
- `downloads.file_match_json TEXT NULL` — the matcher result:
  `{episode_id, file_index, confidence, reason}` or `{ambiguous: true, candidates: [...]}`.
- No change to `media_files`; the organiser still creates one row per physical file it sees.

### Matcher (`services/releases/episode_match.py`)

Input: probed file list (T-030), `Episode` row (season, episode, absolute, name), title kind and
optional release filename. Output: a ranked list of `FileMatch(score, file, reason)`.

Rules, in order:

1. Normalise the file path (Unicode NFKC, lowercase, strip brackets/groups, replace separators).
2. Reject obvious non-content: `sample`, `trailer`, `featurette`, `ncop`, `nced`, `preview`,
   `par2`, `.srt`/`.ass`/`.sup` (subtitles are not primary media), archives.
3. Episode tokens, highest score:
   - `sxxeyy` exact (`s02e05`);
   - `2x05`;
   - ` - 05` / `e05` / `ep05` / `episode 05`;
   - absolute number for anime (` - 17`) when `Episode.absolute` is set;
   - a range (`05-06`) matches both endpoints at lower confidence.
4. Season match: require the file's season token to equal the episode season when present.
   A missing season token is allowed only when the release is a single-season pack.
5. Title/episode-name overlap adds confidence but never overrides an exact S/E mismatch.
6. One file with a unique top score = confident match. Two files within 10 points = ambiguous.
   No file above the floor = no match.

Never pick a sample, never pick a subtitle, never pick the largest file as a tie-breaker.

### Download flow

`POST /api/downloads` body becomes:

```json
{
  "release_id": "...",
  "episode_id": "...",          // optional; inferred from the release when present
  "file_indices": [3],          // optional engine indices (advanced)
  "file_paths": ["Show.S02E05.mkv"], // optional paths from the T-030 probe (UI default)
  "allow_pack": false           // required to download a whole multi-file pack
}
```

Resolution:

1. Release already has one file or is a movie → current behaviour.
2. Multi-file release + explicit `file_indices` or `file_paths` → use them. Paths are mapped to
   qBittorrent indices after the torrent is added, by normalised file name; an unmatched path
   fails with `409 FILE_NOT_IN_TORRENT`. The Files panel sends paths, not probe indices,
   because probe and engine indices are not guaranteed to agree.
3. Multi-file release + episode → probe files (T-030 cache) and run the matcher:
   - confident single match → select it and continue;
   - ambiguous → `409 FILE_SELECTION_REQUIRED` with `candidates` (index, path, size) and no
     download row;
   - no match → `409 EPISODE_FILE_NOT_FOUND` with the file list and a hint to choose manually.
4. Multi-file release with no episode and no selection → require `allow_pack=true`; otherwise
   `409 PACK_SELECTION_REQUIRED`. Never silently download a pack.

The response includes `selected_files` and `match` so the UI can state what will be downloaded.

### qBittorrent adapter

- Add the torrent **paused** (`paused=true`) when a selection is known.
- Wait for metadata (bounded, e.g. 30 s), then call
  `POST /api/v2/torrents/filePrio`:
  - every file index `0` (do not download);
  - selected indices `1` (normal).
- Resume only after priorities are set. If metadata never arrives, leave the torrent paused and
  surface `FILE_SELECTION_PENDING`; the worker retries with backoff and never resumes a
  whole-pack download by accident.
- If the torrent is already active and a new selection is requested, update priorities in
  place; do not remove and re-add.
- Record the effective selection in `downloads.selected_files_json`.
- qBittorrent may report `progress=1` for skipped files; the organiser must filter on selected
  indices **and** on `progress`/existence, never on the torrent's overall progress alone.

### Organiser

- Only files with a selected index (or all files when selection is `null`) are candidates.
- The existing ffprobe/primary-file selection runs over that subset.
- A skipped file that happens to exist on disk from a previous attempt is ignored unless it is
  selected now.
- Jellyfin naming is unchanged; a single selected episode is filed under its season/episode.

### UI

- **Files** panel (T-030) gains a checkbox per row and a **Download selected** button. The
  button is disabled with a hint when no file is ticked.
- An episode release row shows a **Download S02E05** action that auto-matches. Before queueing,
  show `Will download: Show.S02E05.1080p.mkv (1.4 GB)` with **Change** and **Download**.
- Ambiguous match: a chooser listing the candidate files with sizes and reasons
  (`S02E05`, `S02E06`, `absolute 17`), plus a **Download whole pack** escape hatch that sends
  `allow_pack=true` after an explicit confirmation.
- The downloads list shows `S02E05 only` or `File 3 of 12` and a link to the files panel.
- On mobile, the chooser is a bottom sheet; at 390 px the file names wrap rather than scroll
  horizontally.

## Out of scope

Auto-downloading a series or a season without user action. Subtitle selection. Repacking or
renaming inside a torrent. Seeding policy for partially selected torrents. Re-matching an
already completed download (the organiser owns that). Non-qBittorrent torrent engines.

## Acceptance criteria

- [x] A 12-file season pack with an `S02E05` file downloads only that file when the user asks
      for S02E05; the other files stay at priority 0 and are not organised.
- [x] The matcher picks `S02E05` over `S02E05.sample`, and never selects a subtitle or trailer.
- [x] An anime absolute-number episode matches ` - 17` when `absolute=17`.
- [x] An ambiguous pack returns `409 FILE_SELECTION_REQUIRED` with candidates and creates no
      download row.
- [x] A pack with no matching episode returns `409 EPISODE_FILE_NOT_FOUND` and offers manual
      selection.
- [x] Downloading a pack without `allow_pack` is refused; with `allow_pack` it downloads all
      files and the organiser files each episode it can identify.
- [x] Explicit `file_indices`/`file_paths` win over the matcher and the effective engine
      indices are persisted in the download row; an unknown path returns `409`.
- [x] qBittorrent receives all-zero priorities except the selected indices, and only resumes
      after the call succeeds.
- [x] A metadata timeout leaves the torrent paused; the worker retries and never resumes the
      full pack.
- [x] The organiser ignores skipped files and files a single selected episode correctly.
- [x] The flow works at 390 px and with the keyboard alone.

## Test plan

- `tests/releases/test_episode_match.py`: a table of filenames (S/E, 2x05, absolute, range,
  sample, subtitle, multi-season, missing season, unicode/RTL) with expected winners/ambiguity.
- `tests/routers/test_downloads.py`: explicit indices, auto-match, 409 shapes, no download row
  on failure, allow_pack.
- `tests/engines/test_qbittorrent_file_priority.py`: paused add, priority call, resume ordering,
  timeout, in-place update.
- `tests/organizer/test_selection.py`: skipped files ignored, primary selection, Jellyfin path.
- `web/src/components/__tests__/file-selection.test.tsx`: checkboxes, auto-match confirmation,
  ambiguous chooser, whole-pack confirm.

## Implementation notes

- The matcher is pure and synchronous; it takes no DB session. Keep the table-driven tests
  exhaustive.
- Do not use the release filename from Torrentio as the only signal; probe files are the truth.
- File indices are qBittorrent's stable `index` from `/torrents/files`; store them as integers,
  not names. Names can change via `torrents/renameFile` and are not an identity.
- If the eventual engine is not qBittorrent, the adapter interface gains
  `set_file_selection(external_id, indices)`; do not leak qbt JSON into the router.
- A partially selected torrent is not "complete" in the seeding sense. Document this in the
  download detail view; do not promise seeding.
- Update `AGENTS.md` §5 (new columns) and §7 (state machine unchanged; selection is metadata,
  not a new state).
