"""Artwork fallback during metadata refresh (T-052)."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session, sessionmaker

from daku.config import Settings
from daku.models import Title
from daku.services.metadata import anilist, artwork
from daku.services.metadata.cache import TTLCache
from daku.services.metadata.client import make_client
from daku.services.metadata.refresh import refresh_title
from daku.utils import new_id, utc_now_iso

ANILIST_ID = 120377
DEAD_POSTER = "https://images.metahub.space/poster/small/tt38268282/img"
DEAD_BANNER = "https://images.metahub.space/background/medium/tt38268282/img"
LIVE_COVER = "https://s4.anilist.co/file/anilistcdn/media/anime/cover/live.jpg"


@pytest.fixture(autouse=True)
def _clear_artwork_cache() -> None:
    artwork.clear_cache()


def _detail(poster: str | None, banner: str | None = None) -> dict[str, object]:
    return {
        "data": {
            "Media": {
                "id": ANILIST_ID,
                "type": "ANIME",
                "format": "TV",
                "episodes": 10,
                "seasonYear": 2022,
                "title": {"romaji": "Cyberpunk: Edgerunners"},
                "coverImage": {"extraLarge": poster} if poster else None,
                "bannerImage": banner,
                "relations": {"edges": []},
            }
        }
    }


def _title(session: Session) -> Title:
    title = Title(
        id=new_id(),
        kind="anime",
        title="Cyberpunk: Edgerunners",
        anilist_id=ANILIST_ID,
        poster_url=DEAD_POSTER,
        banner_url=DEAD_BANNER,
        metadata_source="anilist",
        added_at=utc_now_iso(),
    )
    session.add(title)
    session.commit()
    return title


@pytest.mark.asyncio
async def test_a_dead_stored_poster_is_cleared_and_reported(
    db: tuple[object, sessionmaker[Session]], settings: Settings
) -> None:
    _engine, factory = db
    with factory() as session:
        title = _title(session)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(anilist.ENDPOINT).mock(return_value=httpx.Response(200, json=_detail(None)))
            mock.head(DEAD_POSTER).mock(return_value=httpx.Response(404))
            mock.head(DEAD_BANNER).mock(return_value=httpx.Response(410))
            async with make_client() as client:
                result = await refresh_title(
                    session,
                    title,
                    client=client,
                    cache=TTLCache(),
                    settings=settings,
                )

        assert result["partial"] is True
        assert result["refresh_error"]["code"] == "ARTWORK_UNAVAILABLE"
        assert title.poster_url is None
        assert title.banner_url is None


@pytest.mark.asyncio
async def test_the_anilist_cover_replaces_dead_stored_artwork(
    db: tuple[object, sessionmaker[Session]], settings: Settings
) -> None:
    _engine, factory = db
    with factory() as session:
        title = _title(session)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(anilist.ENDPOINT).mock(
                return_value=httpx.Response(200, json=_detail(LIVE_COVER))
            )
            mock.head(DEAD_POSTER).mock(return_value=httpx.Response(404))
            mock.head(DEAD_BANNER).mock(return_value=httpx.Response(404))
            mock.head(LIVE_COVER).mock(return_value=httpx.Response(200))
            async with make_client() as client:
                result = await refresh_title(
                    session,
                    title,
                    client=client,
                    cache=TTLCache(),
                    settings=settings,
                )

        assert result["partial"] is False
        assert result["refresh_error"] is None
        assert title.poster_url == LIVE_COVER
        assert title.banner_url is None
