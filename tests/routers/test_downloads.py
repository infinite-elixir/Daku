from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from daku.config import Settings
from daku.repositories import downloads as downloads_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import media_files as media_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.engines.base import EngineError
from daku.services.torrents.probe import TorrentFile, TorrentMetadata

HASH = "a" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Show.S02.1080p.mkv"
FILES = [f"Show.S02E{number:02d}.1080p.mkv" for number in range(1, 13)]


class FakeProbe:
    def __init__(self, paths: list[str] | None = None) -> None:
        self.paths = paths if paths is not None else FILES
        self.calls = 0

    async def aclose(self) -> None:
        return None

    async def probe(self, _magnet: str) -> TorrentMetadata:
        self.calls += 1
        files = [
            TorrentFile(index=index, path=path, size_bytes=(index + 1) * 100)
            for index, path in enumerate(self.paths)
        ]
        return TorrentMetadata(
            info_hash=HASH,
            name="Show S02",
            total_size_bytes=sum(item.size_bytes for item in files),
            piece_length=262144,
            private=False,
            files=files,
            fetched_at="2026-10-26T00:00:00Z",
        )


class FakeLiveEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[list[int], bool]] = []

    async def set_file_selection(
        self,
        _external_id: str,
        indices: list[int],
        *,
        paths: list[str] | None = None,
        start: bool = True,
    ) -> list[int]:
        self.calls.append((list(indices), start))
        if self.error is not None:
            raise self.error
        return list(indices)


def _install_live(client: TestClient, engine: FakeLiveEngine) -> None:
    client.app.state.qbt_engine = engine  # type: ignore[attr-defined]


def _install(client: TestClient, probe: FakeProbe) -> None:
    client.app.state.probe = probe  # type: ignore[attr-defined]


def _seed(
    client: TestClient, *, kind: str = "series", absolute: int | None = None
) -> tuple[str, str]:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        title = titles_repo.create_title(session, kind=kind, title="Show", imdb_id="tt1234567")
        episode = episodes_repo.create_episode(
            session,
            title_id=title.id,
            season=2,
            episode=5,
            absolute=absolute,
            name="The Episode",
        )
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash=HASH,
            magnet=MAGNET,
            filename="Show.S02.1080p.mkv",
            size_bytes=5_000,
        )
        return release.id, episode.id


def _rows(client: TestClient) -> list[Any]:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        return downloads_repo.list_downloads(session)


