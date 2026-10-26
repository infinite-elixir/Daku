"""Server-Sent Events stream.

One HTTP connection per browser, one bounded queue per connection. The stream has
a 15 s heartbeat, tells ``EventSource`` to retry after 3 s, and replays up to 100
missed events when the browser reconnects with ``Last-Event-ID``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from daku.events import Event, EventBus, format_heartbeat, format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["events"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _last_event_id(request: Request) -> int | None:
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def event_stream(
    request: Request,
    bus: EventBus,
    last_event_id: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames until the client disconnects."""

    queue = bus.subscribe()
    try:
        yield "retry: 3000\n\n"
        if last_event_id is not None:
            missed = bus.replay(last_event_id)
            if missed is None:
                resync = Event(-1, "resync", {"last_event_id": bus.last_event_id})
                yield format_sse(resync)
            else:
                for event in missed:
                    yield format_sse(event)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=bus.heartbeat_seconds)
            except TimeoutError:
                yield format_heartbeat()
                continue
            yield format_sse(event)
    finally:
        bus.unsubscribe(queue)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    bus: EventBus = request.app.state.event_bus
    return StreamingResponse(
        event_stream(request, bus, _last_event_id(request)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
