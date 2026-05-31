"""`multi_timeframe_analysis` MCP tool (Plan 0021 phase 1, ADR-0023).

Fetches cached bars for one symbol on each requested timeframe through the
`MarketDataProvider` Protocol, runs the in-house `multi_timeframe_alignment`, and
returns the alignment plus `analyzed_at`. `as_of` is honoured per timeframe — the
window ends at `as_of` and is passed to the provider, which truncates to
`event_ts <= as_of`, so historical replay inherits the layer's anti-lookahead
guarantee for free.

The per-timeframe fetch window is derived from each timeframe's `max_history`
(the deepest reach the feed has for that cadence), or a generous default for the
unbounded cadences (daily, weekly), so the snapshot's longest indicator leg
(EMA-50, ADX) has enough trailing bars without a hand-tuned lookback per
timeframe. The tool validates its inputs at the MCP boundary and dispatches only
through the provider (never SQLite or the adapters directly — ADR-0007). The fetch
and the synchronous alignment are offloaded with `asyncio.to_thread` (the
`analyze_symbol` pattern) so neither stalls the event loop.

The body is factored out as `_multi_timeframe_response` so the fetch + missing-
timeframe paths are unit-testable on a single event loop (no live MCP server).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.multi_timeframe import multi_timeframe_alignment
from market_analyser.analysis.types import MultiTimeframeAlignment
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label
from market_analyser.data.types import Bar

# Cadence-descending ladder (weekly → daily → 4h → 1h → 15m): the default the
# plan names. A list (not a frozenset) so the alignment preserves a stable,
# readable timeframe order.
_DEFAULT_LADDER: list[str] = ["1w", "1d", "4h", "1h", "15m"]

# Fetch window for the unbounded cadences (daily, weekly), which have no
# `max_history` cap — five years gives weekly ~260 bars and daily its full
# cached reach, comfortably past the EMA-50 / ADX warm-up.
_DEFAULT_UNBOUNDED_WINDOW = timedelta(days=5 * 365)

MULTI_TIMEFRAME_DESCRIPTION = (
    "Report whether one symbol's trend is aligned across a ladder of timeframes. "
    "Runs the full condition snapshot per timeframe and returns "
    "{alignment, analyzed_at}: alignment.timeframes carries each timeframe's "
    "snapshot (null when nothing is cached for that timeframe — backfill via "
    "get_ohlcv first), alignment.dominant_trend is the trend held by the most "
    "timeframes, and alignment.agreement is the 0..1 fraction of available "
    "timeframes that agree with it. Default ladder is weekly/daily/4h/1h/15m; "
    "pass `timeframes` to override. Pass `as_of` (ISO datetime) for historical "
    "replay — each per-timeframe read is trailing, so no future bar leaks in. "
    f"Conditions only — never buy/sell advice. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


class MultiTimeframeAnalysisResponse(BaseModel):
    """`multi_timeframe_analysis` result. `alignment` is the cross-timeframe trend
    summary (per-timeframe snapshots, dominant trend, agreement score);
    `analyzed_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alignment: MultiTimeframeAlignment
    analyzed_at: datetime


def _window_for(timeframe: str) -> timedelta:
    """The fetch window for `timeframe`: its feed-limited `max_history`, or the
    unbounded default for daily/weekly. Wide enough for the longest snapshot leg."""

    cap = max_history(timeframe)
    return cap if cap is not None else _DEFAULT_UNBOUNDED_WINDOW


async def _multi_timeframe_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframes: list[str],
    as_of: datetime | None,
) -> MultiTimeframeAnalysisResponse:
    """Body of the `multi_timeframe_analysis` tool. Validates at the boundary,
    reads bars per timeframe through the provider, and composes the alignment
    off-thread."""

    _require_non_empty_symbol(symbol)
    if not timeframes:
        raise ValueError("timeframes must be a non-empty list")
    for timeframe in timeframes:
        _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    bars_by_timeframe: dict[str, list[Bar]] = {}
    for timeframe in timeframes:
        start = end - _window_for(timeframe)
        bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
        bars_by_timeframe[timeframe] = list(bars)

    alignment = await asyncio.to_thread(multi_timeframe_alignment, symbol, bars_by_timeframe)
    return MultiTimeframeAnalysisResponse(alignment=alignment, analyzed_at=datetime.now(tz=UTC))


def register_multi_timeframe_analysis(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `multi_timeframe_analysis` tool to `server`. The provider is
    captured by closure so the tool body keeps the parameters FastMCP introspects
    to build the input schema."""

    @server.tool(description=MULTI_TIMEFRAME_DESCRIPTION)
    async def multi_timeframe_analysis(
        symbol: str,
        timeframes: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> MultiTimeframeAnalysisResponse:
        # None -> the default weekly/daily/4h/1h/15m ladder (a fresh list, never a
        # shared mutable default); an explicit [] reaches the body and is rejected.
        ladder = list(_DEFAULT_LADDER) if timeframes is None else timeframes
        return await _multi_timeframe_response(
            provider=provider,
            symbol=symbol,
            timeframes=ladder,
            as_of=as_of,
        )


__all__ = [
    "MULTI_TIMEFRAME_DESCRIPTION",
    "MultiTimeframeAnalysisResponse",
    "_multi_timeframe_response",
    "register_multi_timeframe_analysis",
]
