from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import httpx
import pytest
import respx

from daku.config import Settings
from daku.services.engines.aria2 import Aria2Engine, is_torrent_url
from daku.services.engines.base import AddRequest, EngineError

RPC = "http://127.0.0.1:6800/jsonrpc"
GID = "0123456789abcdef"


def _engine(settings: Settings) -> Aria2Engine:
    configured = settings.model_copy(
        update={"aria2_rpc": RPC, "aria2_secret": "s3cret"},
    )
    return Aria2Engine(configured)


def _rpc_response(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": result})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("magnet:?xt=urn:btih:" + "a" * 40, True),
        ("http://example.test/file.torrent", True),
        ("http://example.test/file.torrent?token=1", True),
        ("https://example.test/movie.mkv", False),
        ("https://example.test/file.mkv?name=x.torrent.txt", False),
    ],
)
def test_torrent_urls_are_detected(url: str, expected: bool) -> None:
    assert is_torrent_url(url) is expected


@respx.mock
async def test_add_sends_token_urls_and_options(settings: Settings) -> None:
    engine = _engine(settings)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _rpc_response(GID)

    respx.post(RPC).mock(side_effect=handler)

    gid = await engine.add(
        AddRequest(
            external_id="",
            url="https://example.test/file.mkv",
            save_path="/downloads/Daku",
            filename="file.mkv",
        )
    )
    await engine.aclose()

    assert gid == GID
    payload = seen[0]
    assert payload["method"] == "aria2.addUri"
    token, uris, options = payload["params"]
    assert token == "token:s3cret"
    assert uris == ["https://example.test/file.mkv"]
    assert options["dir"] == "/downloads/Daku"
    assert options["out"] == "file.mkv"
    assert options["continue"] == "true"
    assert options["max-connection-per-server"] == "8"


@pytest.mark.parametrize(
    ("aria_status", "expected", "error"),
    [
        ("active", "active", None),
        ("waiting", "queued", None),
        ("paused", "paused", None),
        ("complete", "complete", None),
        ("error", "failed", "download failed"),
        ("removed", "cancelled", None),
    ],
)
@respx.mock
async def test_status_mapping(
    settings: Settings, aria_status: str, expected: str, error: str | None
) -> None:
    engine = _engine(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return _rpc_response(
            {
                "gid": GID,
                "status": aria_status,
                "totalLength": "1000",
                "completedLength": "500",
                "downloadSpeed": "100",
                "errorMessage": error or "",
                "files": [{"path": "/downloads/Daku/file.mkv"}],
            }
        )

    respx.post(RPC).mock(side_effect=handler)
    statuses = await engine.poll([GID])
    await engine.aclose()

    status = statuses[GID]
    assert status.status == expected
    assert status.downloaded_bytes == 500
    assert status.total_bytes == 1000
    assert status.progress == (1.0 if expected == "complete" else 0.5)
    assert status.speed_bps == (100 if expected == "active" else None)
    assert status.eta_seconds == 5
    assert status.error == (error or None)
    assert status.files == ["/downloads/Daku/file.mkv"]


@respx.mock
async def test_missing_gid_becomes_failed(settings: Settings) -> None:
    engine = _engine(settings)
    respx.post(RPC).mock(
        return_value=httpx.Response(
            200,
            json={"error": {"code": 1, "message": f"GID {GID} is not found"}},
        )
    )

    statuses = await engine.poll([GID])
    await engine.aclose()

    assert statuses[GID].status == "failed"
    assert "no longer exists" in (statuses[GID].error or "")


@respx.mock
async def test_controls_call_the_documented_methods(settings: Settings) -> None:
    engine = _engine(settings)
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        return _rpc_response(GID)

    respx.post(RPC).mock(side_effect=handler)

    await engine.pause(GID)
    await engine.resume(GID)
    await engine.remove(GID)
    await engine.aclose()

    assert methods == [
        "aria2.pause",
        "aria2.unpause",
        "aria2.remove",
        "aria2.removeDownloadResult",
    ]


@respx.mock
async def test_unreachable_rpc_is_transient(settings: Settings) -> None:
    engine = _engine(settings)
    respx.post(RPC).mock(side_effect=httpx.ConnectError("refused"))

    health = await engine.health()
    await engine.aclose()

    assert health.ok is False
    assert "unreachable" in health.detail


@respx.mock
async def test_engine_refuses_a_torrent_url(settings: Settings) -> None:
    engine = _engine(settings)
    respx.post(RPC).mock(return_value=_rpc_response(GID))

    with pytest.raises(EngineError) as caught:
        await engine.add(
            AddRequest(
                external_id="",
                url="magnet:?xt=urn:btih:" + "a" * 40,
                save_path="/downloads/Daku",
            )
        )
    await engine.aclose()

    assert caught.value.code == "ARIA2_TORRENT_FORBIDDEN"


@respx.mock
async def test_health_reports_version(settings: Settings) -> None:
    engine = _engine(settings)
    respx.post(RPC).mock(return_value=_rpc_response({"version": "1.36.0", "enabledFeatures": []}))

    health = await engine.health()
    await engine.aclose()

    assert health.ok is True
    assert health.version == "1.36.0"


@respx.mock
async def test_poll_uses_one_request_per_gid(settings: Settings) -> None:
    engine = _engine(settings)
    calls: defaultdict[str, int] = defaultdict(int)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls[payload["method"]] += 1
        gid = payload["params"][1]
        return _rpc_response(
            {
                "gid": gid,
                "status": "active",
                "totalLength": "10",
                "completedLength": "5",
                "downloadSpeed": "1",
                "files": [],
            }
        )

    respx.post(RPC).mock(side_effect=handler)
    statuses = await engine.poll(["a" * 16, "b" * 16])
    await engine.aclose()

    assert set(statuses) == {"a" * 16, "b" * 16}
    assert calls["aria2.tellStatus"] == 2


@respx.mock
async def test_adopt_finds_a_restarted_transfer_by_url(settings: Settings) -> None:
    engine = _engine(settings)
    url = "https://example.test/file.mkv"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "aria2.tellActive":
            return _rpc_response(
                [
                    {
                        "gid": "new-gid",
                        "status": "active",
                        "files": [
                            {
                                "path": "/downloads/Daku/file.mkv",
                                "uris": [{"uri": url, "status": "used"}],
                            }
                        ],
                    }
                ]
            )
        return _rpc_response([])

    respx.post(RPC).mock(side_effect=handler)

    assert await engine.adopt(url) == "new-gid"
    assert await engine.adopt("https://example.test/other.mkv") is None
    await engine.aclose()
