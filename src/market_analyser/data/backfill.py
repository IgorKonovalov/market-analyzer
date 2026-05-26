"""Backfill coordination (Plan 0013).

**Phase 2 scope (this file as it stands):** a *placeholder* `BackfillCoordinator`
that schedules one background fetch per `schedule()` call (no `(symbol, timeframe)`
dedup yet) and publishes the `ohlcv.backfill_*` event sequence around it. Phase 3
adds the in-flight `(symbol, timeframe)` registry, coalescing, and partial-failure
surfacing.

The coordinator depends on the narrow `SupportsBackfill` interface — `get_ohlcv`
(the existing sync fetch-on-miss path) plus `coverage` (the cache-only read +
gap computation Plan 0013 added to `DefaultMarketDataProvider`). Keeping it narrow
(rather than the full `MarketDataProvider` Protocol) means the broad Protocol — and
the ~14 fakes that implement it — stay untouched.

Layering note: this module imports the `EventBus` + payloads from
`market_analyser.api.events` (a data→api reach) because the plan designs the
coordinator to publish backfill progress directly. The reach is confined here;
`default_provider` stays free of any api import.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from market_analyser.api.events import (
    EventBus,
    GapWindow,
    OhlcvBackfilledPayloadV1,
    OhlcvBackfillFailedPayloadV1,
    OhlcvBackfillStartedPayloadV1,
)
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
)
from market_analyser.data.types import Bar, Coverage

FailureReason = Literal["rate_limited", "upstream_unavailable", "unknown_symbol"]


@runtime_checkable
class SupportsBackfill(Protocol):
    """The narrow provider capability the coordinator needs: the sync fetch path
    plus a cache-only coverage read. `DefaultMarketDataProvider` satisfies it; the
    broad `MarketDataProvider` Protocol is deliberately NOT widened with these."""

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


def _reason_for(err: UpstreamDataError) -> FailureReason:
    """Map a typed upstream error onto the closed `ohlcv.backfill_failed` reason set."""
    if isinstance(err, RateLimitedError):
        return "rate_limited"
    if isinstance(err, UnknownSymbolError):
        return "unknown_symbol"
    return "upstream_unavailable"


class BackfillCoordinator:
    """Schedules background OHLCV backfills and publishes their progress.

    Phase 2 placeholder: each `schedule()` creates a fresh `asyncio.Task` with no
    `(symbol, timeframe)` dedup. The task publishes `ohlcv.backfill_started`
    (before the fetch), then either `ohlcv.backfilled` (success) or
    `ohlcv.backfill_failed` (typed upstream error). Phase 3 swaps in the in-flight
    registry + coalescing + partial-failure surfacing.

    DI only — takes the provider + event bus as constructor args, no singletons.
    """

    def __init__(self, *, provider: SupportsBackfill, event_bus: EventBus) -> None:
        self._provider = provider
        self._event_bus = event_bus

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

    def schedule(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> asyncio.Task[None]:
        """Schedule a background backfill for the window. Returns the task (the
        caller does not await it; callers that want the result join via the bus)."""
        return asyncio.create_task(self._run_backfill(symbol, timeframe, start, end))

    async def _run_backfill(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> None:
        cov = self._provider.coverage(symbol, timeframe, start, end)
        gaps = [GapWindow(start=gap_start, end=gap_end) for gap_start, gap_end in cov.gaps]
        self._event_bus.publish(
            "ohlcv.backfill_started",
            OhlcvBackfillStartedPayloadV1(symbol=symbol, timeframe=timeframe, gaps=gaps),
        )
        try:
            # The provider's fetch path is sync; offload so it never blocks the loop.
            bars = await asyncio.to_thread(self._provider.get_ohlcv, symbol, timeframe, start, end)
        except UpstreamDataError as err:
            self._event_bus.publish(
                "ohlcv.backfill_failed",
                OhlcvBackfillFailedPayloadV1(
                    symbol=symbol,
                    timeframe=timeframe,
                    reason=_reason_for(err),
                    message=str(err),
                ),
            )
            return
        bars_added = max(0, len(bars) - len(cov.cached))
        self._event_bus.publish(
            "ohlcv.backfilled",
            OhlcvBackfilledPayloadV1(
                symbol=symbol,
                timeframe=timeframe,
                range_start=start,
                range_end=end,
                bars_added=bars_added,
            ),
        )


__all__ = ["BackfillCoordinator", "SupportsBackfill"]
