"""`market_structure` MCP tool (Plan 0092 phase 4, ADR-0084, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and runs `analysis.structure.market_structure`, returning the price-action
market-structure read — the HH/HL/LH/LL labeled swing sequence, the derived
`structural_trend`, and the BOS/CHoCH events. This is the ADR-0084 *second, distinct*
trend read, reported alongside (never merged into) the indicator `trend` that
`analyze_symbol` reports. `result` is `None` with `partial_reason="no_bars"` when
nothing is cached.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous read is offloaded with `asyncio.to_thread`.

The body is factored as `_market_structure_response` so the fetch / empty-cache
paths are unit-testable on a single event loop (no live MCP server needed).
Conditions only — a structural read is chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.structure import market_structure as compute_market_structure
from market_analyser.analysis.types import MarketStructure
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label

# Fetch window: the timeframe's feed-limited history, or a generous default for the
# unbounded cadences — wide enough for the swing sequence to build up.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

MARKET_STRUCTURE_DESCRIPTION = (
    "Read the price-action market structure on one symbol's cached bars: labels the "
    "confirmed swing sequence HH/HL/LH/LL, derives structural_trend (up = HH+HL, "
    "down = LH+LL, else range), and detects BOS (in-trend break) and CHoCH (first "
    "counter-trend break) events. Returns {result, partial_reason, scanned_at}: "
    "result is a MarketStructure with structural_trend, labeled_pivots, and events "
    "(each with kind, direction, the first-knowable bar_index, and the broken "
    "price). This is a SECOND, distinct trend read reported ALONGSIDE the indicator "
    "trend from analyze_symbol — the two may legitimately disagree, and that "
    "disagreement is itself the signal (never merged). result is null with "
    "partial_reason='no_bars' when nothing is cached (backfill via get_ohlcv "
    "first). Strictly trailing: a label/event at bar i reads only bars up to i. "
    "Pass `as_of` for historical replay (no future leak). Conditions only — never "
    f"buy/sell advice. Supported timeframes: {supported_timeframes_label()}."
)


class MarketStructureResponse(BaseModel):
    """`market_structure` result. `result` is the price-action structural read, or
    `None` with `partial_reason="no_bars"` when the cache holds nothing for the
    symbol. `scanned_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: MarketStructure | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


async def _market_structure_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    as_of: datetime | None,
) -> MarketStructureResponse:
    """Body of the `market_structure` tool. Validates at the boundary, reads bars
    through the provider, and runs the trailing structural read off the bars."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return MarketStructureResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(compute_market_structure, list(bars))
    return MarketStructureResponse(result=result, partial_reason=None, scanned_at=now)


def register_market_structure(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `market_structure` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="market_structure", description=MARKET_STRUCTURE_DESCRIPTION)
    async def market_structure_tool(
        symbol: str,
        timeframe: str,
        as_of: datetime | None = None,
    ) -> MarketStructureResponse:
        return await _market_structure_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
        )


__all__ = [
    "MARKET_STRUCTURE_DESCRIPTION",
    "MarketStructureResponse",
    "_market_structure_response",
    "register_market_structure",
]
