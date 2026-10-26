from __future__ import annotations

import pytest

from daku.services.releases.episode_match import (
    EpisodeTarget,
    match_episode,
    normalize_path,
    season_episode_from_path,
)
from daku.services.torrents.probe import TorrentFile


def files(*paths: str) -> list[TorrentFile]:
    return [
        TorrentFile(index=index, path=path, size_bytes=(index + 1) * 1000)
        for index, path in enumerate(paths)
    ]


def target(
    season: int = 2,
    episode: int = 5,
    *,
    absolute: int | None = None,
    name: str | None = None,
) -> EpisodeTarget:
    return EpisodeTarget(season=season, episode=episode, absolute=absolute, name=name)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Show.S02E05.1080p.mkv", "S02E05"),
        ("Show - 2x05 - Title.mkv", "2x05"),
        ("Show - E05.mkv", "episode 05"),
        ("Show.EP05.mkv", "episode 05"),
        ("Show Episode 05.mkv", "episode 05"),
        ("Show - 05.mkv", "episode 05"),
    ],
)
def test_episode_tokens_win(path: str, expected: str) -> None:
    outcome = match_episode(files(path), target())

    assert outcome.match is not None
    assert outcome.match.path == path
    assert expected in outcome.match.reason


def test_sample_never_beats_the_real_episode() -> None:
    pack = files("Show.S02E05.sample.mkv", "Show.S02E05.1080p.mkv")

    outcome = match_episode(pack, target())

    assert outcome.match is not None
    assert outcome.match.path == "Show.S02E05.1080p.mkv"
    assert not outcome.ambiguous


def test_wrong_episode_is_not_a_candidate() -> None:
    outcome = match_episode(files("Show.S02E06.mkv"), target())

    assert outcome.match is None
    assert "no file" in outcome.reason


def test_subtitles_trailers_and_archives_are_ignored() -> None:
    pack = files(
        "Show.S02E05.srt",
        "Show.S02E05.trailer.mkv",
        "Show.S02E05.par2",
        "Show.S02E05.1080p.mkv",
    )

    outcome = match_episode(pack, target())

    assert outcome.match is not None
    assert outcome.match.path == "Show.S02E05.1080p.mkv"


def test_anime_absolute_number_matches_when_episode_absolute_is_set() -> None:
    episode = target(season=1, episode=17, absolute=17, name="The Gate")

    outcome = match_episode(
        files("Show - 17 - The Gate.mkv"),
        episode,
        title_kind="anime",
    )

    assert outcome.match is not None
    assert "absolute 17" in outcome.match.reason


def test_range_matches_both_endpoints_at_lower_confidence() -> None:
    outcome = match_episode(files("Show - 05-06.mkv"), target())

    assert outcome.match is not None
    assert outcome.match.score < 100
    assert "range" in outcome.match.reason


def test_two_equally_good_files_are_ambiguous() -> None:
    pack = files("Show.S02E05.1080p.mkv", "Show.S02E05.720p.mkv")

    outcome = match_episode(pack, target())

    assert outcome.match is None
    assert outcome.ambiguous is True
    assert {item.path for item in outcome.candidates} == {
        "Show.S02E05.1080p.mkv",
        "Show.S02E05.720p.mkv",
    }


def test_missing_season_is_allowed_in_a_single_season_pack() -> None:
    outcome = match_episode(
        files("Episode 05.mkv"),
        target(),
        release_filename="Show S02 Complete",
    )

    assert outcome.match is not None
    assert outcome.match.path == "Episode 05.mkv"


def test_missing_season_is_rejected_in_a_multi_season_pack() -> None:
    pack = files("Show.S01E05.mkv", "Episode 05.mkv")

    outcome = match_episode(pack, target())

    assert outcome.match is None


def test_season_mismatch_is_rejected_even_when_episode_matches() -> None:
    outcome = match_episode(files("Show.S01E05.mkv"), target(season=2))

    assert outcome.match is None


def test_unicode_and_rtl_marks_do_not_break_matching() -> None:
    path = "\u202eShow\u202c.S02E05.1080p.mkv"

    outcome = match_episode(files(path), target())

    assert outcome.match is not None
    assert outcome.match.path == path
    assert "s02e05" in normalize_path(path)


def test_season_episode_from_path_reads_exact_and_x_tokens() -> None:
    assert season_episode_from_path("Show.S02E05.mkv") == (2, 5)
    assert season_episode_from_path("Show - 3x07.mkv") == (3, 7)
    assert season_episode_from_path("Show.mkv") is None
