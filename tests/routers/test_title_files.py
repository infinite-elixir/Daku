from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from daku.app import create_app
from daku.config import Settings
from daku.models import Episode, Title
from daku.repositories import media_files as media_files_repo
from daku.utils import new_id, utc_now_iso


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, configure_logs=False)) as test_client:
        yield test_client


def _session(client: TestClient) -> Session:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory()


def _title(session: Session, *, kind: str = "series") -> Title:
    title = Title(
        id=new_id(),
        kind=kind,
        title="Cyberpunk: Edgerunners",
        year=2022,
        metadata_source="cinemeta",
        added_at=utc_now_iso(),
    )
    session.add(title)
    session.commit()
    return title


def _episode(session: Session, title: Title, season: int, episode: int, name: str) -> Episode:
    row = Episode(
        id=new_id(),
        title_id=title.id,
        season=season,
        episode=episode,
        name=name,
    )
    session.add(row)
    session.commit()
    return row


def _real_file(tmp_path: Path, name: str, size: int = 16) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def test_lists_only_existing_files_with_season_aggregates(
    client: TestClient, tmp_path: Path
) -> None:
    with _session(client) as session:
        title = _title(session)
        ep1 = _episode(session, title, 1, 3, "The Jacket")
        ep2 = _episode(session, title, 1, 1, "Let You Down")
        ep3 = _episode(session, title, 2, 1, "Girl on Fire")
        files = [
            _real_file(tmp_path, "s02e01.mkv", 32),
            _real_file(tmp_path, "s01e03.mkv", 16),
            _real_file(tmp_path, "s01e01.mkv", 8),
            _real_file(tmp_path, "sample.mkv", 4),
        ]
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=ep3.id,
            src_path=str(files[0]),
            size_bytes=32,
        )
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=ep1.id,
            src_path=str(files[1]),
            size_bytes=16,
        )
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=ep2.id,
            src_path=str(files[2]),
            size_bytes=8,
        )
        # Extras: no episode, so it carries has_episode=false and sorts last.
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            src_path=str(files[3]),
            size_bytes=4,
        )
        # A row whose file vanished must not be reported.
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=ep2.id,
            src_path=str(tmp_path / "gone.mkv"),
            size_bytes=8,
        )

    response = client.get(f"/api/titles/{title.id}/files")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title_id"] == title.id
    assert payload["file_count"] == 4
    assert payload["total_size_bytes"] == 60
    assert payload["seasons"] == [
        {"library_season": 1, "file_count": 2, "total_size_bytes": 24},
        {"library_season": 2, "file_count": 1, "total_size_bytes": 32},
    ]
    assert [item["file_name"] for item in payload["items"]] == [
        "s01e01.mkv",
        "s01e03.mkv",
        "s02e01.mkv",
        "sample.mkv",
    ]
    assert [item["episode_label"] for item in payload["items"][:3]] == [
        "S01E01",
        "S01E03",
        "S02E01",
    ]
    extras = payload["items"][3]
    assert extras["has_episode"] is False
    assert extras["episode_label"] is None
    assert extras["season"] is None
    assert payload["items"][0]["episode_name"] == "Let You Down"
    assert payload["items"][0]["size_bytes"] == 8


def test_prefers_library_path_when_reporting_file_name(client: TestClient, tmp_path: Path) -> None:
    with _session(client) as session:
        title = _title(session, kind="movie")
        episode = _episode(session, title, 1, 1, "Feature")
        source = _real_file(tmp_path, "download-copy.mkv", 8)
        library = _real_file(tmp_path, "Big Buck Bunny (2008).mkv", 8)
        media_files_repo.create_media_file(
            session,
            title_id=title.id,
            episode_id=episode.id,
            src_path=str(source),
            lib_path=str(library),
            size_bytes=8,
        )

    response = client.get(f"/api/titles/{title.id}/files")

    assert response.status_code == 200
    assert response.json()["items"][0]["file_name"] == "Big Buck Bunny (2008).mkv"


def test_empty_title_returns_zero_counts(client: TestClient) -> None:
    with _session(client) as session:
        title = _title(session)

    response = client.get(f"/api/titles/{title.id}/files")

    assert response.status_code == 200
    assert response.json() == {
        "title_id": title.id,
        "file_count": 0,
        "total_size_bytes": 0,
        "seasons": [],
        "items": [],
    }


def test_unknown_title_is_404(client: TestClient) -> None:
    response = client.get("/api/titles/does-not-exist/files")

    assert response.status_code == 404
    assert response.json()["code"] == "TITLE_NOT_FOUND"
