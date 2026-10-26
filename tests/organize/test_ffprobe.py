from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from daku.errors import AppError
from daku.services.organize.ffprobe import MediaInfo, parse_probe, probe_sync

MKV_HEVC = {
    "format": {"format_name": "matroska,webm", "duration": "5400.123000"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "width": 1920,
            "height": 1080,
            "disposition": {"attached_pic": 0},
        },
        {"codec_type": "audio", "codec_name": "dts", "tags": {"language": "eng"}},
        {"codec_type": "audio", "codec_name": "ac3", "tags": {"language": "jpn"}},
        {"codec_type": "subtitle", "tags": {"language": "eng"}},
        {"codec_type": "subtitle", "tags": {"language": "und"}},
    ],
}

MP4_H264 = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "7200.0"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 3840, "height": 2160},
        {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
    ],
}

AUDIO_ONLY = {
    "format": {"format_name": "mp3", "duration": "210.5"},
    "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
}

COVER_ART_ONLY = {
    "format": {"duration": "60.0"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "mjpeg",
            "width": 600,
            "height": 600,
            "disposition": {"attached_pic": 1},
        }
    ],
}


def test_parse_mkv_with_hevc_and_dts() -> None:
    info = parse_probe(MKV_HEVC)

    assert info == MediaInfo(
        container="matroska",
        vcodec="hevc",
        acodec="dts",
        width=1920,
        height=1080,
        duration_s=5400.123,
        audio_languages=["eng", "jpn"],
        subtitle_languages=["eng"],
    )


def test_parse_mp4_with_h264_and_aac() -> None:
    info = parse_probe(MP4_H264)

    assert info.container == "mov"
    assert info.vcodec == "h264"
    assert info.acodec == "aac"
    assert (info.width, info.height) == (3840, 2160)
    assert info.duration_s == 7200.0
    assert info.audio_languages == ["eng"]
    assert info.subtitle_languages == []


def test_audio_only_has_no_video_stream() -> None:
    info = parse_probe(AUDIO_ONLY)

    assert info.has_video is False
    assert info.vcodec is None
    assert info.acodec == "mp3"
    assert info.width is None


def test_cover_art_is_not_treated_as_the_video_stream() -> None:
    info = parse_probe(COVER_ART_ONLY)

    assert info.has_video is False
    assert info.vcodec is None


def test_missing_streams_yield_nulls_instead_of_exceptions() -> None:
    info = parse_probe({})

    assert info == MediaInfo()


def test_probe_sync_parses_command_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"not actually video")

    def fake_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(MKV_HEVC), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    info = probe_sync(media, ffprobe_bin="/usr/bin/ffprobe")

    assert info.vcodec == "hevc"


def test_probe_sync_rejects_zero_byte_files(tmp_path: Path) -> None:
    media = tmp_path / "empty.mkv"
    media.write_bytes(b"")

    with pytest.raises(AppError) as excinfo:
        probe_sync(media, ffprobe_bin="/usr/bin/ffprobe")

    assert excinfo.value.code == "MEDIA_FILE_EMPTY"
    assert "zero bytes" in excinfo.value.message


def test_probe_sync_reports_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "broken.mkv"
    media.write_bytes(b"x")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )

    with pytest.raises(AppError) as excinfo:
        probe_sync(media, ffprobe_bin="/usr/bin/ffprobe")

    assert excinfo.value.code == "MEDIA_PROBE_FAILED"
    assert "malformed JSON" in excinfo.value.message


def test_probe_sync_reports_a_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "broken.mkv"
    media.write_bytes(b"x")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=1, stdout="", stderr="Invalid data found when processing input"
        ),
    )

    with pytest.raises(AppError) as excinfo:
        probe_sync(media, ffprobe_bin="/usr/bin/ffprobe")

    assert excinfo.value.code == "MEDIA_PROBE_FAILED"
    assert "Invalid data" in excinfo.value.message


def test_probe_sync_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "slow.mkv"
    media.write_bytes(b"x")

    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AppError) as excinfo:
        probe_sync(media, ffprobe_bin="/usr/bin/ffprobe")

    assert "timed out" in excinfo.value.message


def test_missing_ffprobe_blocks_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"x")
    monkeypatch.setattr("daku.services.organize.ffprobe.shutil.which", lambda _name: None)

    with pytest.raises(AppError) as excinfo:
        probe_sync(media)

    assert excinfo.value.code == "FFPROBE_MISSING"
    assert "ffmpeg" in excinfo.value.message
