from __future__ import annotations

import pytest

from daku.services.torrents.animetosho import (
    matches_episode,
    matches_query,
    pack_covers_episode,
    pack_label,
    parse_release_episode,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("[G] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]", (6, 7, False)),
        ("[G] Show s6e7.mkv", (6, 7, False)),
        ("[G] Show 06x07 [720p]", (6, 7, False)),
        ("[G] Show E07.mkv", (None, 7, False)),
        ("[G] Show EP07.mkv", (None, 7, False)),
        ("[G] Show Episode 7.mkv", (None, 7, False)),
        ("[G] Show - 07 [1080p]", (None, 7, False)),
        ("[G] Show [07] [1080p]", (None, 7, False)),
        ("[G] Show 07v2 [1080p]", (None, 7, False)),
        ("[G] Show 第7話 [1080p]", (None, 7, False)),
        ("[G] Show S01E01-E12 [1080p]", (1, 1, True)),
        ("[G] Show 01-12 [1080p]", (None, 1, True)),
        ("[G] Show 1~12 [1080p]", (None, 1, True)),
    ],
)
def test_parse_episode_notations(
    filename: str, expected: tuple[int | None, int | None, bool]
) -> None:
    parsed = parse_release_episode(filename)

    assert (parsed.season, parsed.episode, parsed.is_pack) == expected


def test_audio_channel_decimal_is_not_a_range() -> None:
    parsed = parse_release_episode("[G] Show S01E07 DDP5.1-2.0 [1080p]")

    assert parsed.is_pack is False
    assert (parsed.season, parsed.episode) == (1, 7)


def test_provider_season_accepts_split_cour_release() -> None:
    assert matches_episode(
        "[G] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]",
        season=1,
        episode=7,
        provider_seasons=[6],
    )


def test_provider_season_never_accepts_the_wrong_episode() -> None:
    assert not matches_episode(
        "[G] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]",
        season=1,
        episode=8,
        provider_seasons=[6],
    )


def test_single_season_fallback_accepts_provider_season_without_mapping() -> None:
    filename = "[G] JoJo's Bizarre Adventure - Steel Ball Run S06E07 [1080p]"

    assert matches_episode(filename, season=1, episode=7, season_fallback=True)
    assert not matches_episode(filename, season=1, episode=8, season_fallback=True)
    assert not matches_episode(
        filename, season=1, episode=7, season_fallback=True, title_matched=False
    )


def test_library_season_still_matches_directly() -> None:
    assert matches_episode("[G] Show S01E07 [1080p]", season=1, episode=7)
    assert not matches_episode("[G] Show S02E07 [1080p]", season=1, episode=7)


def test_absolute_only_release_matches_absolute_request() -> None:
    assert matches_episode("[G] Show - 07 [1080p]", season=1, episode=7)
    assert matches_episode("[G] Show - 07 [1080p]", season=1, episode=7, absolute=7)


def test_batches_are_never_attached_to_one_episode() -> None:
    assert not matches_episode("[G] Show S01E01-E12 [1080p]", season=1, episode=7)
    assert not matches_episode("[G] Show 01-12 [1080p]", season=1, episode=7)
    assert matches_episode("[G] Show S01E01-E12 [1080p]", season=1, episode=7, pack=True)


def test_season_zero_specials_only_match_zero() -> None:
    assert matches_episode("[G] Show S00E07 [1080p]", season=0, episode=7)
    assert not matches_episode("[G] Show S00E07 [1080p]", season=1, episode=7)


def test_different_show_with_run_token_is_rejected() -> None:
    filename = "[G] The Run 2026 - 07 [1080p]"

    assert not matches_query("Steel Ball Run", filename)
    assert matches_query("Steel Ball Run", "[G] JoJo's Bizarre Adventure - Steel Ball Run - 07")


@pytest.mark.parametrize(
    "filename",
    [
        "[Judas] Cyberpunk Edgerunners (Season 2) [1080p][HEVC x265 10bit] (Batch)",
        "Cyberpunk Edgerunners S02 2160p NF WEB-DL DDP5.1 HEVC (Batch)",
        "[whomst] Cyberpunk Edgerunners - S02 v2 [WEB 1080p][Dual Audio]",
        "Cyberpunk Edgerunners S02 COMPLETE (720p WEBRip)",
    ],
)
def test_season_marker_without_episode_is_a_pack(filename: str) -> None:
    parsed = parse_release_episode(filename)

    assert parsed.is_pack is True
    assert parsed.season == 2
    assert parsed.episode is None


def test_range_after_a_season_number_is_parsed_as_a_pack() -> None:
    parsed = parse_release_episode(
        "[Erai-raws] Cyberpunk: Edgerunners 2 - 01 ~ 10 [1080p NF WEB-DL][BATCH]"
    )

    assert parsed.is_pack is True
    assert parsed.season == 2
    assert (parsed.episode, parsed.end) == (1, 10)


def test_pack_coverage_is_season_aware() -> None:
    judas = "[Judas] Cyberpunk Edgerunners (Season 2) [1080p] (Batch)"
    erai = "[Erai-raws] Cyberpunk: Edgerunners 2 - 01 ~ 10 [1080p][BATCH]"

    assert pack_covers_episode(judas, season=2, episode=5)
    assert not pack_covers_episode(judas, season=1, episode=5)
    assert pack_covers_episode(erai, season=2, episode=5)
    assert pack_label(judas) == "S02"
    assert pack_label(erai) == "E01-E10"


def test_pack_coverage_respects_provider_seasons() -> None:
    filename = "[G] JoJo's Bizarre Adventure - Steel Ball Run S06 (Batch)"

    assert pack_covers_episode(filename, season=1, episode=7, provider_seasons=[6])
    assert not pack_covers_episode(filename, season=1, episode=7, provider_seasons=[5])
