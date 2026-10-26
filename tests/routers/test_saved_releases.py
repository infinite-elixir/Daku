from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import episodes as episodes_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.torrents.torrentio import build_stream_url

T007_KEYS = {
    "info_hash",
    "filename",
    "quality",
    "size_bytes",
    "seeders",
    "provider",
    "magnet",
    "metadata_json",
}


def _client(settings: Settings) -> TestClient:
    plain = settings.model_copy(update={"torrentio_config": "", "providers": []})
    return TestClient(create_app(plain, configure_logs=False))


def _session(client: TestClient) -> Any:
    return client.app.state.session_factory  # type: ignore[attr-defined]


def _seed(client: TestClient) -> tuple[str, dict[str, str]]:
    """One series with two saved releases (one dead) and one recent unsaved row."""

    factory = _session(client)
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Show")
        ep1 = episodes_repo.create_episode(session, title_id=title.id, season=1, episode=1)
        ep2 = episodes_repo.create_episode(session, title_id=title.id, season=1, episode=2)
        saved = releases_repo.create_release(
            session,
            title_id=title.id,
            episode_id=ep1.id,
            info_hash="a" * 40,
            magnet="magnet:?xt=urn:btih:" + "a" * 40,
            filename="Show.S01E01.1080p.mkv",
            quality="1080p",
            seeders=12,
            provider="YTS",
            metadata_json='{"resolved_from": "torrentio", "source": "WEB-DL"}',
            is_saved=True,
        )
        dead = releases_repo.create_release(
            session,
            title_id=title.id,
            episode_id=ep2.id,
            info_hash="b" * 40,
            magnet="magnet:?xt=urn:btih:" + "b" * 40,
            filename="Show.S01E02.2160p.mkv",
            quality="2160p",
            seeders=0,
            provider="manual",
            metadata_json='{"resolved_from": "manual"}',
            is_saved=True,
        )
        releases_repo.record_miss(session, dead.id)
        releases_repo.record_miss(session, dead.id)
        releases_repo.record_miss(session, dead.id)
        recent = releases_repo.create_release(
            session,
            title_id=title.id,
            episode_id=ep1.id,
            info_hash="c" * 40,
            magnet="magnet:?xt=urn:btih:" + "c" * 40,
            filename="Show.S01E01.720p.mkv",
            quality="720p",
        )
        return title.id, {
            "ep1": ep1.id,
            "ep2": ep2.id,
            "saved": saved.id,
            "dead": dead.id,
            "recent": recent.id,
        }


def test_saved_tab_only_lists_saved_rows(settings: Settings) -> None:
    with _client(settings) as client:
        title_id, ids = _seed(client)
        response = client.get(f"/api/titles/{title_id}/releases/saved")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    returned = {item["id"] for item in body["releases"]}
    assert returned == {ids["saved"], ids["dead"]}
    assert ids["recent"] not in returned


def test_saved_tab_shape_has_t007_keys(settings: Settings) -> None:
    with _client(settings) as client:
        title_id, _ = _seed(client)
        body = client.get(f"/api/titles/{title_id}/releases/saved").json()

    item = body["releases"][0]
    assert set(item) >= T007_KEYS
    assert {"id", "is_saved", "saved_at", "last_checked_at", "dead", "miss_count"} <= set(item)
    assert item["metadata_json"]["resolved_from"] in ("torrentio", "manual")


def test_saved_tab_filters_by_quality_and_dead(settings: Settings) -> None:
    with _client(settings) as client:
        title_id, ids = _seed(client)

        high = client.get(
            f"/api/titles/{title_id}/releases/saved", params={"quality": "2160p"}
        ).json()
        assert [item["id"] for item in high["releases"]] == [ids["dead"]]

        dead = client.get(f"/api/titles/{title_id}/releases/saved", params={"dead": "true"}).json()
        assert [item["id"] for item in dead["releases"]] == [ids["dead"]]
        assert dead["releases"][0]["dead"] is True

        alive = client.get(
            f"/api/titles/{title_id}/releases/saved", params={"dead": "false"}
        ).json()
        assert [item["id"] for item in alive["releases"]] == [ids["saved"]]


def test_saved_tab_filters_by_episode(settings: Settings) -> None:
    with _client(settings) as client:
        title_id, ids = _seed(client)
        body = client.get(
            f"/api/titles/{title_id}/releases/saved",
            params={"episode_id": ids["ep2"]},
        ).json()

    assert [item["id"] for item in body["releases"]] == [ids["dead"]]


def test_title_detail_reports_saved_count_without_files(settings: Settings) -> None:
    with _client(settings) as client:
        title_id, _ = _seed(client)
        payload = client.get(f"/api/titles/{title_id}").json()

    assert payload["availability"] == "none"
    assert payload["saved_release_count"] == 2


def test_search_results_are_annotated_with_saved_state(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    payload = load_fixture("torrentio_big_buck_bunny.json")
    assert isinstance(payload, dict)
    url = build_stream_url("", "series", "tt0944947:1:1")
    hash_2160 = "1111111111111111111111111111111111111111"

    with _client(settings) as client:
        factory = _session(client)
        with factory() as session:
            title = titles_repo.create_title(
                session, kind="series", title="Game of Thrones", imdb_id="tt0944947"
            )
            episode = episodes_repo.create_episode(session, title_id=title.id, season=1, episode=1)
            release = releases_repo.create_release(
                session,
                title_id=title.id,
                episode_id=episode.id,
                info_hash=hash_2160,
                magnet=f"magnet:?xt=urn:btih:{hash_2160}",
                filename="Show.2160p.mkv",
                quality="2160p",
                provider="YTS",
                metadata_json='{"resolved_from": "torrentio"}',
                is_saved=True,
            )
            title_id = title.id
            release_id = release.id

        with respx.mock(assert_all_called=False) as mock:
            mock.get(url).mock(return_value=httpx.Response(200, json=payload))
            body = client.get(
                f"/api/titles/{title_id}/releases",
                params={"season": 1, "episode": 1, "quality_filter": ""},
            ).json()

    matched = next(item for item in body["releases"] if item["info_hash"] == hash_2160)
    assert matched["release_id"] == release_id
    assert matched["is_saved"] is True
    assert matched["dead"] is False

    other = next(item for item in body["releases"] if item["info_hash"] != hash_2160)
    assert other["release_id"] is None
    assert other["is_saved"] is False
