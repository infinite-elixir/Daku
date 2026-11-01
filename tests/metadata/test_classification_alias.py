"""Alias classification for split-cour anime (T-052)."""

from __future__ import annotations

from daku.services.metadata.classification import match_exact
from daku.services.metadata.models import TitleCandidate

STEEL_BALL_RUN_IMDB = TitleCandidate(
    source="imdb",
    source_id="tt38268282",
    kind="series",
    title="Steel Ball Run: JoJo's Bizarre Adventure",
    year=2026,
)


def _anime(
    anilist_id: int,
    title: str,
    *,
    year: int | None = 2026,
    fmt: str = "ONA",
    country: str = "JP",
    synonyms: tuple[str, ...] = (),
) -> TitleCandidate:
    return TitleCandidate(
        source="anilist",
        source_id=str(anilist_id),
        kind="anime",
        title=title,
        year=year,
        format=fmt,
        country_of_origin=country,
        synonyms=synonyms,
    )


def test_exact_match_still_wins_over_an_alias() -> None:
    exact = _anime(100, "Steel Ball Run: JoJo's Bizarre Adventure")
    alias = _anime(200, "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE")

    signal = match_exact(STEEL_BALL_RUN_IMDB, [alias, exact])

    assert signal.is_anime is True
    assert signal.confidence == "exact"
    assert signal.anilist_id == 100


def test_steel_ball_run_is_classified_through_an_anilist_alias() -> None:
    signal = match_exact(
        STEEL_BALL_RUN_IMDB,
        [
            _anime(
                190327,
                "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE",
                synonyms=("STEEL BALL RUN JoJo's Bizarre Adventure 1st STAGE",),
            )
        ],
    )

    assert signal.is_anime is True
    assert signal.confidence == "alias"
    assert signal.anilist_id == 190327
    assert "alias" in signal.reason.lower()


def test_an_unrelated_same_year_series_is_not_an_alias_match() -> None:
    signal = match_exact(
        TitleCandidate(
            source="imdb",
            source_id="tt99999999",
            kind="series",
            title="The Last of Us",
            year=2026,
        ),
        [_anime(210482, "JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE")],
    )

    assert signal.is_anime is False
    assert signal.confidence == "none"


def test_an_alias_does_not_accept_a_manga_format() -> None:
    signal = match_exact(
        STEEL_BALL_RUN_IMDB,
        [
            _anime(
                31706,
                "JoJo no Kimyou na Bouken: Steel Ball Run",
                fmt="MANGA",
            )
        ],
    )

    assert signal.is_anime is False


def test_ambiguous_cours_prefer_an_anibridge_mapping_then_lowest_id() -> None:
    media = [
        _anime(210482, "JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE"),
        _anime(190327, "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE"),
    ]

    preferred = match_exact(STEEL_BALL_RUN_IMDB, media, preferred_ids={210482})
    deterministic = match_exact(STEEL_BALL_RUN_IMDB, list(reversed(media)))

    assert preferred.anilist_id == 210482
    assert deterministic.anilist_id == 190327
    assert match_exact(STEEL_BALL_RUN_IMDB, media).anilist_id == 190327
