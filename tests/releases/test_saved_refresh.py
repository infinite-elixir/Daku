from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from daku.app import create_app
from daku.config import Settings
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.releases.refresh import RefreshThrottle
from daku.services.torrents.torrentio import build_stream_url

HASH = "1111111111111111111111111111111111111111"
UNKNOWN_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
STREAM_URL = build_stream_url("", "movie", "tt0156887")


def _client(settings: Settings) -> TestClient:
    plain = settings.model_copy(update={"torrentio_config": ""})
    return TestClient(create_app(plain, configure_logs=False))


def _fixture(load_fixture: Callable[[str], object]) -> dict[str, Any]:
    payload = load_fixture("torrentio_big_buck_bunny.json")
    assert isinstance(payload, dict)
    return payload


def _saved_release(
    client: TestClient,
    *,
    info_hash: str = HASH,
    seeders: int | None = 1,
    provider: str = "YTS",
    resolved: str = "torrentio",
) -> tuple[str, str]:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        title = titles_repo.create_title(
            session, kind="movie", title="Big Buck Bunny", year=2008, imdb_id="tt0156887"
        )
        row = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=info_hash,
            magnet=f"magnet:?xt=urn:btih:{info_hash}&dn=bunny.mkv",
            filename="bunny.mkv",
            quality="1080p",
            seeders=seeders,
            provider=provider,
            metadata_json=json.dumps({"resolved_from": resolved, "source": "BluRay"}),
            is_saved=True,
        )
        return title.id, row.id


def test_refresh_updates_seeders_and_checked_at(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with _client(settings) as client:
        title_id, release_id = _saved_release(client)
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(STREAM_URL).mock(
                return_value=httpx.Response(200, json=_fixture(load_fixture))
            )
            response = client.get(f"/api/titles/{title_id}/releases/saved")

        assert response.status_code == 200, response.text
        assert route.called
        body = response.json()
        assert body["refresh"]["found"] == 1
        item = body["releases"][0]
        assert item["id"] == release_id
        assert item["seeders"] == 120
        assert item["last_seen_seeders"] == 120
        assert item["last_checked_at"] is not None
        assert item["miss_count"] == 0
        assert item["dead"] is False


def test_fetch_failure_keeps_previous_seeders(
    settings: Settings,
) -> None:
    with _client(settings) as client:
        title_id, release_id = _saved_release(client, seeders=5)
        with respx.mock(assert_all_called=False) as mock:
            mock.get(STREAM_URL).mock(return_value=httpx.Response(503))
            response = client.get(f"/api/titles/{title_id}/releases/saved")

        assert response.status_code == 200
        body = response.json()
        item = body["releases"][0]
        assert item["seeders"] == 5
        assert item["miss_count"] == 0
        assert item["dead"] is False
        assert body["refresh"]["reason"] is not None

        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            row = releases_repo.get_release(session, release_id)
            assert row is not None
            assert row.seeders == 5


def test_three_misses_mark_dead_and_reappearance_resets(settings: Settings) -> None:
    with _client(settings) as client:
        _title_id, release_id = _saved_release(client, info_hash=UNKNOWN_HASH, seeders=3)

        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(STREAM_URL)
            route.mock(return_value=httpx.Response(200, json={"streams": []}))
            for _ in range(3):
                body = client.post(f"/api/releases/{release_id}/refresh").json()
                assert body["release"]["dead"] is (body["release"]["miss_count"] >= 3)

        assert body["release"]["miss_count"] == 3
        assert body["release"]["dead"] is True
        assert body["release"]["seeders"] == 3  # never zeroed by a miss

        # Reappearance resets the counter and clears the badge.
        client.app.state.releases_cache.clear()  # type: ignore[attr-defined]
        with respx.mock(assert_all_called=False) as mock:
            mock.get(STREAM_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "streams": [
                            {
                                "infoHash": UNKNOWN_HASH.upper(),
                                "title": "1080p\n👤 42 💾 1.5 GB ⚙️ ThePirateBay",
                                "behaviorHints": {"filename": "bunny.mkv"},
                            }
                        ]
                    },
                )
            )
            body = client.post(f"/api/releases/{release_id}/refresh").json()

        assert body["found"] is True
        assert body["release"]["seeders"] == 42
        assert body["release"]["miss_count"] == 0
        assert body["release"]["dead"] is False

        # The row is kept, not deleted.
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory() as session:
            assert releases_repo.get_release(session, release_id) is not None


def test_manual_magnet_is_not_marked_dead(settings: Settings) -> None:
    with _client(settings) as client:
        _, release_id = _saved_release(client, provider="manual", resolved="manual")

        with respx.mock(assert_all_called=False) as mock:
            mock.get(STREAM_URL).mock(return_value=httpx.Response(200, json={"streams": []}))
            for _ in range(3):
                body = client.post(f"/api/releases/{release_id}/refresh").json()

        assert body["release"]["miss_count"] == 0
        assert body["release"]["dead"] is False


def test_saved_tab_refresh_is_throttled_per_release(
    settings: Settings, load_fixture: Callable[[str], object]
) -> None:
    with _client(settings) as client:
        title_id, _ = _saved_release(client)
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(STREAM_URL).mock(
                return_value=httpx.Response(200, json=_fixture(load_fixture))
            )
            first = client.get(f"/api/titles/{title_id}/releases/saved")
            assert first.json()["refresh"]["attempted"] == 1

            # Force the payload cache stale: only the throttle can prevent a second call.
            client.app.state.releases_cache.ttl_seconds = -1  # type: ignore[attr-defined]
            second = client.get(f"/api/titles/{title_id}/releases/saved")

        assert route.call_count == 1
        assert second.json()["refresh"]["attempted"] == 0
        assert second.json()["releases"][0]["seeders"] == 120


def test_throttle_allows_one_attempt_per_interval() -> None:
    now = [100.0]
    throttle = RefreshThrottle(600, clock=lambda: now[0])
    assert throttle.due("release") is True
    throttle.mark("release")
    assert throttle.due("release") is False

    now[0] = 699.9
    assert throttle.due("release") is False
    now[0] = 700.0
    assert throttle.due("release") is True
