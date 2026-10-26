from __future__ import annotations

import copy

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
STOP = f"{BASE}/torrents/stop"
START = f"{BASE}/torrents/start"
WEBAPI_VERSION = f"{BASE}/app/webapiVersion"
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


def _login(*, version: str = "2.11.0") -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.get(WEBAPI_VERSION).mock(return_value=httpx.Response(200, text=version))


def _read_priority_body(body: str) -> tuple[list[int], list[int]]:
    from urllib.parse import parse_qs

    parsed = parse_qs(body)
    ids = [int(item) for item in parsed["id"][0].split("|")]
    priorities = [int(item) for item in parsed["priority"][0].split("|")]
    return ids, priorities


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
async def test_add_time_file_priorities_are_sent_on_modern_webapi(
    engine: QbittorrentEngine,
) -> None:
    _login(version="2.11.9")
    add = respx.post(ADD).mock(return_value=httpx.Response(200, text="Ok."))

    await engine.add(
        AddRequest(
            external_id=HASH,
            url=f"magnet:?xt=urn:btih:{HASH}",
            save_path="/downloads",
            paused=True,
            file_indices=[1],
            total_files=3,
        )
    )
    await engine.aclose()

    body = add.calls[0].request.content.decode("utf-8")
    assert 'name="filePriorities"' in body
    assert "0|1|0" in body
    assert 'name="stopped"' in body and "true" in body


@respx.mock
async def test_torrent_is_stopped_before_priorities_and_started_after_verification(
    engine: QbittorrentEngine,
) -> None:
    _login()
    state = {"files": copy.deepcopy(FILES_PAYLOAD)}
    respx.get(FILES).mock(side_effect=lambda _request: httpx.Response(200, json=state["files"]))
    order: list[str] = []

    def _stop(_request: httpx.Request) -> httpx.Response:
        order.append("stop")
        return httpx.Response(200)

    def _priority(request: httpx.Request) -> httpx.Response:
        order.append("priority")
        _ids, priorities = _read_priority_body(request.content.decode("utf-8"))
        for row, priority in zip(state["files"], priorities, strict=True):
            row["priority"] = priority
        return httpx.Response(200)

    def _start(_request: httpx.Request) -> httpx.Response:
        order.append("start")
        return httpx.Response(200)

    respx.post(STOP).mock(side_effect=_stop)
    priority = respx.post(FILE_PRIORITY).mock(side_effect=_priority)
    respx.post(START).mock(side_effect=_start)

    selected = await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert selected == [1]
    assert order == ["stop", "priority", "start"]
    ids, priorities = _read_priority_body(priority.calls[0].request.content.decode("utf-8"))
    assert ids == [0, 1, 2]
    assert priorities == [0, 1, 0]


@respx.mock
async def test_read_back_is_retried_until_qbittorrent_applies_the_priorities(
    engine: QbittorrentEngine,
) -> None:
    _login()
    stale = copy.deepcopy(FILES_PAYLOAD)
    applied = copy.deepcopy(FILES_PAYLOAD)
    applied[1]["priority"] = 1
    for index in (0, 2):
        applied[index]["priority"] = 0
    reads = [stale, stale, applied]
    respx.get(FILES).mock(side_effect=lambda _request: httpx.Response(200, json=reads.pop(0)))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    selected = await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert selected == [1]
    assert start.called


@respx.mock
async def test_a_rejected_file_priority_leaves_the_torrent_stopped(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(400))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_SELECTION_FAILED"
    assert not caught.value.transient
    assert not start.called


@respx.mock
async def test_a_priority_that_is_never_applied_leaves_the_torrent_stopped(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_SELECTION_FAILED"
    assert not caught.value.transient
    assert not start.called


@respx.mock
async def test_metadata_timeout_leaves_the_torrent_stopped(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=[]))
    stop = respx.post(STOP).mock(return_value=httpx.Response(200))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [1])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_SELECTION_PENDING"
    assert caught.value.transient
    assert stop.called
    assert not priority.called
    assert not start.called


@respx.mock
async def test_paths_are_mapped_to_engine_indices(engine: QbittorrentEngine) -> None:
    _login()
    state = {"files": copy.deepcopy(FILES_PAYLOAD)}
    respx.get(FILES).mock(side_effect=lambda _request: httpx.Response(200, json=state["files"]))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    priority = respx.post(FILE_PRIORITY).mock(
        side_effect=lambda request: _apply_priorities(request, state["files"])
    )
    respx.post(START).mock(return_value=httpx.Response(200))

    selected = await engine.set_file_selection(
        HASH,
        [0],
        paths=["Show.S02E06.mkv"],
    )
    await engine.aclose()

    assert selected == [2]
    _ids, priorities = _read_priority_body(priority.calls[0].request.content.decode("utf-8"))
    assert priorities == [0, 0, 1]


def _apply_priorities(request: httpx.Request, files: list[dict[str, object]]) -> httpx.Response:
    _ids, priorities = _read_priority_body(request.content.decode("utf-8"))
    for row, priority in zip(files, priorities, strict=True):
        row["priority"] = priority
    return httpx.Response(200)


@respx.mock
async def test_an_unknown_path_never_sets_priorities_or_starts(
    engine: QbittorrentEngine,
) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [], paths=["Nope.mkv"])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_NOT_IN_TORRENT"
    assert not priority.called
    assert not start.called


@respx.mock
async def test_an_unknown_index_never_sets_priorities(engine: QbittorrentEngine) -> None:
    _login()
    respx.get(FILES).mock(return_value=httpx.Response(200, json=FILES_PAYLOAD))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    priority = respx.post(FILE_PRIORITY).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    with pytest.raises(EngineError) as caught:
        await engine.set_file_selection(HASH, [99])
    await engine.aclose()

    assert caught.value.code == "QBT_FILE_NOT_IN_TORRENT"
    assert not priority.called
    assert not start.called


@respx.mock
async def test_live_reselection_without_start_restores_priorities_stopped(
    engine: QbittorrentEngine,
) -> None:
    _login()
    state = {"files": copy.deepcopy(FILES_PAYLOAD)}
    respx.get(FILES).mock(side_effect=lambda _request: httpx.Response(200, json=state["files"]))
    respx.post(STOP).mock(return_value=httpx.Response(200))
    respx.post(FILE_PRIORITY).mock(
        side_effect=lambda request: _apply_priorities(request, state["files"])
    )
    start = respx.post(START).mock(return_value=httpx.Response(200))

    effective = await engine.set_file_selection(HASH, [2], start=False)
    await engine.aclose()

    assert effective == [2]
    assert [row["priority"] for row in state["files"]] == [0, 0, 1]
    assert not start.called
