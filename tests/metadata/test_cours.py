"""Split-cour resolver tests (T-052)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from sqlalchemy.orm import Session, sessionmaker

from daku.models import AnimeMapping
from daku.services.metadata import anilist
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import make_client
from daku.services.metadata.cours import (
    build_ranges,
    combined_refs,
    cour_rows,
    provider_season_for_anilist,
    resolve_cours,
    season_specs,
)
from daku.services.metadata.models import TitleDetail

FIRST_COUR = 190327
SECOND_COUR = 210482


def _media(
    anilist_id: int,
    *,
    title: str,
    episodes: int,
    relations: list[dict[str, object]] | None = None,
    poster: str | None = None,
) -> dict[str, object]:
    return {
        "id": anilist_id,
        "type": "ANIME",
        "format": "ONA",
        "episodes": episodes,
        "seasonYear": 2026,
        "countryOfOrigin": "JP",
        "title": {"romaji": title, "english": title},
        "coverImage": {"extraLarge": poster} if poster else None,
        "relations": {"edges": relations or []},
    }


def _edge(relation: str, anilist_id: int, title: str, episodes: int) -> dict[str, object]:
    return {
        "relationType": relation,
        "node": {
            "id": anilist_id,
            "type": "ANIME",
            "format": "ONA",
            "episodes": episodes,
            "seasonYear": 2026,
            "countryOfOrigin": "JP",
            "title": {"romaji": title, "english": title},
        },
    }


def _primary() -> TitleDetail:
    payload = {
        "data": {
            "Media": _media(
                FIRST_COUR,
                title="JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE",
                episodes=1,
                relations=[
                    _edge(
                        "SEQUEL",
                        SECOND_COUR,
                        "JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE",
                        11,
                    )
                ],
                poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/first.jpg",
            )
        }
    }
    detail = anilist.parse_detail(payload["data"])
    assert detail is not None
    detail.episodes = [ref for ref in detail.episodes]
    return detail


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    variables = body.get("variables", {})
    anilist_id = variables.get("id")
    if anilist_id == SECOND_COUR:
        return httpx.Response(
            200,
            json={
                "data": {
                    "Media": _media(
                        SECOND_COUR,
                        title="JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE",
                        episodes=11,
                        poster="https://s4.anilist.co/file/anilistcdn/media/anime/cover/second.jpg",
                    )
                }
            },
        )
    if anilist_id == FIRST_COUR:
        return httpx.Response(
            200,
            json={
                "data": {
                    "Media": _media(
                        FIRST_COUR,
                        title="JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE",
                        episodes=1,
                    )
                }
            },
        )
    return httpx.Response(200, json={"data": {"Media": None}})


@pytest.fixture(autouse=True)
def _clear_artwork_cache() -> None:
    from daku.services.metadata import artwork

    artwork.clear_cache()


def _mapping(session: Session, tgt_id: str) -> None:
    session.add(
        AnimeMapping(
            src_provider="tvdb_show",
            src_id="262954",
            src_scope="s6",
            src_start=1,
            src_end=12,
            tgt_provider="anilist",
            tgt_id=tgt_id,
            tgt_scope="",
            tgt_start=1,
            tgt_end=12,
            ratio=1,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_sequel_cours_become_one_library_season_with_provider_season_6(
    db: tuple[object, sessionmaker[Session]],
) -> None:
    _engine, factory = db
    primary = _primary()
    cache = TTLCache()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(side_effect=_handler)
        async with make_client() as client:
            entries = await resolve_cours(client, cache, primary_id=FIRST_COUR, primary=primary)

    assert [entry.anilist_id for entry in entries] == [FIRST_COUR, SECOND_COUR]
    assert entries[1].episode_count == 11
    assert entries[1].poster_url is not None

    with factory() as session:
        _mapping(session, str(FIRST_COUR))
        provider = provider_season_for_anilist(session, [entry.anilist_id for entry in entries])

    ranges = build_ranges(entries, provider_season=provider)
    assert [(item.start_episode, item.end_episode) for item in ranges] == [(1, 1), (2, 12)]
    assert all(item.provider_season == 6 for item in ranges)
    assert [item.anilist_id for item in ranges] == [FIRST_COUR, SECOND_COUR]

    refs = combined_refs(ranges)
    assert [(ref.season, ref.episode) for ref in refs] == [(1, number) for number in range(1, 13)]
    assert [row["absolute_offset"] for row in cour_rows(ranges)] == [0, 1]

    specs = season_specs(ranges)
    assert [(spec["library_season"], spec["provider_season"]) for spec in specs] == [(1, 6)]
    assert specs[0]["episode_count"] == 12


@pytest.mark.asyncio
async def test_refreshing_the_second_cour_finds_the_prequel() -> None:
    primary_payload = {
        "data": {
            "Media": _media(
                SECOND_COUR,
                title="JoJo no Kimyou na Bouken: Steel Ball Run - 2nd & 3rd STAGE",
                episodes=11,
                relations=[
                    _edge(
                        "PREQUEL",
                        FIRST_COUR,
                        "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE",
                        1,
                    )
                ],
            )
        }
    }
    primary = anilist.parse_detail(primary_payload["data"])
    assert primary is not None

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(side_effect=_handler)
        async with make_client() as client:
            entries = await resolve_cours(
                client, TTLCache(), primary_id=SECOND_COUR, primary=primary
            )

    assert [entry.anilist_id for entry in entries] == [FIRST_COUR, SECOND_COUR]


@pytest.mark.asyncio
async def test_a_failed_cour_detail_keeps_the_relation_stub() -> None:
    primary = _primary()

    with respx.mock(assert_all_called=False) as mock:
        mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(503))
        async with make_client() as client:
            entries = await resolve_cours(
                client, TTLCache(), primary_id=FIRST_COUR, primary=primary
            )

    assert [entry.anilist_id for entry in entries] == [FIRST_COUR, SECOND_COUR]
    assert entries[1].episode_count == 11
    assert entries[1].detail is None


def test_cour_rows_cascade_when_the_title_is_deleted(
    db: tuple[object, sessionmaker[Session]],
) -> None:
    from sqlalchemy import func, select

    from daku.models import Title, TitleCour
    from daku.repositories import cours as cours_repo
    from daku.utils import new_id, utc_now_iso

    _engine, factory = db
    with factory() as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="Steel Ball Run",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.commit()
        cours_repo.replace_cours(
            session,
            title.id,
            [
                {
                    "library_season": 1,
                    "provider_season": 6,
                    "start_episode": 1,
                    "end_episode": 1,
                    "anilist_id": FIRST_COUR,
                    "absolute_offset": 0,
                },
                {
                    "library_season": 1,
                    "provider_season": 6,
                    "start_episode": 2,
                    "end_episode": 12,
                    "anilist_id": SECOND_COUR,
                    "absolute_offset": 1,
                },
            ],
        )
        assert session.scalar(select(func.count(TitleCour.id))) == 2

        session.delete(title)
        session.commit()

        assert session.scalar(select(func.count(TitleCour.id))) == 0