def test_explicit_indices_win_and_are_persisted(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    probe = FakeProbe()
    _install(client, probe)
    release_id, _episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "file_indices": [3]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["selected_files"] == [3]
    assert response.json()["match"]["reason"] == "explicit selection"
    rows = _rows(client)
    assert len(rows) == 1
    assert rows[0].selected_files_json == "[3]"
    assert probe.calls == 0


def test_auto_match_selects_only_s02e05(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": episode_id},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["selected_files"] == [4]
    assert body["selection_label"] == "S02E05 only"
    assert body["match"]["reason"].startswith("S02E05")
    row = _rows(client)[0]
    assert row.selected_files_json == "[4]"
    assert "Show.S02E05.1080p.mkv" in (row.file_match_json or "")


def test_ambiguous_match_is_409_and_creates_no_row(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(
        client,
        FakeProbe(["Show.S02E05.1080p.mkv", "Show.S02E05.720p.mkv"]),
    )
    release_id, episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": episode_id},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "FILE_SELECTION_REQUIRED"
    assert {item["path"] for item in body["detail"]["candidates"]} == {
        "Show.S02E05.1080p.mkv",
        "Show.S02E05.720p.mkv",
    }
    assert _rows(client) == []


def test_no_matching_file_is_409_with_the_file_list(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe(["Show.S02E06.mkv", "Show.S02E07.mkv"]))
    release_id, episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": episode_id},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "EPISODE_FILE_NOT_FOUND"
    assert len(body["detail"]["files"]) == 2
    assert _rows(client) == []


def test_pack_requires_allow_pack_and_then_downloads_everything(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, _episode_id = _seed(client)

    refused = client.post("/api/downloads", json={"release_id": release_id})
    assert refused.status_code == 409
    assert refused.json()["code"] == "PACK_SELECTION_REQUIRED"
    assert _rows(client) == []

    allowed = client.post(
        "/api/downloads",
        json={"release_id": release_id, "allow_pack": True},
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["selected_files"] is None
    assert allowed.json()["match"]["pack"] is True
    assert allowed.json()["selection_label"] == "Whole pack"


def test_file_paths_are_mapped_and_win_over_the_matcher(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={
            "release_id": release_id,
            "episode_id": episode_id,
            "file_paths": ["Show.S02E06.1080p.mkv"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["selected_files"] == [5]
    assert body["match"]["reason"] == "explicit selection"
    assert body["selected_paths"] == ["Show.S02E06.1080p.mkv"]


def test_unknown_file_path_is_409(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, _episode_id = _seed(client)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "file_paths": ["Nope.mkv"]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "FILE_NOT_IN_TORRENT"
    assert _rows(client) == []


def test_anime_absolute_match_uses_the_absolute_number(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe(["Show - 17 - The Episode.mkv"]))
    release_id, episode_id = _seed(client, kind="anime", absolute=17)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": episode_id},
    )

    assert response.status_code == 201, response.text
    assert response.json()["selected_files"] == [0]
    assert "absolute 17" in response.json()["match"]["reason"]


def _active_row(client: TestClient, release_id: str) -> str:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="active",
            save_path="/downloads",
            selected_files_json="[4]",
            file_match_json='{"file_paths": ["Show.S02E05.1080p.mkv"]}',
        )
        return row.id


def test_live_reselection_updates_an_active_torrent(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, _episode_id = _seed(client)
    download_id = _active_row(client, release_id)
    engine = FakeLiveEngine()
    _install_live(client, engine)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "file_indices": [5]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "DOWNLOAD_ALREADY_TRACKED"
    assert engine.calls == [([5], True)]
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.get_download(session, download_id)
    assert row is not None
    assert row.selected_files_json == "[5]"


def test_a_rejected_live_reselection_restores_priorities_and_returns_409(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    _install(client, FakeProbe())
    release_id, _episode_id = _seed(client)
    download_id = _active_row(client, release_id)
    engine = FakeLiveEngine(
        error=EngineError("QBT_FILE_SELECTION_FAILED", "priorities were not applied")
    )
    _install_live(client, engine)

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "file_indices": [5]},
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "FILE_SELECTION_FAILED"
    assert "priorities were not applied" in body["message"]
    assert engine.calls == [([5], True), ([4], False)]
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.get_download(session, download_id)
    assert row is not None
    assert row.selected_files_json == "[4]"


def test_a_finished_pack_can_download_the_next_episode(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    _install(client, FakeProbe())
    release_id, first_episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        release = releases_repo.get_release(session, release_id)
        assert release is not None
        second = episodes_repo.create_episode(
            session, title_id=release.title_id, season=2, episode=6, name="Next"
        )
        second_episode_id = second.id
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="complete",
            save_path=str(settings.downloads_dir),
            selected_files_json="[4]",
            file_match_json='{"file_paths": ["Show.S02E05.1080p.mkv"]}',
        )
        download_id = row.id
        media = media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=first_episode_id,
            download_id=row.id,
            src_path=str(settings.downloads_dir / "Show.S02E05.1080p.mkv"),
            is_primary=True,
        )
        first_media_id = media.id

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": second_episode_id},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"] == download_id
    assert body["status"] == "queued"
    assert body["selected_files"] == [5]
    assert body["selection_label"] == "S02E06 only"
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = downloads_repo.list_downloads(session)
        assert [item.id for item in rows] == [download_id]
        assert rows[0].selected_files_json == "[5]"
        # The first episode stays owned by the torrent, so the row can list both.
        kept = media_repo.get_media_file(session, first_media_id)
        assert kept is not None
        assert kept.download_id == download_id
        assert kept.episode_id == first_episode_id
    assert [item["episode_label"] for item in body["downloaded_files"]] == ["S02E05"]


def test_downloading_an_episode_that_is_already_available_is_a_duplicate(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    _install(client, FakeProbe())
    release_id, episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        release = releases_repo.get_release(session, release_id)
        assert release is not None
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="complete",
            save_path=str(settings.downloads_dir),
            selected_files_json="[4]",
            file_match_json='{"file_paths": ["Show.S02E05.1080p.mkv"]}',
        )
        media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=episode_id,
            download_id=row.id,
            src_path=str(settings.downloads_dir / "Show.S02E05.1080p.mkv"),
        )
        download_id = row.id

    response = client.post(
        "/api/downloads",
        json={"release_id": release_id, "episode_id": episode_id},
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "DOWNLOAD_ALREADY_TRACKED"
    assert "already in your library" in body["message"]
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.get_download(session, download_id)
    assert row is not None
    assert row.status == "complete"
    assert row.selected_files_json == "[4]"


def test_downloads_payload_lists_every_episode_the_torrent_downloaded(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    release_id, first_episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        release = releases_repo.get_release(session, release_id)
        assert release is not None
        second = episodes_repo.create_episode(
            session, title_id=release.title_id, season=2, episode=6, name="Next"
        )
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="complete",
            save_path=str(settings.downloads_dir),
            selected_files_json="[5]",
            file_match_json='{"file_paths": ["Show.S02E06.1080p.mkv"]}',
        )
        folder = settings.downloads_dir / "Show.S02"
        media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=first_episode_id,
            download_id=row.id,
            src_path=str(folder / "Show.S02E05.1080p.mkv"),
        )
        media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=second.id,
            download_id=row.id,
            src_path=str(folder / "Show.S02E06.1080p.mkv"),
        )

    response = client.get("/api/downloads")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert [entry["episode_label"] for entry in item["downloaded_files"]] == [
        "S02E05",
        "S02E06",
    ]
    assert [entry["file_name"] for entry in item["downloaded_files"]] == [
        "Show.S02E05.1080p.mkv",
        "Show.S02E06.1080p.mkv",
    ]


def test_downloads_payload_recovers_episodes_detached_before_t042(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    release_id, first_episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        release = releases_repo.get_release(session, release_id)
        assert release is not None
        second = episodes_repo.create_episode(
            session, title_id=release.title_id, season=2, episode=6, name="Next"
        )
        folder = settings.downloads_dir / "Show.S02"
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="complete",
            save_path=str(settings.downloads_dir),
            selected_files_json="[5]",
            file_match_json='{"file_paths": ["Show.S02E06.1080p.mkv"]}',
        )
        # The old requeue path detached the previous episode instead of keeping it.
        media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=first_episode_id,
            download_id=None,
            src_path=str(folder / "Show.S02E05.1080p.mkv"),
        )
        media_repo.create_media_file(
            session,
            title_id=release.title_id,
            episode_id=second.id,
            download_id=row.id,
            src_path=str(folder / "Show.S02E06.1080p.mkv"),
        )

    response = client.get("/api/downloads")

    entries = response.json()["items"][0]["downloaded_files"]
    assert [entry["episode_label"] for entry in entries] == ["S02E05", "S02E06"]


class FakeRemoveEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def remove(self, external_id: str, *, delete_files: bool = False) -> None:
        self.calls.append((external_id, delete_files))
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize(
    "status", ["queued", "active", "paused", "complete", "failed", "cancelled", "available"]
)
def test_remove_download_deletes_any_non_organizing_row(
    app_client: tuple[TestClient, Settings], status: str
) -> None:
    client, _settings = app_client
    release_id, _episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status=status,
            save_path="/downloads",
        )
        download_id = row.id
    engine = FakeRemoveEngine()
    client.app.state.qbt_engine = engine  # type: ignore[attr-defined]

    response = client.delete(f"/api/downloads/{download_id}")

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert engine.calls == [(HASH, False)]
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert downloads_repo.get_download(session, download_id) is None
    events = client.app.state.event_bus._buffer  # type: ignore[attr-defined]
    assert events[-1].type == "download.removed"
    assert events[-1].data == {"id": download_id}


def test_remove_download_forwards_delete_files(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    release_id, _episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="failed",
            save_path="/downloads",
        )
        download_id = row.id
    engine = FakeRemoveEngine()
    client.app.state.qbt_engine = engine  # type: ignore[attr-defined]

    response = client.delete(f"/api/downloads/{download_id}?delete_files=true")

    assert response.status_code == 204, response.text
    assert engine.calls == [(HASH, True)]


def test_removing_an_available_download_keeps_the_library_file(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, settings = app_client
    release_id, _episode_id = _seed(client)
    lib_path = settings.movies_dir / "Show" / "Show.mkv"
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        release = releases_repo.get_release(session, release_id)
        assert release is not None
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="available",
            save_path="/downloads",
        )
        media = media_repo.create_media_file(
            session,
            title_id=release.title_id,
            download_id=row.id,
            src_path=str(settings.downloads_dir / "Show.mkv"),
            lib_path=str(lib_path),
            is_primary=True,
        )
        download_id = row.id
        media_id = media.id
    client.app.state.qbt_engine = FakeRemoveEngine()  # type: ignore[attr-defined]

    response = client.delete(f"/api/downloads/{download_id}?delete_files=true")

    assert response.status_code == 204, response.text
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert downloads_repo.get_download(session, download_id) is None
        kept = media_repo.get_media_file(session, media_id)
    assert kept is not None
    assert kept.download_id is None
    assert kept.lib_path == str(lib_path)


def test_remove_download_returns_404_for_a_missing_row(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client

    response = client.delete("/api/downloads/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "DOWNLOAD_NOT_FOUND"


def test_remove_download_refuses_an_organizing_row(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    release_id, _episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="organizing",
            save_path="/downloads",
        )
        download_id = row.id
    engine = FakeRemoveEngine()
    client.app.state.qbt_engine = engine  # type: ignore[attr-defined]

    response = client.delete(f"/api/downloads/{download_id}")

    assert response.status_code == 409
    assert response.json()["code"] == "DOWNLOAD_BUSY"
    assert engine.calls == []
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert downloads_repo.get_download(session, download_id) is not None


def test_remove_download_keeps_the_row_when_the_engine_fails(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    release_id, _episode_id = _seed(client)
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        row = downloads_repo.create_download(
            session,
            source="torrent",
            engine="qbittorrent",
            external_id=HASH,
            release_id=release_id,
            status="failed",
            save_path="/downloads",
        )
        download_id = row.id
    client.app.state.qbt_engine = FakeRemoveEngine(  # type: ignore[attr-defined]
        error=EngineError("QBT_UNREACHABLE", "qBittorrent is unreachable")
    )

    response = client.delete(f"/api/downloads/{download_id}")

    assert response.status_code == 502
    assert response.json()["code"] == "QBT_UNREACHABLE"
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert downloads_repo.get_download(session, download_id) is not None
