"""Backfill coordination (Plan 0013 phase 3).

`BackfillCoordinator` owns an in-flight `(symbol, timeframe)` → task registry so
bursty same-symbol calls coalesce onto one upstream fetch instead of multiplying
load. Each backfill publishes `ohlcv.backfill_started` (before the fetch) then
`ohlcv.backfilled` (success) or `ohlcv.backfill_failed` (typed upstream error);
on failure the task itself re-raises the typed error so a caller that awaits it
sees the failure, while fire-and-forget callers don't trip asyncio's
"exception never retrieved" warning.

The coordinator depends on the narrow `SupportsBackfill` interface — the existing
`get_ohlcv` (fail-loud) fetch path, the cache-only `coverage` read, and the
partial-surfacing `get_ohlcv_with_status`. Keeping it narrow (rather than the full
`MarketDataProvider` Protocol) means the broad Protocol — and the fakes that
implement it — stay untouched.

Layering note: this module imports the `EventBus` + payloads from
`market_analyser.api.events` (a data→api reach) because the plan designs the
coordinator to publish backfill progress directly. The reach is confined here;
`default_provider` stays free of any api import.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from market_analyser.api.events import (
    EventBus,
    GapWindow,
    OhlcvBackfilledPayloadV1,
    OhlcvBackfillFailedPayloadV1,
    OhlcvBackfillStartedPayloadV1,
)
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.types import BackfillResult, Bar, Coverage

_logger = logging.getLogger(__name__)


@runtime_checkable
class SupportsBackfill(Protocol):
    """The narrow provider capability the coordinator + backfill tools need: the
    sync fetch path (`get_ohlcv`, fail-loud), the cache-only `coverage` read, and
    the partial-surfacing `get_ohlcv_with_status`. `DefaultMarketDataProvider`
    satisfies it; the broad `MarketDataProvider` Protocol is deliberately NOT
    widened with these (so its fakes stay untouched)."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]: ...

    def coverage(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Coverage: ...

    def get_ohlcv_with_status(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillResult: ...


@dataclass
class _InFlight:
    task: asyncio.Task[BackfillResult]
    start: datetime
    end: datetime


def _consume_task_exception(task: asyncio.Task[BackfillResult]) -> None:
    """Retrieve a failed fire-and-forget task's exception so asyncio doesn't log
    'Task exception was never retrieved'. Callers that DO await the task still get
    the exception re-raised (retrieval suppresses the warning, not the raise)."""
    if not task.cancelled():
        task.exception()


class BackfillCoordinator:
    """Schedules background OHLCV backfills with `(symbol, timeframe)` dedup and
    publishes their progress. DI only — provider + event bus are constructor args,
    no module-level singletons."""

    def __init__(self, *, provider: SupportsBackfill, event_bus: EventBus) -> None:
        self._provider = provider
        self._event_bus = event_bus
        self._in_flight: dict[tuple[str, str], _InFlight] = {}

    def coverage(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Coverage:
        """Cache-only coverage read (delegates to the provider) — lets the MCP
        tools decide whether to schedule and report gaps without fetching."""
        return self._provider.coverage(symbol, timeframe, start, end)

    def get_ohlcv_with_status(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillResult:
        """Synchronous fetch-on-miss that surfaces partial failures (delegates to
        the provider) — backs the `get_ohlcv` tool's default (sync) path."""
        return self._provider.get_ohlcv_with_status(symbol, timeframe, start, end)

    def schedule(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> asyncio.Task[BackfillResult]:
        """Schedule a background backfill. Coalesces on `(symbol, timeframe)`: a
        call while one is already in flight for the same key returns the SAME task
        (the in-flight range wins; a differing requested range is dropped with a
        WARN). The caller does not await the returned task — fire-and-forget;
        progress arrives on the event bus."""
        key = (symbol, timeframe)
        existing = self._in_flight.get(key)
        if existing is not None:
            if (existing.start, existing.end) != (start, end):
                _logger.warning(
                    "backfill for %s/%s already in flight over [%s, %s]; coalescing "
                    "and dropping the newly requested [%s, %s]",
                    symbol,
                    timeframe,
                    existing.start.isoformat(),
                    existing.end.isoformat(),
                    start.isoformat(),
                    end.isoformat(),
                )
            return existing.task
        task: asyncio.Task[BackfillResult] = asyncio.create_task(
            self._run_backfill(symbol, timeframe, start, end),
        )
        self._in_flight[key] = _InFlight(task=task, start=start, end=end)
        task.add_done_callback(_consume_task_exception)
        return task

    async def _run_backfill(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillResult:
        key = (symbol, timeframe)
        try:
            cov = self._provider.coverage(symbol, timeframe, start, end)
            gaps = [GapWindow(start=gap_start, end=gap_end) for gap_start, gap_end in cov.gaps]
            self._event_bus.publish(
                "ohlcv.backfill_started",
                OhlcvBackfillStartedPayloadV1(symbol=symbol, timeframe=timeframe, gaps=gaps),
            )
            try:
                # Fail-loud path: any gap failure raises; the async backfill
                # surfaces it as ohlcv.backfill_failed (partial surfacing is the
                # sync get_ohlcv path's job, via get_ohlcv_with_status).
                bars = await asyncio.to_thread(
                    self._provider.get_ohlcv, symbol, timeframe, start, end
                )
            except UpstreamDataError as err:
                self._event_bus.publish(
                    "ohlcv.backfill_failed",
                    OhlcvBackfillFailedPayloadV1(
                        symbol=symbol,
                        timeframe=timeframe,
                        reason=failure_reason(err),
                        message=str(err),
                    ),
                )
                raise
            result = BackfillResult(bars=list(bars), partial_reason=None, message=None)
            self._event_bus.publish(
                "ohlcv.backfilled",
                OhlcvBackfilledPayloadV1(
                    symbol=symbol,
                    timeframe=timeframe,
                    range_start=start,
                    range_end=end,
                    bars_added=max(0, len(result.bars) - len(cov.cached)),
                ),
            )
            return result
        finally:
            # Remove the registry entry as part of the coroutine (not a done
            # callback) so `len(coordinator._in_flight) == 0` holds immediately
            # after `await task`, on success and failure alike.
            self._in_flight.pop(key, None)


__all__ = ["BackfillCoordinator", "SupportsBackfill"]
