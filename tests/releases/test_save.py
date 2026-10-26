from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from daku.app import create_app
from daku.config import Settings
from daku.models import Download, Release
from daku.repositories import episodes as episodes_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo

HEX_HASH = "abcdef0123456789abcdef0123456789abcdef01"
MAGNET = (
    f"magnet:?xt=urn:btih:{HEX_HASH}"
    "&dn=Perfect.Blue.1997.1080p.BluRay.x264-GROUP.mkv"
    "&tr=udp%3A%2F%2Ftracker.one%3A80%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.two%3A6969%2Fannounce"
)


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False))


def _add_title(client: TestClient, *, title: str = "Perfect Blue") -> str:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = titles_repo.create_title(session, kind="movie", title=title, year=1997)
        return row.id


def _count(client: TestClient, model: type[object]) -> int:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_saving_creates_no_download_and_no_files(settings: Settings, tmp_path: Path) -> None:
    with _client(settings) as client:
        title_id = _add_title(client)
        response = client.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["info_hash"] == HEX_HASH
        assert payload["filename"] == "Perfect.Blue.1997.1080p.BluRay.x264-GROUP.mkv"
        assert payload["provider"] == "manual"
        assert payload["resolved_from"] == "manual"
        assert payload["is_saved"] is True
        assert payload["saved_at"]
        assert payload["magnet"].count("&tr=") == 2
        metadata = payload["metadata_json"]
        assert metadata["source"] == "BluRay"
        assert metadata["video_codec"] == "x264"
        assert metadata["resolved_from"] == "manual"

        assert _count(client, Release) == 1
        assert _count(client, Download) == 0
        assert not settings.downloads_dir.exists()


def test_same_hash_saved_twice_updates_one_row(settings: Settings) -> None:
    with _client(settings) as client:
        title_id = _add_title(client)
        body = {"magnet": MAGNET, "title_id": title_id, "quality": "1080p"}
        first = client.post("/api/releases", json=body)
        assert first.status_code == 201
        saved_at = first.json()["saved_at"]

        second = client.post(
            "/api/releases",
            json={
                "magnet": MAGNET.replace(HEX_HASH, HEX_HASH.upper()),
                "title_id": title_id,
                "quality": "720p",
                "seeders": 9,
            },
        )

        assert second.status_code == 200
        payload = second.json()
        assert payload["id"] == first.json()["id"]
        assert payload["is_saved"] is True
        assert payload["saved_at"] >= saved_at
        assert payload["quality"] == "720p"
        assert payload["seeders"] == 9
        assert _count(client, Release) == 1


def test_save_endpoint_writes_snapshot_and_unsave_keeps_row(settings: Settings) -> None:
    with _client(settings) as client:
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        title_id = _add_title(client)
        with factory() as session:
            row = releases_repo.create_release(
                session,
                title_id=title_id,
                info_hash=HEX_HASH,
                magnet=MAGNET,
                filename="Perfect.Blue.1997.1080p.BluRay.x264-GROUP.mkv",
                quality="1080p",
            )
            release_id = row.id

        saved = client.post(f"/api/releases/{release_id}/save")
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["is_saved"] is True
        assert payload["metadata_json"]["resolved_from"] == "torrentio"
        assert payload["metadata_json"]["source"] == "BluRay"

        removed = client.delete(f"/api/releases/{release_id}/save")
        assert removed.status_code == 200
        assert removed.json()["is_saved"] is False
        assert removed.json()["saved_at"] is None
        assert _count(client, Release) == 1


def test_saved_release_survives_restart(settings: Settings) -> None:
    with _client(settings) as first:
        title_id = _add_title(first)
        created = first.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id})
        assert created.status_code == 201
        release_id = created.json()["id"]

    with _client(settings) as second:
        payload = second.get(f"/api/titles/{title_id}/releases/saved")
        assert payload.status_code == 200, payload.text
        body = payload.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["releases"]] == [release_id]
        assert body["releases"][0]["is_saved"] is True


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("https://example.com/movie.torrent", "magnet"),
        ("magnet:?dn=No.Hash.mkv", "btih"),
        (f"magnet:?xt=urn:btih:{'a' * 39}", "btih"),
        (f"magnet:?xt=urn:btih:{'z' * 40}", "btih"),
    ],
)
def test_invalid_magnet_is_400(settings: Settings, uri: str, message: str) -> None:
    with _client(settings) as client:
        title_id = _add_title(client)
        response = client.post("/api/releases", json={"magnet": uri, "title_id": title_id})

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_MAGNET"
    assert message in response.json()["message"]


def test_unknown_title_is_404(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.post("/api/releases", json={"magnet": MAGNET, "title_id": "nope"})
    assert response.status_code == 404
    assert response.json()["code"] == "TITLE_NOT_FOUND"


def test_episode_must_belong_to_title(settings: Settings) -> None:
    with _client(settings) as client:
        first = _add_title(client, title="Show A")
        second = _add_title(client, title="Show B")
        factory = client.app.state.session_factory  # type: ignore[attr-defined]

        with factory() as session:
            episode = episodes_repo.create_episode(session, title_id=second, season=1, episode=1)
            episode_id = episode.id

        response = client.post(
            "/api/releases",
            json={"magnet": MAGNET, "title_id": first, "episode_id": episode_id},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "EPISODE_NOT_FOUND"


def test_release_payload_keys_include_t007_shape(settings: Settings) -> None:
    with _client(settings) as client:
        title_id = _add_title(client)
        payload = client.post("/api/releases", json={"magnet": MAGNET, "title_id": title_id}).json()

    assert {
        "info_hash",
        "filename",
        "quality",
        "size_bytes",
        "seeders",
        "provider",
        "magnet",
        "metadata_json",
        "id",
        "is_saved",
        "dead",
    } <= set(payload)
