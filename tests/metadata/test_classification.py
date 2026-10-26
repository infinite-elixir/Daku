from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from daku.models import AnimeMapping
from daku.services.metadata.classification import (
    classify_mapped,
    enrich_exact,
    exact_signal,
    mapped_anilist_ids,
    mapping_dataset_present,
    match_exact,
)
from daku.services.metadata.models import TitleCandidate


def _mapping(
    session: Session,
    *,
    src_provider: str = "imdb_movie",
    src_id: str = "tt12590266",
    tgt_id: str = "120377",
) -> None:
    session.add(
        AnimeMapping(
            src_provider=src_provider,
            src_id=src_id,
            src_scope="",
            src_start=1,
            src_end=1,
            tgt_provider="anilist",
            tgt_id=tgt_id,
            tgt_scope="",
            tgt_start=1,
            tgt_end=1,
            ratio=1,
        )
    )
    session.commit()


def _media(
    *,
    title: str = "Cyberpunk: Edgerunners",
    year: int | None = 2022,
    fmt: str = "ONA",
    country: str = "JP",
    anilist_id: int = 120377,
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


def test_mapped_hit(db: tuple[object, sessionmaker[Session]]) -> None:
    _, factory = db
    with factory() as session:
        _mapping(session)
        signal = classify_mapped(session, "tt12590266")
        assert signal.is_anime is True
        assert signal.confidence == "mapped"
        assert signal.anilist_id == 120377
        assert signal.reason == "Detected via AniBridge mapping"
        assert mapping_dataset_present(session) is True


def test_mapped_lookup_is_one_query(db: tuple[object, sessionmaker[Session]]) -> None:
    _, factory = db
    with factory() as session:
        _mapping(session, src_id="tt0000001")
        _mapping(session, src_id="tt0000002", tgt_id="120378")
        assert mapped_anilist_ids(session, ["tt0000001", "tt0000002", "tt9999999"]) == {
            "tt0000001": 120377,
            "tt0000002": 120378,
        }


def test_exact_match_hit() -> None:
    signal = exact_signal(
        title="Cyberpunk: Edgerunners",
        year=2022,
        candidate_title="CYBERPUNK - EDGERUNNERS",
        candidate_year=2022,
        synonyms=[],
        format="ONA",
        country_of_origin="JP",
        anilist_id=120377,
    )
    assert signal.is_anime is True
    assert signal.confidence == "exact"
    assert signal.reason == "AniList exact match"


def test_exact_match_accepts_a_synonym() -> None:
    signal = exact_signal(
        title="Frieren: Beyond Journey's End",
        year=2023,
        candidate_title="Sousou no Frieren",
        candidate_year=2023,
        synonyms=["Frieren: Beyond Journey's End"],
        format="TV",
        country_of_origin="JP",
        anilist_id=154587,
    )
    assert signal.is_anime is True


def test_year_mismatch_is_not_anime() -> None:
    assert (
        match_exact(
            TitleCandidate("imdb", "tt12590266", "series", "Cyberpunk: Edgerunners", 2009),
            [_media(year=2022)],
        ).is_anime
        is False
    )


def test_country_mismatch_is_not_anime() -> None:
    assert (
        match_exact(
            TitleCandidate("imdb", "tt1480055", "series", "Arcane", 2021),
            [_media(title="Arcane", year=2021, country="US", anilist_id=999)],
        ).is_anime
        is False
    )


def test_western_animation_without_anilist_row_is_not_anime() -> None:
    assert (
        match_exact(
            TitleCandidate("imdb", "tt6741278", "series", "Invincible", 2021),
            [],
        ).is_anime
        is False
    )


def test_format_music_is_not_anime() -> None:
    assert (
        match_exact(
            TitleCandidate("imdb", "tt12590266", "series", "Cyberpunk: Edgerunners", 2022),
            [_media(fmt="MUSIC")],
        ).is_anime
        is False
    )


def test_missing_dataset_returns_none(db: tuple[object, sessionmaker[Session]]) -> None:
    _, factory = db
    with factory() as session:
        assert mapping_dataset_present(session) is False
        assert mapped_anilist_ids(session, ["tt12590266"]) == {}
        assert classify_mapped(session, "tt12590266").confidence == "none"


def test_enrich_exact_marks_candidates_once() -> None:
    candidates = [
        TitleCandidate("imdb", "tt12590266", "series", "Cyberpunk: Edgerunners", 2022),
        TitleCandidate("imdb", "tt6741278", "series", "Invincible", 2021),
    ]
    media = [
        _media(),
        _media(title="Invincible", year=2021, country="US", anilist_id=42, fmt="TV"),
    ]
    assert enrich_exact(candidates, media) == 1
    assert candidates[0].anime is True
    assert candidates[0].anilist_id == 120377
    assert candidates[0].classification_reason == "AniList exact match"
    assert candidates[1].anime is False
