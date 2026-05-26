"""Plan 0013 phase 3 done-when: the BackfillCoordinator's (symbol, timeframe)
dedup, registry cleanup, and event sequencing.

A `_FakeBackfillProvider` (the narrow SupportsBackfill capability) drives the
coordinator on a single event loop; the bus is a real `EventBus` so the published
envelopes can be drained and ordered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from market_analyser.api.events import EventBus
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.types import BackfillResult, Bar, Coverage

_T1 = datetime(2026, 4, 1, tzinfo=UTC)
_T2 = datetime(2026, 5, 1, tzinfo=UTC)
_T3 = datetime(2026, 5, 2, tzinfo=UTC)
_T4 = datetime(2026, 6, 1, tzinfo=UTC)


def _bar(day: int = 15) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, day, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


class _FakeBackfillProvider:
    """Narrow SupportsBackfill fake with a fetch counter."""

    def __init__(
        self,
        *,
        gaps: Sequence[tuple[datetime, datetime]],
        fetched: Sequence[Bar] | Exception,
        cached: Sequence[Bar] = (),
    ) -> None:
        self._gaps = list(gaps)
        self._fetched = fetched
        self._cached = list(cached)
        self.fetch_calls = 0

    def coverage(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Coverage:
        return Coverage(cached=list(self._cached), gaps=list(self._gaps))

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.fetch_calls += 1
        if isinstance(self._fetched, Exception):
            raise self._fetched
        return list(self._fetched)

    def get_ohlcv_with_status(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> BackfillResult:
        bars = self.get_ohlcv(symbol, timeframe, start, end)
        return BackfillResult(bars=list(bars), partial_reason=None, message=None)


def test_same_key_same_range_coalesces_to_one_task_and_one_fetch() -> None:
    async def run() -> tuple[bool, int, int]:
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=EventBus())
        task_a = coord.schedule("AAPL", "1d", _T1, _T2)
        task_b = coord.schedule("AAPL", "1d", _T1, _T2)
        same = task_a is task_b
        await task_a
        return same, provider.fetch_calls, len(coord._in_flight)

    same, fetch_calls, in_flight = asyncio.run(run())
    assert same is True
    assert fetch_calls == 1
    assert in_flight == 0  # registry entry removed on completion


def test_same_key_different_range_coalesces_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="market_analyser.data.backfill")

    async def run() -> tuple[bool, int]:
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=EventBus())
        task_a = coord.schedule("AAPL", "1d", _T1, _T2)
        task_b = coord.schedule("AAPL", "1d", _T3, _T4)  # disjoint range — dropped
        same = task_a is task_b
        await task_a
        return same, provider.fetch_calls

    same, fetch_calls = asyncio.run(run())
    assert same is True
    assert fetch_calls == 1
    assert any("coalescing" in r.getMessage() for r in caplog.records)


def test_different_key_does_not_coalesce_and_runs_in_parallel() -> None:
    async def run() -> tuple[bool, int]:
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=EventBus())
        task_a = coord.schedule("AAPL", "1d", _T1, _T2)
        task_b = coord.schedule("MSFT", "1d", _T1, _T2)
        distinct = task_a is not task_b
        await asyncio.gather(task_a, task_b)
        return distinct, provider.fetch_calls

    distinct, fetch_calls = asyncio.run(run())
    assert distinct is True
    assert fetch_calls == 2


def test_completed_key_creates_a_new_task_next_time() -> None:
    async def run() -> bool:
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=EventBus())
        task1 = coord.schedule("AAPL", "1d", _T1, _T2)
        await task1
        task2 = coord.schedule("AAPL", "1d", _T1, _T2)
        is_new = task1 is not task2
        await task2
        return is_new

    assert asyncio.run(run()) is True


def test_success_publishes_started_then_backfilled_in_order() -> None:
    async def run() -> list[str]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        await coord.schedule("AAPL", "1d", _T1, _T2)
        first = await asyncio.wait_for(sub.next(), timeout=2)
        second = await asyncio.wait_for(sub.next(), timeout=2)
        empty = sub.queue.empty()
        sub.close()
        assert empty  # exactly two envelopes
        return [first.type, second.type]

    assert asyncio.run(run()) == ["ohlcv.backfill_started", "ohlcv.backfilled"]


def test_failure_publishes_one_backfill_failed_and_task_raises() -> None:
    async def run() -> tuple[bool, list[str], str, int]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _FakeBackfillProvider(
            gaps=[(_T1, _T2)],
            fetched=RateLimitedError("yahoo: rate limited (HTTP 429)", retry_after_seconds=60),
        )
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        task = coord.schedule("AAPL", "1d", _T1, _T2)
        raised = False
        try:
            await task
        except RateLimitedError:
            raised = True
        first = await asyncio.wait_for(sub.next(), timeout=2)
        second = await asyncio.wait_for(sub.next(), timeout=2)
        only_two = sub.queue.empty()
        sub.close()
        assert only_two
        return (
            raised,
            [first.type, second.type],
            str(second.payload["reason"]),
            len(coord._in_flight),
        )

    raised, types, reason, in_flight = asyncio.run(run())
    assert raised is True
    assert types == ["ohlcv.backfill_started", "ohlcv.backfill_failed"]
    assert reason == "rate_limited"
    assert in_flight == 0  # registry entry removed regardless of success/failure


def test_non_typed_failure_publishes_backfill_failed_as_upstream_unavailable() -> None:
    """A NON-UpstreamDataError from the fetch still publishes backfill_failed so
    the renderer spinner clears (Plan 0013 close-review m2): reason is
    upstream_unavailable, the task re-raises, and the registry entry is removed."""

    async def run() -> tuple[bool, list[str], str, int]:
        bus = EventBus()
        sub = bus.subscribe()
        provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=ValueError("boom"))
        coord = BackfillCoordinator(provider=provider, event_bus=bus)
        task = coord.schedule("AAPL", "1d", _T1, _T2)
        raised = False
        try:
            await task
        except ValueError:
            raised = True
        first = await asyncio.wait_for(sub.next(), timeout=2)
        second = await asyncio.wait_for(sub.next(), timeout=2)
        only_two = sub.queue.empty()
        sub.close()
        assert only_two
        return (
            raised,
            [first.type, second.type],
            str(second.payload["reason"]),
            len(coord._in_flight),
        )

    raised, types, reason, in_flight = asyncio.run(run())
    assert raised is True
    assert types == ["ohlcv.backfill_started", "ohlcv.backfill_failed"]
    assert reason == "upstream_unavailable"
    assert in_flight == 0


def test_two_coordinators_do_not_share_a_registry() -> None:
    """DI / no module-level singletons: independent coordinators are independent."""
    bus = EventBus()
    provider = _FakeBackfillProvider(gaps=[(_T1, _T2)], fetched=[_bar()])
    coord_a = BackfillCoordinator(provider=provider, event_bus=bus)
    coord_b = BackfillCoordinator(provider=provider, event_bus=bus)
    assert coord_a._in_flight is not coord_b._in_flight
