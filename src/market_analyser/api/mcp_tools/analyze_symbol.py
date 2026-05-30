"""`analyze_symbol` MCP tool (Plan 0018 phase 4, ADR-0023).

Fetches cached bars through the `MarketDataProvider` Protocol, runs the in-house
`condition_snapshot`, and returns the snapshot plus `analyzed_at`. `as_of` is
honoured — the window ends at `as_of` and is passed to the provider, which
truncates to `event_ts <= as_of`, so historical replay inherits the layer's
anti-lookahead guarantee for free.

The tool validates its inputs at the MCP boundary and dispatches only through the
provider (never SQLite or the adapters directly — ADR-0007). The synchronous
snapshot computation is offloaded with `asyncio.to_thread` (the `screener_query`
pattern) so it never stalls the event loop.

The body is factored out as `_analyze_symbol_response` so the fetch + empty-cache
paths are unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import ConditionSnapshot
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider

# Lookback units in days. Months/years are nominal fetch-window sizes, not
# calendar-exact — they only bound how far back to read bars.
_LOOKBACK_UNIT_DAYS = {"d": 1, "w": 7, "mo": 30, "y": 365}
_LOOKBACK_RE = re.compile(r"^(\d+)(d|w|mo|y)$")

ANALYZE_SYMBOL_DESCRIPTION = (
    "Compute a full technical-condition snapshot for one symbol over cached bars: "
    "trend (up/down/sideways), momentum stance, latest indicator values (RSI, "
    "MACD, Bollinger, ATR, ADX, Supertrend, plus trailing RSI/ATR percentiles), "
    "trailing support/resistance levels, and any candlestick patterns on the most "
    "recent bars. Returns {snapshot, partial_reason, message, analyzed_at}: "
    "snapshot is null with partial_reason='no_bars' when nothing is cached for the "
    "symbol (backfill via get_ohlcv first). `lookback` is like 6mo/1y/30d/2w. Pass "
    "`as_of` (ISO datetime) for historical replay — the read is trailing, so no "
    "future bar leaks in. Conditions only — never buy/sell advice. Supported "
    "timeframes: 1d, 1h."
)


class AnalyzeSymbolResponse(BaseModel):
    """`analyze_symbol` result. `snapshot` is the condition read, or `None` with
    `partial_reason="no_bars"` when the cache holds nothing for the symbol over the
    window. `analyzed_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: ConditionSnapshot | None
    partial_reason: Literal["no_bars"] | None
    message: str | None
    analyzed_at: datetime


def _parse_lookback(lookback: str) -> timedelta:
    match = _LOOKBACK_RE.match(lookback)
    if match is None:
        raise ValueError(
            f"lookback {lookback!r} is malformed — expected <int><unit> with unit in "
            "d/w/mo/y (e.g. 6mo, 1y, 30d)",
        )
    count, unit = int(match.group(1)), match.group(2)
    if count < 1:
        raise ValueError(f"lookback {lookback!r} must be a positive duration")
    return timedelta(days=count * _LOOKBACK_UNIT_DAYS[unit])


async def _analyze_symbol_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    lookback: str,
    as_of: datetime | None,
) -> AnalyzeSymbolResponse:
    """Body of the `analyze_symbol` tool. Validates at the boundary, reads bars
    through the provider, and composes the snapshot off-thread."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    window = _parse_lookback(lookback)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - window
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return AnalyzeSymbolResponse(
            snapshot=None,
            partial_reason="no_bars",
            message=(
                f"no cached bars for {symbol} {timeframe} over the last {lookback}; "
                "backfill via get_ohlcv first"
            ),
            analyzed_at=now,
        )
    snapshot = await asyncio.to_thread(condition_snapshot, list(bars), timeframe)
    return AnalyzeSymbolResponse(
        snapshot=snapshot, partial_reason=None, message=None, analyzed_at=now
    )


def register_analyze_symbol(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `analyze_symbol` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects to build the
    input schema."""

    @server.tool(description=ANALYZE_SYMBOL_DESCRIPTION)
    async def analyze_symbol(
        symbol: str,
        timeframe: str,
        lookback: str = "6mo",
        as_of: datetime | None = None,
    ) -> AnalyzeSymbolResponse:
        return await _analyze_symbol_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            as_of=as_of,
        )


__all__ = [
    "ANALYZE_SYMBOL_DESCRIPTION",
    "AnalyzeSymbolResponse",
    "_analyze_symbol_response",
    "register_analyze_symbol",
]
