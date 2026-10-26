from __future__ import annotations

from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from daku.models import AnimeMapping
from daku.repositories import titles
from daku.services.metadata import mapping
from daku.services.metadata.anime import episode_map_for_anilist, resolve_title_episode
from daku.services.metadata.classification import classify_mapped, mapped_anilist_ids


def test_forward_imdb_to_anilist(session: Session) -> None:
    mapped = mapping.lookup_imdb_to_anilist(session, "tt1234567", 1, 5, anilist_id=154587)

    assert mapped is not None
    assert mapped.target_provider == "anilist"
    assert mapped.target_id == "154587"
    assert mapped.target_episode == 5
    assert mapped.target_episode_end == 5
    assert mapped.source_scope == "s1"
    assert mapped.ratio == 1


def test_classification_accepts_imdb_movie_provider(session: Session) -> None:
    session.add(
        AnimeMapping(
            src_provider="imdb_movie",
            src_id="tt13293588",
            src_scope="",
            src_start=1,
            src_end=1,
            tgt_provider="anilist",
            tgt_id="141534",
            tgt_scope="",
            tgt_start=1,
            tgt_end=1,
            ratio=1,
        )
    )
    session.commit()

    assert mapped_anilist_ids(session, ["tt13293588"]) == {"tt13293588": 141534}
    signal = classify_mapped(session, "tt13293588")
    assert signal.is_anime is True
    assert signal.confidence == "mapped"
    assert signal.anilist_id == 141534


def test_forward_maps_across_seasons_to_anilist_numbering(session: Session) -> None:
    mapped = mapping.lookup_imdb_to_anilist(session, "tt1234567", 2, 1, anilist_id=154587)

    assert mapped is not None
    assert mapped.target_episode == 29
    assert mapped.target_episode_end == 29


def test_forward_honours_target_id(session: Session) -> None:
    mapped = mapping.lookup_imdb_to_anilist(session, "tt1234567", 2, 1, anilist_id=154587)
    wrong = mapping.lookup_imdb_to_anilist(session, "tt1234567", 2, 1, anilist_id=999)

    assert mapped is not None
    assert wrong is None


def test_reverse_anilist_to_imdb(session: Session) -> None:
    mapped = mapping.lookup_anilist_to_imdb(session, 154587, 5)

    assert mapped is not None
    assert mapped.source_provider == "anilist"
    assert mapped.target_provider == "imdb_show"
    assert mapped.target_id == "tt1234567"
    assert mapped.target_scope == "s1"
    assert mapped.target_episode == 5

    second_season = mapping.lookup_anilist_to_imdb(session, 154587, 30)
    assert second_season is not None
    assert second_season.target_scope == "s2"
    assert second_season.target_episode == 2


def test_multi_segment_target_skips_gap(session: Session) -> None:
    assert (
        mapping.resolve_episode(
            session,
            source_provider="tmdb_show",
            source_id="500",
            source_scope="s1",
            episode=6,
            target_provider="anilist",
        ).target_episode
        == 6
    )  # type: ignore[union-attr]
    assert (
        mapping.resolve_episode(
            session,
            source_provider="tmdb_show",
            source_id="500",
            source_scope="s1",
            episode=7,
            target_provider="anilist",
        ).target_episode
        == 8
    )  # type: ignore[union-attr]


def test_positive_ratio_spans_multiple_target_episodes(session: Session) -> None:
    first = mapping.resolve_episode(
        session,
        source_provider="tmdb_show",
        source_id="500",
        source_scope="s1",
        episode=13,
        target_provider="anilist",
    )
    second = mapping.resolve_episode(
        session,
        source_provider="tmdb_show",
        source_id="500",
        source_scope="s1",
        episode=14,
        target_provider="anilist",
    )

    assert first is not None and (first.target_episode, first.target_episode_end) == (14, 15)
    assert first.ratio == 2
    assert second is not None and (second.target_episode, second.target_episode_end) == (16, 17)


def test_negative_ratio_groups_source_episodes(session: Session) -> None:
    def resolve(episode: int) -> tuple[int, int]:
        mapped = mapping.resolve_episode(
            session,
            source_provider="tvdb_show",
            source_id="600",
            source_scope="s1",
            episode=episode,
            target_provider="anilist",
        )
        assert mapped is not None
        return mapped.target_episode, mapped.target_episode_end

    assert [resolve(episode) for episode in (1, 2, 3, 4, 5, 6)] == [
        (1, 1),
        (1, 1),
        (1, 1),
        (2, 2),
        (2, 2),
        (2, 2),
    ]


def test_reverse_ratio_spans_source_episodes(session: Session) -> None:
    mapped = mapping.resolve_episode(
        session,
        source_provider="anilist",
        source_id="2000",
        source_scope="",
        episode=2,
        target_provider="tvdb_show",
    )

    assert mapped is not None
    assert (mapped.target_episode, mapped.target_episode_end) == (4, 6)
    assert mapped.ratio == 3


def test_no_match_returns_none(session: Session) -> None:
    assert mapping.lookup_imdb_to_anilist(session, "tt0000000", 1, 1) is None
    assert mapping.lookup_imdb_to_anilist(session, "tt1234567", 9, 1) is None
    assert mapping.lookup_anilist_to_imdb(session, 20954, 2) is None
    assert mapping.lookup_anilist_to_imdb(session, 1, 1) is None


def test_episode_map_for_anilist_both_directions(session: Session) -> None:
    forward = episode_map_for_anilist(session, 154587, season=1, episode=5)
    reverse = episode_map_for_anilist(session, 154587, season=None, episode=5)

    assert forward is not None
    assert forward.target_provider == "anilist"
    assert forward.target_episode == 5
    assert reverse is not None
    assert reverse.target_provider == "imdb_show"
    assert reverse.target_episode == 5


def test_override_takes_precedence_and_survives_refresh(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
    mapping_payload: dict[str, Any],
) -> None:
    engine, factory, _report = ingested
    with factory() as session:
        title = titles.create_title(
            session,
            kind="anime",
            title="Frieren",
            imdb_id="tt1234567",
            anilist_id=154587,
        )
        original = resolve_title_episode(session, title, 1, 5)
        assert original is not None
        assert original.target_episode == 5
        assert original.override is False

        row = mapping.write_override(
            session,
            title=title,
            season=1,
            episode=5,
            target_episode=7,
        )
        assert row.target_id == "154587"

        overridden = resolve_title_episode(session, title, 1, 5)
        assert overridden is not None
        assert overridden.target_episode == 7
        assert overridden.override is True

        # A refresh replaces every dataset row but never touches overrides.
        mapping.ingest_payload(engine, mapping_payload)

        session.expire_all()
        after_refresh = resolve_title_episode(session, title, 1, 5)
        assert after_refresh is not None
        assert after_refresh.target_episode == 7
        assert after_refresh.override is True


def test_override_requires_target_id(
    ingested: tuple[Engine, sessionmaker[Session], mapping.IngestReport],
) -> None:
    _engine, factory, _report = ingested
    with factory() as session:
        title = titles.create_title(session, kind="anime", title="No ids")
        try:
            mapping.write_override(session, title=title, season=1, episode=1, target_episode=1)
        except mapping.MappingError as exc:
            assert exc.code == "MAPPING_NO_TARGET"
        else:  # pragma: no cover - defensive
            raise AssertionError("expected MappingError")


def test_non_anime_title_does_not_use_mapping(session: Session) -> None:
    title = titles.create_title(
        session,
        kind="series",
        title="A Show",
        imdb_id="tt1234567",
        anilist_id=154587,
    )

    assert resolve_title_episode(session, title, 1, 5) is None
