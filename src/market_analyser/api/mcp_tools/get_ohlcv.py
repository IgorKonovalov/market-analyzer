"""`get_ohlcv` MCP tool (Plans 0006/0013; extracted Plan 0017).

Reads cached OHLCV bars through the `MarketDataProvider` Protocol and fetches any
missing bars from the upstream on a cache miss before returning. `as_of` is fixed
to `None` (live mode) at this boundary so the anti-lookahead guarantee from
ADR-0007 is preserved at the MCP seam.

The body is factored out as `_get_ohlcv_response` so the backfill paths are
unit-testable on a single event loop (no live MCP server needed for the event
assertions).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.api.backfill_response import GetOhlcvResponse
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label

# The tool docstrings are agent UX (ADR-0015): the agent reads these to decide
# whether get_ohlcv can populate the cache. Plan 0013 fixes the old "from the
# local cache" wording that made the agent treat get_ohlcv as cache-only.
GET_OHLCV_DESCRIPTION = (
    "Read OHLCV bars for one symbol over a [start, end] window. Reads the local "
    "cache and fetches any missing bars from the upstream (Yahoo) on a cache "
    "miss before returning, so this tool populates the cache itself — no separate "
    "step is needed. Returns {bars, partial_reason, message}: partial_reason is "
    "null on full success, or a typed reason (rate_limited | upstream_unavailable "
    "| unknown_symbol) when only some gaps could be filled. Set backfill_async="
    "true to return whatever is already cached immediately and run the fetch in "
    "the background (partial_reason='backfill_async_pending'); progress then "
    "arrives on the event stream as ohlcv.backfilled / ohlcv.backfill_failed. "
    f"Live-mode only; supported timeframes: {supported_timeframes_label()}."
)


async def _get_ohlcv_response(
    *,
    provider: MarketDataProvider,
    coordinator: BackfillCoordinator | None,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    backfill_async: bool,
) -> GetOhlcvResponse:
    """Body of the `get_ohlcv` tool, factored out so the backfill paths are unit-
    testable on a single event loop (no live MCP server needed for the event
    assertions). Sync mode preserves today's fetch-on-miss behaviour."""
    # Validate at the MCP boundary like backfill_ohlcv does — bad input must
    # raise here, not slip into the async path where it would publish a
    # `started` event and then die without a `failed` (leaving the spinner stuck).
    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(start, end)
    if backfill_async:
        if coordinator is None:
            raise ValueError("backfill_async=true requires a cache-coverage-capable provider")
        cov = coordinator.coverage(symbol, timeframe, start, end)
        if not cov.gaps:
            # Cache already complete — return it, schedule nothing, publish nothing.
            return GetOhlcvResponse(bars=list(cov.cached), partial_reason=None, message=None)
        coordinator.schedule(symbol, timeframe, start, end)
        return GetOhlcvResponse(
            bars=list(cov.cached),
            partial_reason="backfill_async_pending",
            message=(
                "returned cached bars; a background backfill was scheduled — watch "
                "ohlcv.backfilled / ohlcv.backfill_failed on the event stream"
            ),
        )
    # Sync mode (default): fetch-on-miss, offloaded so it never blocks the loop.
    # With a coverage-capable provider, surface partial failures (some gaps
    # fetched, some failed) instead of failing loud; else fall back to the plain
    # fetch (legacy / coverage-less stub providers).
    if coordinator is not None:
        result = await asyncio.to_thread(
            coordinator.get_ohlcv_with_status, symbol, timeframe, start, end
        )
        return GetOhlcvResponse(
            bars=list(result.bars),
            partial_reason=result.partial_reason,
            message=result.message,
        )
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end)
    return GetOhlcvResponse(bars=list(bars), partial_reason=None, message=None)


def register_get_ohlcv(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    backfill_coordinator: BackfillCoordinator | None,
) -> None:
    """Bind the `get_ohlcv` tool to `server`. The provider and coordinator are
    captured by closure so the tool body keeps the declared parameters FastMCP
    introspects to build the input schema."""

    @server.tool(description=GET_OHLCV_DESCRIPTION)
    async def get_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        backfill_async: bool = False,
    ) -> GetOhlcvResponse:
        return await _get_ohlcv_response(
            provider=provider,
            coordinator=backfill_coordinator,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            backfill_async=backfill_async,
        )


__all__ = ["GET_OHLCV_DESCRIPTION", "_get_ohlcv_response", "register_get_ohlcv"]
