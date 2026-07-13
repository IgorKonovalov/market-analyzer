"""`pivot_points` MCP tool (Plan 0092 phase 4, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and computes classic floor / Camarilla / Woodie pivot levels
(`analysis.levels.pivot_points`) from the last completed bar's HLC. `result` is
`None` with `partial_reason="no_bars"` when nothing is cached.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of`, so the "prior completed period" resolves to
the last bar at-or-before `as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous computation is offloaded with `asyncio.to_thread`.

The body is factored as `_pivot_points_response` so the fetch / empty-cache paths
are unit-testable on a single event loop (no live MCP server needed). Conditions
only — pivot levels are chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.levels import PivotMethod, pivot_points
from market_analyser.analysis.types import PivotPoints
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label

# Fetch window: the timeframe's feed-limited history, or a generous default. Only
# the last bar feeds the pivots, but a wide window makes the empty-cache miss honest.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

PIVOT_POINTS_DESCRIPTION = (
    "Compute classic pivot levels on one symbol's cached bars from the last "
    "completed bar's high/low/close (the prior-completed-period default). Returns "
    "{result, partial_reason, scanned_at}: result is a PivotPoints with the method, "
    "the central pivot, resistances [R1, R2, R3], and supports [S1, S2, S3]. "
    "method='floor' (default), 'camarilla', or 'woodie' selects the formula set. "
    "result is null with partial_reason='no_bars' when nothing is cached (backfill "
    "via get_ohlcv first). Trailing — reads only the last completed bar. Pass "
    "`as_of` for historical replay (the prior completed period as of that time). "
    "Conditions only — never buy/sell advice. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


class PivotPointsResponse(BaseModel):
    """`pivot_points` result. `result` is the pivot level set, or `None` with
    `partial_reason="no_bars"` when the cache holds nothing for the symbol.
    `scanned_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: PivotPoints | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


async def _pivot_points_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    method: PivotMethod,
    as_of: datetime | None,
) -> PivotPointsResponse:
    """Body of the `pivot_points` tool. Validates at the boundary, reads bars
    through the provider, and computes the pivots off the last fetched bar."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return PivotPointsResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(pivot_points, list(bars), method)
    return PivotPointsResponse(result=result, partial_reason=None, scanned_at=now)


def register_pivot_points(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `pivot_points` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="pivot_points", description=PIVOT_POINTS_DESCRIPTION)
    async def pivot_points_tool(
        symbol: str,
        timeframe: str,
        method: PivotMethod = "floor",
        as_of: datetime | None = None,
    ) -> PivotPointsResponse:
        return await _pivot_points_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            method=method,
            as_of=as_of,
        )


__all__ = [
    "PIVOT_POINTS_DESCRIPTION",
    "PivotPointsResponse",
    "_pivot_points_response",
    "register_pivot_points",
]
