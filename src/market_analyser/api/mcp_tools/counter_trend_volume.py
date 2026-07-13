"""`counter_trend_volume` MCP tool (Plan 0090 phase 4, ADR-0083).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol, classifies the trend with the snapshot's canonical classifier
(`condition_snapshot(...).trend` — EMA/ADX + Ichimoku veto, the same label
`analyze_symbol` reports), and runs `analysis.volume.counter_trend_volume` anchored
to that trend. It returns the per-bar decomposition (each bar's direction, trailing
relative volume, counter-trend flag) plus the aggregate counter-trend volume share.
`result` is `None` with `partial_reason="no_bars"` when nothing is cached.

The anchor is the snapshot trend, not the net move — so "counter-trend" here means
one thing across the whole surface (ADR-0083). When the anchor trend is `sideways`
there is no trend to run counter to, and the read says so (`anchored_to_sideways`,
share `None`), rather than forcing a net-move sign.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous read is offloaded with `asyncio.to_thread`.

The body is factored as `_counter_trend_volume_response` so the fetch / empty-cache
paths are unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import CounterTrendVolume
from market_analyser.analysis.volume import COUNTER_TREND_LOOKBACK, counter_trend_volume
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label
from market_analyser.data.types import Bar

# Fetch window: the timeframe's feed-limited history, or a generous default for
# the unbounded cadences — wide enough for the trend classifier's longest window
# (Ichimoku span_b + displacement) plus the counter-trend lookback.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

COUNTER_TREND_VOLUME_DESCRIPTION = (
    "Decompose one symbol's recent volume into with-trend vs counter-trend on "
    "cached bars, anchored to the symbol's canonical trend (the same up/down/"
    "sideways label analyze_symbol reports). Returns {result, partial_reason, "
    "scanned_at}: result.bars lists each of the trailing `lookback` bars with its "
    "direction (close-vs-open), trailing relative volume, and a counter-trend flag, "
    "and result.counter_trend_volume_share is the share of directional volume on "
    "the counter-trend bars (high = a volume divergence against the trend). When "
    "the trend is sideways there is nothing to run counter to: anchored_to_sideways "
    "is true and the share is null (undefined, not forced). result is null with "
    "partial_reason='no_bars' when nothing is cached (backfill via get_ohlcv "
    "first). Pass `as_of` for historical replay (trailing — no future leak). "
    "Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class CounterTrendVolumeResponse(BaseModel):
    """`counter_trend_volume` result. `result` is the trend-anchored decomposition,
    or `None` with `partial_reason="no_bars"` when the cache holds nothing for the
    symbol. `scanned_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: CounterTrendVolume | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


def _counter_trend_volume(bars: list[Bar], timeframe: str, lookback: int) -> CounterTrendVolume:
    """Classify the trend off the snapshot (the canonical anchor) and decompose the
    trailing window against it — the synchronous core, run off-thread."""

    trend = condition_snapshot(bars, timeframe).trend
    return counter_trend_volume(bars, trend, lookback)


async def _counter_trend_volume_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    lookback: int,
    as_of: datetime | None,
) -> CounterTrendVolumeResponse:
    """Body of the `counter_trend_volume` tool. Validates at the boundary, reads
    bars through the provider, and computes the decomposition off the fetched
    bars (anchored to the snapshot trend)."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return CounterTrendVolumeResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(_counter_trend_volume, list(bars), timeframe, lookback)
    return CounterTrendVolumeResponse(result=result, partial_reason=None, scanned_at=now)


def register_counter_trend_volume(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `counter_trend_volume` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    # Explicit `name=` so the MCP tool is `counter_trend_volume` regardless of the
    # closure's Python function name (suffixed to avoid shadowing the import).
    @server.tool(name="counter_trend_volume", description=COUNTER_TREND_VOLUME_DESCRIPTION)
    async def counter_trend_volume_tool(
        symbol: str,
        timeframe: str,
        lookback: int = COUNTER_TREND_LOOKBACK,
        as_of: datetime | None = None,
    ) -> CounterTrendVolumeResponse:
        return await _counter_trend_volume_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            as_of=as_of,
        )


__all__ = [
    "COUNTER_TREND_VOLUME_DESCRIPTION",
    "CounterTrendVolumeResponse",
    "_counter_trend_volume_response",
    "register_counter_trend_volume",
]
