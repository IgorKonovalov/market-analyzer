"""Short-lived, single-use SSE tickets (Plan 0072 phase 4, ADR-0066).

The renderer subscribes to `GET /events` via the browser `EventSource` API, which
cannot set request headers. Rather than put the durable renderer bearer in the
`?token=` query string (a full-power credential on the leak-prone URL surface),
the renderer exchanges its bearer — through the bearer-gated `POST /events/ticket`
mint endpoint — for a short-TTL, single-use ticket, and opens
`EventSource('/events?ticket=<ticket>')`. The bearer never appears in a URL.

`SseTicketStore` is the in-memory, TTL-swept, single-use store behind that
exchange: `mint()` issues an opaque token good for `ttl_seconds`; `consume()`
validates AND removes it (single use — a second open with the same ticket fails).
No persistence: tickets die with the process, like the bearer. The clock is
injectable so tests can drive expiry deterministically; production uses
`time.monotonic` (immune to wall-clock jumps).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

# Long enough to open the stream after minting, short enough that a leaked
# `/events?ticket=…` URL is worthless almost immediately (ADR-0066: seconds).
DEFAULT_SSE_TICKET_TTL_SECONDS = 10.0

# Ticket entropy: 32 bytes url-safe, on par with the bearer's own strength.
_TICKET_NBYTES = 32


class SseTicketStore:
    """In-memory, TTL-swept, single-use ticket store for the SSE stream."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_SSE_TICKET_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock if clock is not None else time.monotonic
        # ticket -> expiry instant (in the clock's units).
        self._expiry: dict[str, float] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def mint(self) -> str:
        """Issue a fresh single-use ticket, valid for `ttl_seconds`."""
        self._sweep()
        ticket = secrets.token_urlsafe(_TICKET_NBYTES)
        self._expiry[ticket] = self._clock() + self._ttl
        return ticket

    def consume(self, ticket: str) -> bool:
        """Validate and consume `ticket` (single use).

        Returns `True` iff the ticket existed and had not expired. The ticket is
        removed either way, so a second `consume` of the same value — or of an
        expired one — returns `False`. An unknown/absent ticket returns `False`.
        """
        self._sweep()
        expiry = self._expiry.pop(ticket, None)
        if expiry is None:
            return False
        return self._clock() < expiry

    def _sweep(self) -> None:
        """Drop expired tickets so the store cannot grow without bound."""
        now = self._clock()
        stale = [ticket for ticket, expiry in self._expiry.items() if expiry <= now]
        for ticket in stale:
            del self._expiry[ticket]


__all__ = ["DEFAULT_SSE_TICKET_TTL_SECONDS", "SseTicketStore"]
