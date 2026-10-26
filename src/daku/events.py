"""In-process event bus for Server-Sent Events.

The worker and the routers publish; each connected browser gets its own bounded
queue. A slow client can never grow memory without bound: the oldest event is
dropped so the newest state always wins. Event ids are monotonic integers and the
last :data:`REPLAY_SIZE` events are kept so ``Last-Event-ID`` can replay a short
disconnect without a full refetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

QUEUE_SIZE = 64
REPLAY_SIZE = 100
HEARTBEAT_SECONDS = 15.0

#: Keys that must never appear in an SSE payload. The worker never sends these,
#: but the sanitiser is a hard guarantee for future publishers.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "magnet",
        "magnet_uri",
        "url",
        "save_path",
        "src_path",
        "lib_path",
        "path",
        "file_path",
        "token",
        "bot_token",
        "password",
        "pass",
        "api_key",
        "secret",
    }
)


@dataclass(slots=True, frozen=True)
class Event:
    """One immutable SSE event."""

    id: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def sanitise_payload(value: Any) -> Any:
    """Recursively strip secrets and absolute paths from an event payload."""

    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                continue
            if isinstance(item, str) and item.lower().startswith("magnet:"):
                continue
            clean[str(key)] = sanitise_payload(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitise_payload(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("magnet:"):
        return ""
    return value


class Subscription:
    """A bounded queue that can also be consumed with ``async for``."""

    __slots__ = ("bus", "queue")

    def __init__(self, bus: EventBus, maxsize: int) -> None:
        self.bus = bus
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self.queue.get()

    async def get(self) -> Event:
        return await self.queue.get()

    def get_nowait(self) -> Event:
        return self.queue.get_nowait()

    def qsize(self) -> int:
        return self.queue.qsize()

    def empty(self) -> bool:
        return self.queue.empty()

    def full(self) -> bool:
        return self.queue.full()

    def close(self) -> None:
        self.bus.unsubscribe(self)


class EventBus:
    """Fan-out bus with bounded per-subscriber queues and a replay ring."""

    def __init__(
        self,
        *,
        queue_size: int = QUEUE_SIZE,
        replay_size: int = REPLAY_SIZE,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.queue_size = queue_size
        self.heartbeat_seconds = heartbeat_seconds
        self._next_id = 1
        self._buffer: deque[Event] = deque(maxlen=replay_size)
        self._subscribers: set[Subscription] = set()

    @property
    def last_event_id(self) -> int:
        return self._next_id - 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> Event:
        """Assign the next id and fan the event out to every subscriber."""

        clean = sanitise_payload(data or {})
        event = Event(id=self._next_id, type=event_type, data=clean)
        self._next_id += 1
        self._buffer.append(event)
        for subscription in list(self._subscribers):
            self._offer(subscription, event)
        return event

    def _offer(self, subscription: Subscription, event: Event) -> None:
        while subscription.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                subscription.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            subscription.queue.put_nowait(event)

    def subscribe(self) -> Subscription:
        subscription = Subscription(self, self.queue_size)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def replay(self, last_event_id: int) -> list[Event] | None:
        """Return missed events after ``last_event_id``.

        ``None`` means the client is too far behind and must resync. A client
        that is already current gets an empty list, not a resync.
        """

        latest = self.last_event_id
        if last_event_id > latest:
            return None
        if not self._buffer:
            return [] if last_event_id == latest else None
        earliest = self._buffer[0].id
        if last_event_id < earliest - 1:
            return None
        return [event for event in self._buffer if event.id > last_event_id]


def format_sse(event: Event) -> str:
    """Serialise an event in the wire format expected by ``EventSource``."""

    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))

    # A payload is one logical field; a literal newline would split the frame.
    payload = payload.replace("\r", "").replace("\n", " ")
    return f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"


def format_heartbeat() -> str:
    """Heartbeats carry no id so they never enter the replay ring."""

    return 'event: heartbeat\ndata: {"comment":"keep-alive"}\n\n'
