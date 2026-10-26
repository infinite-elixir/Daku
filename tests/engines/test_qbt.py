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
WEBAPI_VERSION = "http://127.0.0.1:8080/api/v2/app/webapiVersion"
PAUSE = "http://127.0.0.1:8080/api/v2/torrents/pause"
RESUME = "http://127.0.0.1:8080/api/v2/torrents/resume"
STOP = "http://127.0.0.1:8080/api/v2/torrents/stop"
START = "http://127.0.0.1:8080/api/v2/torrents/start"
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
    assert map_state("pausedUP", 0.4) == "paused"
    assert map_state("stoppedDL", 0.4) == "paused"
    assert map_state("stoppedUP", 0.4) == "paused"
    assert map_state("stoppedUP", 1.0) == "paused"
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
    assert 'name="paused"' in body and "false" in body
    assert 'name="stopped"' in body and "false" in body
    assert add.calls[0].request.headers["referer"] == "http://127.0.0.1:8080"


@respx.mock
async def test_add_accepts_the_qbittorrent_52_json_response(engine: QbittorrentEngine) -> None:
    """The exact body from the report must not turn a started torrent into a failure."""

    respx.post(LOGIN).mock(return_value=httpx.Response(204))
    respx.post(ADD).mock(
        return_value=httpx.Response(
            200,
            json={
                "success_count": 1,
                "failure_count": 0,
                "pending_count": 0,
                "added_torrent_ids": [HASH],
            },
        )
    )

    result = await engine.add(
        AddRequest(external_id=HASH, url=f"magnet:?xt=urn:btih:{HASH}", save_path="/x")
    )
    await engine.aclose()

    assert result == HASH


