"""`backfill_ohlcv` MCP tool (Plan 0013; extracted Plan 0017).

Pre-warms the local cache for a symbol/timeframe over a window by scheduling a
background fetch of any missing bars. The body is factored out as
`_backfill_ohlcv_response` for single-loop unit tests.
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.api.backfill_response import BackfillOhlcvResponse
from market_analyser.api.events import GapWindow
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.backfill import BackfillCoordinator

BACKFILL_OHLCV_DESCRIPTION = (
    "Pre-warm the local cache for a symbol/timeframe over [start, end] by "
    "fetching any missing bars from the upstream in the background. Returns "
    "immediately with {started, gaps, message}: started=true plus the gap "
    "windows when a background fetch was scheduled, or started=false and an "
    "empty gaps list when the cache already covers the window. Watch the event "
    "stream — ohlcv.backfill_started fires first, then ohlcv.backfilled on "
    "success or ohlcv.backfill_failed (reason: rate_limited | upstream_unavailable "
    "| unknown_symbol) on failure."
)


async def _backfill_ohlcv_response(
    *,
    coordinator: BackfillCoordinator | None,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> BackfillOhlcvResponse:
    """Body of the `backfill_ohlcv` tool, factored out for single-loop unit tests.
    Validates input at the boundary, then schedules a background fetch only when
    the cache actually has gaps."""
    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(start, end)
    if coordinator is None:
        raise ValueError("backfill_ohlcv requires a cache-coverage-capable provider")
    cov = coordinator.coverage(symbol, timeframe, start, end)
    if not cov.gaps:
        return BackfillOhlcvResponse(
            started=False,
            gaps=[],
            message="cache already covers the requested window; nothing to fetch",
        )
    coordinator.schedule(symbol, timeframe, start, end)
    return BackfillOhlcvResponse(
        started=True,
        gaps=[GapWindow(start=gap_start, end=gap_end) for gap_start, gap_end in cov.gaps],
        message=(
            "backfill scheduled in the background — watch ohlcv.backfilled / "
            "ohlcv.backfill_failed on the event stream"
        ),
    )


def register_backfill_ohlcv(
    server: FastMCP,
    *,
    backfill_coordinator: BackfillCoordinator | None,
) -> None:
    """Bind the `backfill_ohlcv` tool to `server`. The coordinator is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(description=BACKFILL_OHLCV_DESCRIPTION)
    async def backfill_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillOhlcvResponse:
        return await _backfill_ohlcv_response(
            coordinator=backfill_coordinator,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )


__all__ = [
    "BACKFILL_OHLCV_DESCRIPTION",
    "_backfill_ohlcv_response",
    "register_backfill_ohlcv",
]
