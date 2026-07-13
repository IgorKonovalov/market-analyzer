"""`anchored_vwap` MCP tool (Plan 0092 phase 4, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and computes the anchored VWAP (`analysis.volume.anchored_vwap_value`) —
the volume-weighted average of the typical price accumulated from a chosen anchor
bar, dynamic support/resistance with a fixed start (distinct from the rolling
`vwap`). When `anchor_index` is omitted the anchor auto-selects the start of the
dominant recent swing (the swing's earlier pivot), falling back to the first bar
when no dominant swing exists. `result` is `None` with `partial_reason="no_bars"`
when nothing is cached.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous computation is offloaded with `asyncio.to_thread`.

The body is factored as `_anchored_vwap_response` so the fetch / empty-cache /
auto-anchor paths are unit-testable on a single event loop (no live MCP server).
Conditions only — anchored VWAP is chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.fibonacci import dominant_swing
from market_analyser.analysis.types import AnchoredVwapValue
from market_analyser.analysis.volume import anchored_vwap_value
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

ANCHORED_VWAP_DESCRIPTION = (
    "Compute the anchored VWAP on one symbol's cached bars: the volume-weighted "
    "average of the typical price accumulated from a chosen anchor bar to the last "
    "bar (dynamic support/resistance with a fixed start, unlike the rolling vwap). "
    "Returns {result, partial_reason, scanned_at}: result is an AnchoredVwapValue "
    "with the anchor_index, anchor_ts, and the latest value (null if the volume "
    "accumulated from the anchor is zero). Omit `anchor_index` to auto-anchor to "
    "the start of the dominant recent swing (first bar when there is no swing), or "
    "pass an explicit 0-based `anchor_index`. result is null with "
    "partial_reason='no_bars' when nothing is cached (backfill via get_ohlcv "
    "first). Trailing — the value at bar i reads only anchor..i. Pass `as_of` for "
    "historical replay (no future leak). Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class AnchoredVwapResponse(BaseModel):
    """`anchored_vwap` result. `result` is the latest anchored VWAP with its anchor
    provenance, or `None` with `partial_reason="no_bars"` when the cache holds
    nothing for the symbol. `scanned_at` is the wall-clock run time (run
    provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: AnchoredVwapValue | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


def _resolve_anchor(bars: Sequence[Bar], anchor_index: int | None) -> int:
    """The anchor bar index: an explicit `anchor_index` (validated in range), or the
    auto-anchor — the start (earlier pivot) of the dominant recent swing, falling
    back to the first bar when there is no dominant swing."""

    if anchor_index is not None:
        if not 0 <= anchor_index < len(bars):
            raise ValueError(f"anchor_index {anchor_index} out of range for {len(bars)} bars")
        return anchor_index
    swing = dominant_swing(bars)
    if swing is None:
        return 0
    high_anchor, low_anchor = swing
    start = min(high_anchor, low_anchor, key=lambda a: a.ts)  # the swing's earlier pivot
    return next(i for i, b in enumerate(bars) if b.event_ts == start.ts)


def _anchored_vwap(bars: Sequence[Bar], anchor_index: int | None) -> AnchoredVwapValue:
    """The synchronous core: resolve the anchor and compose the latest value."""

    return anchored_vwap_value(bars, _resolve_anchor(bars, anchor_index))


async def _anchored_vwap_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    anchor_index: int | None,
    as_of: datetime | None,
) -> AnchoredVwapResponse:
    """Body of the `anchored_vwap` tool. Validates at the boundary, reads bars
    through the provider, and computes the anchored VWAP off the fetched bars."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return AnchoredVwapResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(_anchored_vwap, list(bars), anchor_index)
    return AnchoredVwapResponse(result=result, partial_reason=None, scanned_at=now)


def register_anchored_vwap(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `anchored_vwap` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="anchored_vwap", description=ANCHORED_VWAP_DESCRIPTION)
    async def anchored_vwap_tool(
        symbol: str,
        timeframe: str,
        anchor_index: int | None = None,
        as_of: datetime | None = None,
    ) -> AnchoredVwapResponse:
        return await _anchored_vwap_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            anchor_index=anchor_index,
            as_of=as_of,
        )


__all__ = [
    "ANCHORED_VWAP_DESCRIPTION",
    "AnchoredVwapResponse",
    "_anchored_vwap_response",
    "register_anchored_vwap",
]