@respx.mock
async def test_add_accepts_a_202_with_pending_metadata(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post(ADD).mock(
        return_value=httpx.Response(
            202,
            json={
                "success_count": 0,
                "failure_count": 0,
                "pending_count": 1,
                "added_torrent_ids": [],
            },
        )
    )

    result = await engine.add(
        AddRequest(external_id=HASH, url=f"magnet:?xt=urn:btih:{HASH}", save_path="/x")
    )
    await engine.aclose()

    assert result == HASH


@respx.mock
async def test_add_accepts_an_empty_2xx_body(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post(ADD).mock(return_value=httpx.Response(200, text=""))

    result = await engine.add(
        AddRequest(external_id=HASH, url=f"magnet:?xt=urn:btih:{HASH}", save_path="/x")
    )
    await engine.aclose()

    assert result == HASH


@respx.mock
async def test_add_json_failure_is_a_short_message(engine: QbittorrentEngine) -> None:
    payload = {
        "success_count": 0,
        "failure_count": 1,
        "pending_count": 0,
        "added_torrent_ids": [],
    }
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post(ADD).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(EngineError) as caught:
        await engine.add(AddRequest(external_id=HASH, url="magnet:?xt=urn:btih:x", save_path="/x"))
    await engine.aclose()

    assert caught.value.code == "QBT_REJECTED"
    assert caught.value.message == "qBittorrent rejected the torrent"
    assert "success_count" not in caught.value.message


@respx.mock
async def test_add_sends_the_requested_paused_state_to_both_parameters(
    engine: QbittorrentEngine,
) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    add = respx.post(ADD).mock(return_value=httpx.Response(200, text="Ok."))

    await engine.add(
        AddRequest(
            external_id=HASH,
            url=f"magnet:?xt=urn:btih:{HASH}",
            save_path="/x",
            paused=True,
        )
    )
    await engine.aclose()

    body = add.calls[0].request.content.decode("utf-8")
    assert 'name="paused"' in body
    assert 'name="stopped"' in body
    assert body.count("true") >= 2


@respx.mock
async def test_add_non_2xx_fails_even_with_an_ok_body(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post(ADD).mock(return_value=httpx.Response(400, text="Ok."))

    with pytest.raises(EngineError) as caught:
        await engine.add(AddRequest(external_id=HASH, url="magnet:?xt=urn:btih:x", save_path="/x"))
    await engine.aclose()

    assert caught.value.code == "QBT_REJECTED"


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
async def test_wrong_password_backs_off_instead_of_hammering(engine: QbittorrentEngine) -> None:
    login = respx.post(LOGIN).mock(return_value=httpx.Response(401))

    with pytest.raises(EngineError) as first:
        await engine.poll([HASH])
    with pytest.raises(EngineError) as second:
        await engine.poll([HASH])
    await engine.aclose()

    assert first.value.code == "QBT_LOGIN_FAILED"
    assert second.value.message == first.value.message
    assert login.call_count == 1


@respx.mock
async def test_banned_ip_is_not_retried_every_poll(engine: QbittorrentEngine) -> None:
    login = respx.post(LOGIN).mock(
        return_value=httpx.Response(403, text="Your IP has been banned, try again later.")
    )

    first = await engine.health()
    second = await engine.health()
    await engine.aclose()

    assert first.ok is False
    assert second.ok is False
    assert "banned" in second.detail
    assert login.call_count == 1


@respx.mock
async def test_pause_resume_use_stop_start_on_modern_webapi(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(204))
    version = respx.get(WEBAPI_VERSION).mock(return_value=httpx.Response(200, text="2.11.0"))
    stop = respx.post(STOP).mock(return_value=httpx.Response(200))
    start = respx.post(START).mock(return_value=httpx.Response(200))

    await engine.pause(HASH)
    await engine.pause(HASH)
    await engine.resume(HASH)
    await engine.aclose()

    assert version.call_count == 1
    assert stop.call_count == 2
    assert start.called


@respx.mock
async def test_pause_resume_use_pause_resume_on_old_webapi(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.get(WEBAPI_VERSION).mock(return_value=httpx.Response(200, text="2.10.0"))
    pause = respx.post(PAUSE).mock(return_value=httpx.Response(200))
    resume = respx.post(RESUME).mock(return_value=httpx.Response(200))

    await engine.pause(HASH)
    await engine.resume(HASH)
    await engine.aclose()

    assert pause.called
    assert resume.called


@respx.mock
async def test_unknown_version_falls_back_to_resume_and_caches_it(
    engine: QbittorrentEngine,
) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.get(WEBAPI_VERSION).mock(return_value=httpx.Response(404))
    respx.post(STOP).mock(return_value=httpx.Response(404))
    respx.post(START).mock(return_value=httpx.Response(404))
    pause = respx.post(PAUSE).mock(return_value=httpx.Response(200))
    resume = respx.post(RESUME).mock(return_value=httpx.Response(200))

    await engine.pause(HASH)
    await engine.resume(HASH)
    await engine.pause(HASH)
    await engine.aclose()

    assert pause.call_count == 2
    assert resume.called


@respx.mock
async def test_a_404_from_a_mutation_is_a_rejection(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(EngineError) as caught:
        await engine.remove(HASH)
    await engine.aclose()

    assert caught.value.code == "QBT_REJECTED"
    assert not caught.value.transient


@respx.mock
async def test_pause_resume_cancel_are_plain_form_calls(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(200, text="Ok."))
    respx.get(WEBAPI_VERSION).mock(return_value=httpx.Response(200, text="2.10.0"))
    pause = respx.post(PAUSE).mock(return_value=httpx.Response(200))
    resume = respx.post(RESUME).mock(return_value=httpx.Response(200))
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


@respx.mock
async def test_poll_maps_qbittorrent_5_paused_states(engine: QbittorrentEngine) -> None:
    respx.post(LOGIN).mock(return_value=httpx.Response(204))
    respx.get(INFO).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"hash": HASH, "state": "stoppedDL", "progress": 0.3, "size": 100, "completed": 30},
                {
                    "hash": "b" * 40,
                    "state": "stoppedUP",
                    "progress": 1.0,
                    "size": 100,
                    "completed": 100,
                },
            ],
        )
    )

    statuses = await engine.poll([HASH, "b" * 40])
    await engine.aclose()

    assert statuses[HASH].status == "paused"
    assert statuses["b" * 40].status == "paused"
