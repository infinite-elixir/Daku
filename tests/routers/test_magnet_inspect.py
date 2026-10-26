from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from daku.models import Release
from daku.repositories import releases as releases_repo
from daku.repositories import titles as titles_repo
from daku.services.torrents.probe import (
    InvalidMagnet,
    ProbeBusy,
    ProbeFailed,
    ProbeTimeout,
    ProbeUnavailable,
    TorrentFile,
    TorrentMetadata,
)


def metadata() -> TorrentMetadata:
    return TorrentMetadata(
        info_hash="a" * 40,
        name="Season Pack",
        total_size_bytes=3_000,
        piece_length=262144,
        private=False,
        files=[
            TorrentFile(index=0, path="Season 01/Episode 01.mkv", size_bytes=1_000),
            TorrentFile(index=1, path="Season 01/Episode 02.mkv", size_bytes=2_000),
        ],
        fetched_at="2026-10-26T00:00:00Z",
    )


class StubProbe:
    def __init__(
        self,
        *,
        result: TorrentMetadata | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or metadata()
        self.error = error
        self.calls: list[str] = []

    async def probe(self, magnet: str) -> TorrentMetadata:
        self.calls.append(magnet)
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self) -> None:  # pragma: no cover - lifespan uses the real service
        return None


def install(client: TestClient, stub: StubProbe) -> StubProbe:
    client.app.state.probe = stub  # type: ignore[attr-defined]
    return stub


def test_inspect_returns_the_file_list(client: TestClient) -> None:
    stub = install(client, StubProbe())
    magnet = f"magnet:?xt=urn:btih:{'a' * 40}"

    response = client.post("/api/magnets/inspect", json={"magnet": magnet})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "probe"
    assert payload["cached"] is False
    assert payload["total_size_bytes"] == 3_000
    assert [item["path"] for item in payload["files"]] == [
        "Season 01/Episode 01.mkv",
        "Season 01/Episode 02.mkv",
    ]
    assert stub.calls == [magnet]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidMagnet("bad magnet"), 400, "INVALID_MAGNET"),
        (ProbeUnavailable("missing"), 503, "PROBE_UNAVAILABLE"),
        (ProbeTimeout("slow"), 504, "PROBE_TIMEOUT"),
        (ProbeBusy("full"), 429, "PROBE_BUSY"),
        (ProbeFailed("payload"), 502, "PROBE_FAILED"),
    ],
)
def test_inspect_maps_stable_errors(
    client: TestClient, error: Exception, status: int, code: str
) -> None:
    install(client, StubProbe(error=error))

    response = client.post(
        "/api/magnets/inspect", json={"magnet": f"magnet:?xt=urn:btih:{'b' * 40}"}
    )

    assert response.status_code == status
    assert response.json()["code"] == code


def test_missing_libtorrent_explains_the_optional_extra(client: TestClient) -> None:
    install(client, StubProbe(error=ProbeUnavailable("missing")))

    response = client.post(
        "/api/magnets/inspect", json={"magnet": f"magnet:?xt=urn:btih:{'b' * 40}"}
    )

    assert response.status_code == 503
    assert "libtorrent extra" in response.json()["message"]


def test_release_files_probes_a_stored_release_without_writing(
    client: TestClient,
) -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    magnet = f"magnet:?xt=urn:btih:{'c' * 40}&dn=Pack"
    with factory() as session:
        title = titles_repo.create_title(session, kind="series", title="Example")
        release = releases_repo.create_release(
            session,
            title_id=title.id,
            info_hash="c" * 40,
            magnet=magnet,
            filename="Pack",
            is_saved=True,
        )
        release_id = release.id
        before = session.query(Release).count()

    stub = install(client, StubProbe())
    response = client.get(f"/api/releases/{release_id}/files")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["release_id"] == release_id
    assert payload["source"] == "probe"
    assert len(payload["files"]) == 2
    assert stub.calls == [magnet]

    with factory() as session:
        assert session.query(Release).count() == before
        stored = session.get(Release, release_id)
        assert stored is not None
        assert stored.magnet == magnet
        assert stored.is_saved is True


def test_release_files_rejects_a_release_without_a_magnet(client: TestClient) -> None:
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as session:
        title = titles_repo.create_title(session, kind="movie", title="No magnet")
        release = releases_repo.create_release(session, title_id=title.id, info_hash="d" * 40)
        release_id = release.id

    stub = install(client, StubProbe())
    response = client.get(f"/api/releases/{release_id}/files")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_MAGNET"
    assert stub.calls == []


def test_release_files_unknown_release_is_404(client: TestClient) -> None:
    install(client, StubProbe())
    response = client.get("/api/releases/nope/files")

    assert response.status_code == 404
    assert response.json()["code"] == "RELEASE_NOT_FOUND"


def test_inspect_rejects_an_empty_body(client: TestClient) -> None:
    response = client.post("/api/magnets/inspect", json={"magnet": ""})

    assert response.status_code == 422


def test_probe_unavailable_when_the_service_is_missing(client: TestClient) -> None:
    client.app.state.probe = None  # type: ignore[attr-defined]
    response = client.post(
        "/api/magnets/inspect", json={"magnet": f"magnet:?xt=urn:btih:{'e' * 40}"}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "PROBE_UNAVAILABLE"
