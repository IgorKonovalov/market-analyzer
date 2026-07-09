"""Plan 0072 phase 4 (ADR-0066): the in-memory SSE ticket store.

Unit-level defence of the store's contract — single use, TTL expiry, unknown
rejection, sweep — driven by a fake clock so expiry is deterministic (no
sleeping). The `/events` integration behaviour is defended in test_events_sse.py.
"""

from __future__ import annotations

from market_analyser.api.sse_ticket import SseTicketStore


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


def test_minted_ticket_consumes_once() -> None:
    store = SseTicketStore(ttl_seconds=10.0, clock=_FakeClock())
    ticket = store.mint()
    assert store.consume(ticket) is True
    # Single use: the same ticket cannot be consumed again.
    assert store.consume(ticket) is False


def test_unknown_ticket_is_rejected() -> None:
    store = SseTicketStore(ttl_seconds=10.0, clock=_FakeClock())
    assert store.consume("never-minted") is False
    assert store.consume("") is False


def test_expired_ticket_is_rejected() -> None:
    clock = _FakeClock()
    store = SseTicketStore(ttl_seconds=10.0, clock=clock)
    ticket = store.mint()
    clock.advance(10.0)  # exactly at expiry — not strictly before, so invalid
    assert store.consume(ticket) is False


def test_ticket_valid_within_ttl() -> None:
    clock = _FakeClock()
    store = SseTicketStore(ttl_seconds=10.0, clock=clock)
    ticket = store.mint()
    clock.advance(9.999)
    assert store.consume(ticket) is True


def test_each_mint_is_distinct() -> None:
    store = SseTicketStore(clock=_FakeClock())
    tickets = {store.mint() for _ in range(50)}
    assert len(tickets) == 50  # opaque, unguessable, unique


def test_sweep_evicts_expired_tickets() -> None:
    clock = _FakeClock()
    store = SseTicketStore(ttl_seconds=10.0, clock=clock)
    stale = store.mint()
    clock.advance(11.0)
    # Minting sweeps: the stale ticket is gone from the store, so even the sweep
    # path cannot resurrect it.
    store.mint()
    assert store.consume(stale) is False
    assert store.ttl_seconds == 10.0
