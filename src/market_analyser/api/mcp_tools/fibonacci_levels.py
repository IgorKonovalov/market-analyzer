"""`fibonacci_levels` MCP tool (Plan 0092 phase 4, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and computes a Fibonacci retracement (default) or extension grid, auto-
anchored to the dominant recent swing (`analysis.fibonacci.dominant_swing`). For an
extension, the impulse is that dominant swing and the pullback anchor is the last
bar's close (the extension is projected off current price). `result` is `None` with
`partial_reason="no_bars"` when nothing is cached, or `"no_swing"` when the bars
hold no dominant swing to anchor to (an honest miss, never a fabricated grid).

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous computation is offloaded with `asyncio.to_thread`.

The body is factored as `_fibonacci_levels_response` so the fetch / empty-cache /
no-swing paths are unit-testable on a single event loop (no live MCP server).
Conditions only — a fib grid is chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.fibonacci import (
    dominant_swing,
    fibonacci_extension,
    fibonacci_retracement,
)
from market_analyser.analysis.types import FibonacciLevels, PivotPoint
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label
from market_analyser.data.types import Bar

# Fetch window: the timeframe's feed-limited history, or a generous default for the
# unbounded cadences — wide enough for the auto-anchor's swing lookback.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

FibKind = Literal["retracement", "extension"]

FIBONACCI_LEVELS_DESCRIPTION = (
    "Compute a Fibonacci grid on one symbol's cached bars, auto-anchored to the "
    "dominant recent swing. Returns {result, partial_reason, scanned_at}: result is "
    "a FibonacciLevels — its kind (retracement or extension), the high/low swing "
    "anchors, the swing direction, and the levels map (ratio string -> price, e.g. "
    "'0.618'). kind='retracement' (default) draws the levels inside the swing; "
    "kind='extension' projects the levels beyond it, off the last close. result is "
    "null with partial_reason='no_bars' when nothing is cached (backfill via "
    "get_ohlcv first), or 'no_swing' when the bars hold no dominant swing to anchor "
    "to. Strictly trailing: the auto-anchor reads only confirmed pivots. Pass "
    "`as_of` for historical replay (no future leak). Conditions only — never "
    f"buy/sell advice. Supported timeframes: {supported_timeframes_label()}."
)


class FibonacciLevelsResponse(BaseModel):
    """`fibonacci_levels` result. `result` is the auto-anchored grid, or `None` with
    `partial_reason` ``no_bars`` (nothing cached) / ``no_swing`` (no dominant swing).
    `scanned_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: FibonacciLevels | None
    partial_reason: Literal["no_bars", "no_swing"] | None
    scanned_at: datetime


def _fibonacci_levels(bars: Sequence[Bar], kind: FibKind) -> FibonacciLevels | None:
    """The synchronous core: auto-anchor to the dominant swing and build the grid,
    or `None` when there is no dominant swing. For an extension the pullback anchor
    is the last bar's close."""

    swing = dominant_swing(bars)
    if swing is None:
        return None
    high_anchor, low_anchor = swing
    if kind == "extension":
        last = bars[-1]
        pullback = PivotPoint(ts=last.event_ts, price=last.close)
        return fibonacci_extension(high_anchor, low_anchor, pullback)
    return fibonacci_retracement(high_anchor, low_anchor)


async def _fibonacci_levels_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    kind: FibKind,
    as_of: datetime | None,
) -> FibonacciLevelsResponse:
    """Body of the `fibonacci_levels` tool. Validates at the boundary, reads bars
    through the provider, and builds the auto-anchored grid off the fetched bars."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return FibonacciLevelsResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(_fibonacci_levels, list(bars), kind)
    if result is None:
        return FibonacciLevelsResponse(result=None, partial_reason="no_swing", scanned_at=now)
    return FibonacciLevelsResponse(result=result, partial_reason=None, scanned_at=now)


def register_fibonacci_levels(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `fibonacci_levels` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="fibonacci_levels", description=FIBONACCI_LEVELS_DESCRIPTION)
    async def fibonacci_levels_tool(
        symbol: str,
        timeframe: str,
        kind: FibKind = "retracement",
        as_of: datetime | None = None,
    ) -> FibonacciLevelsResponse:
        return await _fibonacci_levels_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            kind=kind,
            as_of=as_of,
        )


__all__ = [
    "FIBONACCI_LEVELS_DESCRIPTION",
    "FibonacciLevelsResponse",
    "_fibonacci_levels_response",
    "register_fibonacci_levels",
]
