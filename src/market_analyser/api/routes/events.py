"""`GET /events` Server-Sent Events route + `POST /events/ticket` mint (ADR-0017,
Plan 0007 phase 2; ticket auth added Plan 0072 phase 4, ADR-0066).

`GET /events` authenticates with a short-lived, single-use **ticket** in the
`?ticket=<ticket>` query string — never the durable bearer. Browser `EventSource`
cannot set request headers, so the renderer first calls the bearer-gated
`POST /events/ticket` (bearer in the `Authorization` header, as normal) to
exchange its bearer for a ticket, then opens `EventSource('/events?ticket=…')`.
The central middleware in `app.py` validates + consumes the ticket for `/events`
(single use); the mint endpoint gets the ordinary renderer-bearer check. The
durable bearer never appears in a URL.

The stream returns `text/event-stream` and yields:
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
from pydantic import BaseModel

from market_analyser.api.sse_ticket import SseTicketStore
from market_analyser.events import EventBus

router = APIRouter(tags=["events"])

PING_INTERVAL_S = 15.0
RETRY_MS = 5000


class SseTicketResponse(BaseModel):
    """`POST /events/ticket` response: a fresh single-use ticket for the SSE
    stream and how many seconds it is valid for."""

    ticket: str
    expires_in_seconds: float


@router.post("/events/ticket", response_model=SseTicketResponse)
def mint_events_ticket(request: Request) -> SseTicketResponse:
    """Exchange the renderer bearer (checked by the central middleware) for a
    short-TTL, single-use SSE ticket (ADR-0066). The renderer opens the stream
    with `?ticket=<ticket>` and re-mints before every reconnect."""
    store: SseTicketStore = request.app.state.sse_ticket_store
    return SseTicketResponse(ticket=store.mint(), expires_in_seconds=store.ttl_seconds)


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
