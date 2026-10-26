from __future__ import annotations

import asyncio

from daku.events import EventBus, format_sse, sanitise_payload


async def test_publish_reaches_every_subscriber() -> None:
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()

    event = bus.publish("download.updated", {"id": "d1", "progress": 0.5})

    assert event.id == 1
    assert (await first.get()).data["progress"] == 0.5
    assert (await second.get()).data["id"] == "d1"
    assert bus.subscriber_count == 2

    bus.unsubscribe(first)
    assert bus.subscriber_count == 1


async def test_subscription_supports_async_iteration() -> None:
    bus = EventBus()
    subscription = bus.subscribe()

    bus.publish("download.updated", {"progress": 0.5})

    async def first() -> str:
        async for event in subscription:
            return event.type
        return ""

    assert await asyncio.wait_for(first(), timeout=1) == "download.updated"


async def test_slow_client_drops_the_oldest_event() -> None:
    bus = EventBus(queue_size=2)
    queue = bus.subscribe()

    for index in range(4):
        bus.publish("download.updated", {"progress": index})

    assert queue.qsize() == 2
    assert (await queue.get()).data["progress"] == 2
    assert (await queue.get()).data["progress"] == 3


async def test_replay_returns_only_missed_events() -> None:
    bus = EventBus()
    for index in range(3):
        bus.publish("download.updated", {"progress": index})

    missed = bus.replay(1)

    assert missed is not None
    assert [event.id for event in missed] == [2, 3]
    assert bus.replay(3) == []


async def test_replay_too_old_asks_for_resync() -> None:
    bus = EventBus(replay_size=2)
    for index in range(4):
        bus.publish("download.updated", {"progress": index})

    assert bus.replay(0) is None
    # Earliest buffered id is 3, so a client at 2 is exactly current.
    assert bus.replay(2) is not None
    assert bus.replay(99) is None


def test_payloads_never_include_magnets_paths_or_tokens() -> None:
    bus = EventBus()
    event = bus.publish(
        "download.updated",
        {
            "id": "d1",
            "magnet": "magnet:?xt=urn:btih:abc",
            "url": "https://example.test/file.mkv",
            "save_path": "/home/user/Downloads/Daku",
            "token": "secret",
            "nested": {"src_path": "/tmp/file.mkv", "note": "magnet:?xt=urn:btih:xyz"},
        },
    )

    assert "magnet" not in event.data
    assert "url" not in event.data
    assert "save_path" not in event.data
    assert "token" not in event.data
    assert event.data["nested"] == {}
    assert "magnet:" not in format_sse(event)


def test_sanitiser_keeps_ordinary_strings() -> None:
    assert sanitise_payload({"title": "Dune", "quality": "2160p"}) == {
        "title": "Dune",
        "quality": "2160p",
    }


def test_format_sse_is_one_frame_per_event() -> None:
    bus = EventBus()
    frame = format_sse(bus.publish("download.updated", {"line": "a\nb"}))

    assert frame.startswith("id: 1\nevent: download.updated\ndata: ")
    assert frame.endswith("\n\n")
    assert frame.count("\n\n") == 1
