from __future__ import annotations

from collections.abc import Callable

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.models import Title
from daku.services.metadata import anilist, cinemeta


def _app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def test_series_detail_returns_ordered_episodes(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    url = cinemeta.meta_url("series", "tt0944947")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(
            return_value=httpx.Response(200, json=load_fixture("cinemeta_meta_series.json"))
        )
        with _app_client(settings) as client:
            response = client.get("/api/metadata/series/tt0944947")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cinemeta"
    assert payload["source_id"] == "tt0944947"
    assert payload["kind"] == "series"
    assert payload["stale"] is False
    assert [episode["label"] for episode in payload["episodes"]] == [
        "S01E01",
        "S01E02",
        "S02E01",
    ]


def test_detail_rejects_non_imdb_id(settings: Settings) -> None:
    with _app_client(settings) as client:
        response = client.get("/api/metadata/series/not-an-id")

    assert response.status_code == 422


def test_detail_returns_502_when_unavailable(settings: Settings) -> None:
    url = cinemeta.meta_url("movie", "tt1234567")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(500))
        with _app_client(settings) as client:
            response = client.get("/api/metadata/movie/tt1234567")

    assert response.status_code == 502
    assert response.json()["code"] == "METADATA_UNAVAILABLE"


def _anime_title(client: TestClient, anilist_id: int = 190327) -> str:
    from daku.models import Title
    from daku.utils import new_id, utc_now_iso

    with client.app.state.session_factory() as session:
        title = Title(
            id=new_id(),
            kind="anime",
            title="Steel Ball Run",
            anilist_id=anilist_id,
            metadata_source="anilist",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.commit()
        return title.id


def test_identity_patch_rejects_a_manga_anilist_id_before_commit(
    settings: Settings,
) -> None:
    from daku.models import TitleOverride

    with _app_client(settings) as client:
        title_id = _anime_title(client)
        with respx.mock(assert_all_called=False) as mock:
            mock.post(anilist.ENDPOINT).mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"Media": None}}),
                    httpx.Response(
                        200,
                        json={
                            "data": {
                                "Media": {
                                    "id": 31706,
                                    "type": "MANGA",
                                    "format": "MANGA",
                                    "title": {"romaji": "JoJo no Kimyou na Bouken: Steel Ball Run"},
                                }
                            }
                        },
                    ),
                ]
            )
            response = client.patch(f"/api/titles/{title_id}/identity", json={"anilist_id": 31706})

        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] == "ANILIST_NOT_ANIME"
        assert payload["detail"]["type"] == "MANGA"
        with client.app.state.session_factory() as session:
            assert session.query(TitleOverride).count() == 0
            title = session.get(Title, title_id)
            assert title is not None
            assert title.anilist_id == 190327


def test_identity_patch_fetches_a_valid_anime_once_before_commit(
    settings: Settings,
) -> None:
    with _app_client(settings) as client:
        title_id = _anime_title(client, anilist_id=1)
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(anilist.ENDPOINT).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "Media": {
                                "id": 190327,
                                "type": "ANIME",
                                "format": "ONA",
                                "episodes": 1,
                                "seasonYear": 2026,
                                "title": {
                                    "romaji": "JoJo no Kimyou na Bouken: Steel Ball Run - 1st STAGE"
                                },
                            }
                        }
                    },
                )
            )
            response = client.patch(f"/api/titles/{title_id}/identity", json={"anilist_id": 190327})

        assert response.status_code == 200, response.text
        assert response.json()["anilist_id"] == 190327
        assert route.call_count == 1
        with client.app.state.session_factory() as session:
            title = session.get(Title, title_id)
            assert title is not None
            assert title.anilist_id == 190327


def _library_title(client: TestClient, name: str) -> str:
    from daku.utils import new_id, utc_now_iso

    with client.app.state.session_factory() as session:
        title = Title(
            id=new_id(),
            kind="movie",
            title=name,
            metadata_source="cinemeta",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.commit()
        return title.id


def test_library_list_exposes_file_count_and_has_files(settings: Settings, tmp_path) -> None:
    from daku.repositories import media_files as media_files_repo

    with _app_client(settings) as client:
        with_files = _library_title(client, "With Files")
        without_files = _library_title(client, "Without Files")
        video = tmp_path / "film.mkv"
        video.write_bytes(b"x" * 8)
        extra = tmp_path / "sample.mkv"
        extra.write_bytes(b"x" * 4)
        with client.app.state.session_factory() as session:
            for path, size in ((video, 8), (extra, 4)):
                media_files_repo.create_media_file(
                    session,
                    title_id=with_files,
                    src_path=str(path),
                    size_bytes=size,
                )

        response = client.get("/api/titles")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[with_files]["file_count"] == 2
    assert items[with_files]["has_files"] is True
    assert items[without_files]["file_count"] == 0
    assert items[without_files]["has_files"] is False
    # Availability stays derived from present files; has_files is the library axis.
    assert items[with_files]["availability"] == "complete"


def test_library_list_has_files_filter(settings: Settings, tmp_path) -> None:
    from daku.repositories import media_files as media_files_repo

    with _app_client(settings) as client:
        with_files = _library_title(client, "With Files")
        without_files = _library_title(client, "Without Files")
        video = tmp_path / "film.mkv"
        video.write_bytes(b"x" * 8)
        with client.app.state.session_factory() as session:
            media_files_repo.create_media_file(
                session, title_id=with_files, src_path=str(video), size_bytes=8
            )

        filtered = client.get("/api/titles", params={"has_files": "true"})
        excluded = client.get("/api/titles", params={"has_files": "false"})

    assert [item["id"] for item in filtered.json()["items"]] == [with_files]
    assert [item["id"] for item in excluded.json()["items"]] == [without_files]
