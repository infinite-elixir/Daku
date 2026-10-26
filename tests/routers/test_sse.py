from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from daku.config import Settings


def _frames(lines: Iterator[str], limit: int = 40) -> Iterator[list[str]]:
    frame: list[str] = []
    for line in lines:
        if line == "":
            if frame:
                yield frame
                frame = []
            continue
        frame.append(line)
        if len(frame) >= limit:
            yield frame
            frame = []


def test_events_stream_has_the_required_headers(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    client.app.state.event_bus.heartbeat_seconds = 0.05  # type: ignore[attr-defined]

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert next(response.iter_lines()) == "retry: 3000"


def test_heartbeat_arrives_at_least_every_interval(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    bus = client.app.state.event_bus  # type: ignore[attr-defined]
    bus.heartbeat_seconds = 0.05

    with client.stream("GET", "/api/events") as response:
        seen = False
        for frame in _frames(response.iter_lines()):
            if "event: heartbeat" in frame:
                seen = True
                break
        assert seen, "no heartbeat frame was sent"


def test_last_event_id_replays_missed_events(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    bus = client.app.state.event_bus  # type: ignore[attr-defined]
    bus.heartbeat_seconds = 0.05
    bus.publish("download.updated", {"id": "d1", "progress": 0.25})
    bus.publish("download.updated", {"id": "d1", "progress": 0.75})

    with client.stream("GET", "/api/events", headers={"Last-Event-ID": "0"}) as response:
        data: list[str] = []
        for frame in _frames(response.iter_lines(), limit=40):
            data.extend(line for line in frame if line.startswith("data:"))
            if len(data) >= 2:
                break

    assert any("0.25" in line for line in data)
    assert any("0.75" in line for line in data)


def test_stale_last_event_id_triggers_resync(
    app_client: tuple[TestClient, Settings],
) -> None:
    client, _settings = app_client
    bus = client.app.state.event_bus  # type: ignore[attr-defined]
    bus.heartbeat_seconds = 0.05
    bus._buffer.clear()  # simulate a long disconnect
    bus._next_id = 500
    bus.publish("download.updated", {"id": "d1"})

    with client.stream("GET", "/api/events", headers={"Last-Event-ID": "0"}) as response:
        block = []
        for frame in _frames(response.iter_lines()):
            block.extend(frame)
            if any(line.startswith("data:") for line in frame):
                break

    assert any(line == "event: resync" for line in block)


@pytest.mark.asyncio
async def test_disconnect_unsubscribes(app_client: tuple[TestClient, Settings]) -> None:
    client, _settings = app_client
    bus = client.app.state.event_bus  # type: ignore[attr-defined]
    bus.heartbeat_seconds = 0.05

    with client.stream("GET", "/api/events") as response:
        next(response.iter_lines())
        assert bus.subscriber_count == 1

    for _ in range(20):
        if bus.subscriber_count == 0:
            break
        await asyncio.sleep(0.01)
    assert bus.subscriber_count == 0
