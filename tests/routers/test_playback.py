from __future__ import annotations

from fastapi.testclient import TestClient

from daku.config import Settings
from daku.models import Title
from daku.repositories import media_files as media_files_repo
from daku.utils import new_id, utc_now_iso


def _movie_file(client: TestClient, settings: Settings) -> tuple[str, str]:
    """Create a title plus one present media file; return ``(title_id, media_id)``."""

    settings.movies_dir.mkdir(parents=True, exist_ok=True)
    path = settings.movies_dir / "Progress.mkv"
    path.write_bytes(b"x" * 32)

    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        title = Title(
            id=new_id(),
            kind="movie",
            title="Progress",
            metadata_source="cinemeta",
            added_at=utc_now_iso(),
        )
        session.add(title)
        session.commit()
        row = media_files_repo.create_media_file(
            session,
            title_id=title.id,
            src_path=str(path),
            size_bytes=32,
            is_primary=True,
        )
        return title.id, row.id


def test_files_payload_includes_persisted_playback(client: TestClient, settings: Settings) -> None:
    title_id, media_id = _movie_file(client, settings)

    before = client.get(f"/api/titles/{title_id}/files").json()
    assert before["items"][0]["playback"] is None

    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        media_files_repo.set_playback(session, media_id, 120.0, 600.0)

    item = client.get(f"/api/titles/{title_id}/files").json()["items"][0]
    assert item["playback"]["position_s"] == 120.0
    assert item["playback"]["duration_s"] == 600.0
    assert item["playback"]["updated_at"] is not None


def test_playback_endpoint_is_null_when_idle(client: TestClient) -> None:
    assert client.get("/api/media-files/playback").json() == {"active": None}


def test_playback_endpoint_reports_the_live_position(client: TestClient) -> None:
    live = {
        "media_file_id": "media-1",
        "position_s": 42.5,
        "duration_s": 600.0,
        "playing": True,
        "updated_at": "2026-11-01T10:00:00Z",
    }

    class StubPlayer:
        def current_progress(self) -> dict[str, object]:
            return live

        async def aclose(self) -> None:
            return None

    client.app.state.mpv_player = StubPlayer()

    assert client.get("/api/media-files/playback").json() == {"active": live}


def test_set_playback_ignores_unknown_files(client: TestClient) -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        assert media_files_repo.set_playback(session, "missing", 1.0, 2.0) is None


def test_playback_duration_falls_back_to_the_probed_duration(
    client: TestClient, settings: Settings
) -> None:
    title_id, media_id = _movie_file(client, settings)

    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        row = media_files_repo.get_media_file(session, media_id)
        assert row is not None
        row.duration_s = 7200.0
        media_files_repo.set_playback(session, media_id, 10.0, None)

    item = client.get(f"/api/titles/{title_id}/files").json()["items"][0]
    assert item["playback"]["duration_s"] == 7200.0
