from __future__ import annotations

import httpx
import pytest
import respx

from daku.config import Settings
from daku.services.engines.base import AddRequest, EngineError
from daku.services.engines.qbittorrent import QbittorrentEngine, map_state

LOGIN = "http://127.0.0.1:8080/api/v2/auth/login"
ADD = "http://127.0.0.1:8080/api/v2/torrents/add"
INFO = "http://127.0.0.1:8080/api/v2/torrents/info"
HASH = "a" * 40


@pytest.fixture
def engine(settings: Settings) -> QbittorrentEngine:
    configured = settings.model_copy(
        update={"qbt_url": "http://127.0.0.1:8080", "qbt_user": "admin", "qbt_pass": "secret"}
    )
    return QbittorrentEngine(configured)


def test_state_mapping_covers_every_documented_state() -> None:
    assert map_state("metaDL", 0.1) == "active"
    assert map_state("downloading", 0.4) == "active"
    assert map_state("stalledDL", 0.4) == "active"
    assert map_state("forcedDL", 0.4) == "active"
    assert map_state("queuedDL", 0.0) == "queued"
    assert map_state("pausedDL", 0.4) == "paused"
    assert map_state("error", 0.4) == "failed"
    assert map_state("missingFiles", 0.4) == "failed"
    assert map_state("uploading", 1.0) == "complete"
    assert map_state("stalledUP", 1.0) == "complete"
    assert map_state("forcedUP", 1.0) == "complete"
    # Seeding must never look incomplete, even if qBittorrent reports < 1.
    assert map_state("uploading", 0.9999) == "complete"


@respx.mock
async def test_login_and_add_uses_the_required_options(engine: QbittorrentEngine) -> None:
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    add = respx.post(ADD).mock(return_value=httpx.Response(200, text="Ok."))

    result = await engine.add(
        AddRequest(
            external_id=HASH.upper(),
            url=f"magnet:?xt=urn:btih:{HASH}",
            save_path="/downloads/Daku",
            tag="title-id",
        )
    )
    await engine.aclose()

    assert result == HASH
    assert login.called
    body = add.calls[0].request.content.decode("utf-8")
    assert "magnet:?xt=urn:btih:" in body
    assert 'name="savepath"' in body
    assert "/downloads/Daku" in body
    assert 'name="category"' in body and "daku" in body
    assert "title-id" in body
    assert "sequentialDownload" in body and "false" in body
    assert "firstLastPiecePrio" in body and "true" in body
    assert "paused" in body
    assert add.calls[0].request.headers["referer"] == "http://127.0.0.1:8080"


@respx.mock
async def test_add_failure_raises_rejected(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post(ADD).mock(return_value=httpx.Response(200, text="Fails."))

    with pytest.raises(EngineError) as caught:
        await engine.add(AddRequest(external_id=HASH, url="magnet:?xt=urn:btih:x", save_path="/x"))
    await engine.aclose()

    assert caught.value.code == "QBT_REJECTED"
    assert not caught.value.transient


@respx.mock
async def test_poll_maps_bytes_speed_eta_and_batches_hashes(
    engine: QbittorrentEngine,
) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    route = respx.get(INFO).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "hash": HASH.upper(),
                    "state": "downloading",
                    "progress": 0.25,
                    "size": 1000,
                    "completed": 250,
                    "dlspeed": 128,
                    "eta": 60,
                    "content_path": "/downloads/Daku/file.mkv",
                },
                {
                    "hash": "b" * 40,
                    "state": "uploading",
                    "progress": 1.0,
                    "size": 500,
                    "completed": 500,
                    "dlspeed": 0,
                    "eta": 8640000,
                },
            ],
        )
    )

    statuses = await engine.poll([HASH, "b" * 40])
    await engine.aclose()

    assert statuses[HASH].status == "active"
    assert statuses[HASH].progress == 0.25
    assert statuses[HASH].downloaded_bytes == 250
    assert statuses[HASH].speed_bps == 128
    assert statuses[HASH].eta_seconds == 60
    assert statuses[HASH].files == ["/downloads/Daku/file.mkv"]
    assert statuses["b" * 40].status == "complete"
    assert statuses["b" * 40].eta_seconds is None
    assert route.calls[0].request.url.params["hashes"] == f"{HASH}|{'b' * 40}"


@respx.mock
async def test_403_reauthenticates_once_and_retries(engine: QbittorrentEngine) -> None:
    login = respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    responses = [
        httpx.Response(403, text="Forbidden"),
        httpx.Response(
            200,
            json=[
                {
                    "hash": HASH,
                    "state": "downloading",
                    "progress": 0.5,
                    "size": 100,
                    "completed": 50,
                    "dlspeed": 1,
                    "eta": 10,
                }
            ],
        ),
    ]
    respx.get(INFO).mock(side_effect=responses)

    statuses = await engine.poll([HASH])
    await engine.aclose()

    assert statuses[HASH].progress == 0.5
    assert login.call_count == 2


@respx.mock
async def test_unreachable_qbittorrent_is_a_transient_error(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(EngineError) as caught:
        await engine.poll([HASH])
    await engine.aclose()

    assert caught.value.code == "QBT_UNREACHABLE"
    assert caught.value.transient


@respx.mock
async def test_health_reports_version(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://127.0.0.1:8080/api/v2/app/version").mock(
        return_value=httpx.Response(200, text="v5.0.2")
    )

    health = await engine.health()
    await engine.aclose()

    assert health.ok is True
    assert health.version == "v5.0.2"


@respx.mock
async def test_health_accepts_the_204_login_of_qbittorrent_5(engine: QbittorrentEngine) -> None:
    """qBittorrent 5.2+ answers a successful login with an empty 204, not ``200 Ok.``."""

    respx.post(LOGIN).mock(return_value=httpx.Response(204))
    respx.get("http://127.0.0.1:8080/api/v2/app/version").mock(
        return_value=httpx.Response(200, text="v5.3.0")
    )

    health = await engine.health()
    await engine.aclose()

    assert health.ok is True
    assert health.version == "v5.3.0"


@respx.mock
async def test_wrong_password_is_reported_as_a_login_failure(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(401))

    with pytest.raises(EngineError) as caught:
        await engine.poll([HASH])
    await engine.aclose()

    assert caught.value.code == "QBT_LOGIN_FAILED"
    assert "username or password" in caught.value.message


@respx.mock
async def test_banned_ip_is_explained(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(
        return_value=httpx.Response(403, text="Your IP has been banned, try again later.")
    )

    with pytest.raises(EngineError) as caught:
        await engine.poll([HASH])
    await engine.aclose()

    assert caught.value.code == "QBT_LOGIN_FAILED"
    assert "banned" in caught.value.message


@respx.mock
async def test_pause_resume_cancel_are_plain_form_calls(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    pause = respx.post("http://127.0.0.1:8080/api/v2/torrents/pause").mock(
        return_value=httpx.Response(200)
    )
    resume = respx.post("http://127.0.0.1:8080/api/v2/torrents/resume").mock(
        return_value=httpx.Response(200)
    )
    delete = respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(
        return_value=httpx.Response(200)
    )

    await engine.pause(HASH)
    await engine.pause(HASH)
    await engine.resume(HASH)
    await engine.remove(HASH)
    await engine.aclose()

    assert pause.call_count == 2
    assert resume.called
    assert "deleteFiles=false" in delete.calls[0].request.content.decode()
