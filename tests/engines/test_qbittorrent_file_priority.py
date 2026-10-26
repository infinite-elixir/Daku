from __future__ import annotations

import httpx
import pytest
import respx

from daku.config import Settings
from daku.services.engines.base import AddRequest, EngineError
from daku.services.engines.qbittorrent import QbittorrentEngine

BASE = "http://127.0.0.1:8080/api/v2"
LOGIN = f"{BASE}/auth/login"
ADD = f"{BASE}/torrents/add"
FILES = f"{BASE}/torrents/files"
FILE_PRIORITY = f"{BASE}/torrents/filePrio"
RESUME = f"{BASE}/torrents/resume"
HASH = "a" * 40

FILES_PAYLOAD = [
    {"index": 0, "name": "Show.S02E01.mkv", "size": 100, "progress": 0.0, "priority": 1},
    {"index": 1, "name": "Show.S02E05.mkv", "size": 200, "progress": 0.0, "priority": 1},
    {"index": 2, "name": "Show.S02E06.mkv", "size": 300, "progress": 0.0, "priority": 1},
]


@pytest.fixture
def engine(settings: Settings) -> QbittorrentEngine:
    configured = settings.model_copy(
        update={"qbt_url": "http://127.0.0.1:8080", "qbt_user": "admin", "qbt_pass": "secret"}
    )
    instance = QbittorrentEngine(configured)
    instance.metadata_poll_interval = 0.0
    instance.metadata_timeout = 0.05
    return instance


def _login() -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))


@respx.mock
async def test_add_uses_paused_true_when_a_selection_is_pending(
    engine: QbittorrentEngine,
) -> None:
    _login()
    add = respx.post(ADD).mock(return_value=httpx.Response(200, text="Ok."))

    await engine.add(
        AddRequest(
            external_id=HASH,
            url=f"magnet:?xt=urn:btih:{HASH}",
            save_path="/downloads",
            paused=True,
        )
    )
    await engine.aclose()

    body = add.calls[0].request.content.decode("utf-8")
    assert 'name="paused"' in body and "true" in body


@respx.mock
async def test_priority_is_set_for_every_file_before_resume(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    order: list[str] = []

    def _priority(request: httpx.Request) -> httpx.Response:
        order.append("priority")
        return httpx.Response(200)

    def _resume(_request: httpx.Request) -> httpx.Response:
        order.append("resume")
        return httpx.Response(200)

    priority = respx.post(FILE_PRIORITY).mock(side_effect=_priority)
    respx.post(RESUME).mock(side_effect=_resume)

    selected = await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert selected == [1]
    assert order == ["priority", "resume"]
    call = priority.calls[0]
    body = call.request.content.decode("utf-8")
    assert f"hash={HASH}" in body
    # Every file is named, selected one normal, the rest skipped.
    assert "id=0%7C1%7C2" in body or "id=0|1|2" in body
    assert "priority=0%7C1%7C0" in body or "priority=0|1|0" in body


@respx.mock
async def test_metadata_timeout_leaves_the_torrent_paused(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=[]))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    resume = respx.post(RESUME).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_SELECTION_PENDING"
    assert caught.value.transient
    assert not priority.called
    assert not resume.called


@respx.mock
async def test_paths_are_mapped_to_engine_indices(engine: QbittorrentEngine) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    respx.post(RESUME).mock(return_value=httpx.Response(200))

    selected = await engine.set_file_selection(
        HASH,
        [0],
        paths=["Show.S02E06.mkv"],
    )
    await engine.aclose()

    assert selected == [2]
    body = priority.calls[0].request.content.decode("utf-8")
    assert "priority=0%7C0%7C1" in body or "priority=0|0|1" in body


@respx.mock
async def test_an_unknown_path_never_sets_priorities_or_resumes(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    resume = respx.post(RESUME).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [], paths=["Nope.mkv"])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_NOT_IN_TORRENT"
    assert not priority.called
    assert not resume.called


@respx.mock
async def test_an_unknown_index_never_sets_priorities(engine: QbittorrentEngine) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    respx.post(RESUME).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [99])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_NOT_IN_TORRENT"
    assert not priority.called
