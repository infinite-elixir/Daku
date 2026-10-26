from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from daku.config import Settings
from daku.repositories import downloads as downloads_repo
from daku.repositories import episodes as episodes_repo
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
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
