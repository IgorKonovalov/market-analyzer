"""`GET /events` Server-Sent Events route (ADR-0017, Plan 0007 phase 2).

Authentication is renderer-bearer-gated by the central middleware in
`app.py`, with one route-specific accommodation: `/events` also accepts the
bearer from a `?token=<bearer>` query parameter so that browser `EventSource`
(which cannot set custom headers) can subscribe. The query-string path is
limited to `/events` — every other renderer route stays header-only.

The route returns `text/event-stream` and yields:
  - One `retry: 5000` line at stream start (browser reconnect backoff).
  - Either an envelope (when one lands) or a `: ping` comment (every 15 s
    if idle) to defeat intermediate proxies' idle-close.
  - A synthetic `chart.update_dropped v1` envelope ahead of the next real
    one when the subscriber's bounded queue overflowed.

Subscribers' queues are cleaned up when the generator is finalised
(client disconnect, server stop, exception inside the loop).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from market_analyser.api.events import EventBus

router = APIRouter(tags=["events"])

PING_INTERVAL_S = 15.0
RETRY_MS = 5000


async def _sse_generator(bus: EventBus) -> AsyncIterator[bytes]:
    sub = bus.subscribe()
    try:
        yield f"retry: {RETRY_MS}\n\n".encode()
        while True:
            try:
                envelope = await asyncio.wait_for(sub.next(), timeout=PING_INTERVAL_S)
                yield f"data: {envelope.model_dump_json()}\n\n".encode()
            except TimeoutError:
                yield b": ping\n\n"
    finally:
        sub.close()


@router.get("/events")
async def get_events(request: Request) -> StreamingResponse:
    bus: EventBus = request.app.state.event_bus
    return StreamingResponse(
        _sse_generator(bus),
        media_type="text/event-stream",
        headers={
            # Discourage intermediate proxies from buffering.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
