# T-012 — Media inspection with ffprobe

**Phase:** 3 · **Depends on:** T-009 · **Estimate:** S

## Goal

After a download completes, find the actual video file(s), inspect them, and choose the
primary one that should become the library entry.

## Scope

- `services/organize/ffprobe.py`: locate `ffprobe`, run with `-v quiet -print_format json
  -show_format -show_streams`, parse into `MediaInfo`.
- `services/organize/select.py`: scan the download directory, ignore samples/trailers/extras,
  rank candidates, pick `is_primary`.
- Persist `media_files` rows with container, vcodec, acodec, width, height, duration.
- Detect adjacent subtitle files (`.srt`, `.ass`, `.vtt`) and store them as sidecars.
- `POST /api/downloads/{id}/inspect` and automatic invocation on completion.

## Out of scope

Organising into Jellyfin paths (T-013), transcoding, subtitle conversion.

## Acceptance criteria

- [x] A completed download is scanned and the largest valid video file is marked
      `is_primary=1`; all other video files are stored with `is_primary=0`.
- [x] Files under `Sample/`, containing `sample`, `trailer`, or under 50 MB, are not primary.
- [x] Multi-episode packs produce one `media_files` row per video file, all linked to the
      download, with `episode_id` left null until T-013 assigns it.
- [x] `ffprobe` output is parsed into `{container, vcodec, acodec, width, height, duration_s,
      audio_languages, subtitle_languages}`; missing streams yield nulls, not exceptions.
- [x] A corrupt or zero-byte file is reported as `failed` with a clear error, not crashed on.
- [x] `ffprobe` missing → doctor says `not_found` and organisation is blocked with a readable
      message.
- [x] Inspection never mutates the source file.

## Implementation notes

Use `ffprobe` from `PATH`; `sudo apt install ffmpeg` is a documented prerequisite. Candidate extensions:
`mkv, mp4, avi, m4v, mov, webm, ts, m2ts, wmv, flv`.

Ranking: has a video stream → non-sample → largest `size_bytes`. Do not use filename heuristics
alone; a `sample.mkv` can be 200 MB.

Video stream selection: first stream with `codec_type == "video"` and `disposition.attached_pic != 1`
(cover art is a video stream in some MKVs).

Run ffprobe through `asyncio.to_thread` with a 30 s timeout per file. Never run it parallel for
more than 4 files.

Sidecars: same basename + subtitle extension, case-insensitive, in the same directory.

## Test plan

- `tests/organize/test_select.py`: single file, sample + main, trailer, multi-episode pack,
  zero-byte, cover-art-only.
- `tests/organize/test_ffprobe.py`: recorded JSON for MKV with HEVC/DTS, MP4 H.264/AAC,
  audio-only file, malformed output.

## Completion notes

- `services/organize/ffprobe.py` runs `ffprobe -v quiet -print_format json -show_format
  -show_streams` through `asyncio.to_thread`, with a 30 s timeout and a 4-file semaphore.
  The first non-cover-art video stream is chosen; missing audio/subtitle streams become
  nulls or empty lists.
- `services/organize/select.py` scans the download directory, probes at most four files at
  a time, ranks by playable stream → non-sample → size, and persists one `media_files` row
  per valid video with `episode_id` left null for T-013.
- Sample detection uses path segments (`Sample/`, `Extras/`), filename tokens and the
  50 MB floor. `ffprobe` is the source of truth for "has a video stream"; filename
  heuristics alone never decide.
- Adjacent `.srt`, `.ass` and `.vtt` sidecars are stored in the new
  `media_files.sidecars_json` column (migration **v6**) and returned by
  `POST /api/downloads/{id}/inspect`.
- The worker inspects automatically when a row first reaches `complete`, but only when the
  download directory exists. Inspection failures set `downloads.error` and never crash the
  loop; an inspection can be re-run idempotently from the API.
- The doctor reports `not_found` (not `error`) when `ffprobe` is missing, with the install
  command in the detail. `probe_sync` raises `FFPROBE_MISSING` before reading any file.
