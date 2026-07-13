"""`detect_divergences` MCP tool (Plan 0091 phase 5, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and runs `analysis.divergence.detect_divergences` for one oscillator,
returning the regular / hidden bullish / bearish divergences between price and that
oscillator. `result` is `None` with `partial_reason="no_bars"` when nothing is
cached (an honest miss — never a silent fetch); an empty list means the scan ran
and found nothing (a valid result, distinct from no-data).

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the synchronous detection is offloaded with `asyncio.to_thread`.

The body is factored as `_detect_divergences_response` so the fetch / empty-cache /
`as_of`-replay paths are unit-testable on a single event loop (no live MCP server).
Conditions only — a divergence is chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.divergence import DIVERGENCE_LOOKBACK, Oscillator, detect_divergences
from market_analyser.analysis.types import Divergence
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label

# Fetch window: the timeframe's feed-limited history, or a generous default for the
# unbounded cadences — wide enough for the oscillator warmup plus the divergence
# lookback and pivot confirmation.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

DETECT_DIVERGENCES_DESCRIPTION = (
    "Detect price↔oscillator divergences on one symbol's cached bars for the chosen "
    "oscillator (rsi, macd_hist, obv, or mfi). Returns {result, partial_reason, "
    "scanned_at}: result is the list of divergences — each with its kind "
    "(regular/hidden bullish/bearish), the two price anchors, the two matched "
    "oscillator anchors, the confirming bar_index, and a 0..1 strength — pairing the "
    "two most recent confirmed price swing pivots of a kind against the oscillator's "
    "own pivots. Regular bearish = higher price high + lower oscillator high (a rally "
    "losing momentum); regular bullish = lower low + higher oscillator low; hidden "
    "divergences flag trend continuation. An empty list means the scan ran and found "
    "nothing; result is null with partial_reason='no_bars' when nothing is cached "
    "(backfill via get_ohlcv first). Strictly trailing: a divergence at bar i reads "
    "only bars up to i. Pass `as_of` for historical replay (no future leak). "
    "Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class DivergencesResponse(BaseModel):
    """`detect_divergences` result. `result` is the list of detected divergences
    (empty = scanned, none found), or `None` with `partial_reason="no_bars"` when
    the cache holds nothing for the symbol. `scanned_at` is the wall-clock run time
    (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: list[Divergence] | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


async def _detect_divergences_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    oscillator: Oscillator,
    lookback: int,
    as_of: datetime | None,
) -> DivergencesResponse:
    """Body of the `detect_divergences` tool. Validates at the boundary, reads bars
    through the provider, and runs the trailing divergence detector off the fetched
    bars."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return DivergencesResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = await asyncio.to_thread(detect_divergences, list(bars), oscillator, lookback)
    return DivergencesResponse(result=result, partial_reason=None, scanned_at=now)


def register_detect_divergences(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `detect_divergences` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    # Explicit `name=` so the MCP tool is `detect_divergences` regardless of the
    # closure's Python function name (suffixed to avoid shadowing the import).
    @server.tool(name="detect_divergences", description=DETECT_DIVERGENCES_DESCRIPTION)
    async def detect_divergences_tool(
        symbol: str,
        timeframe: str,
        oscillator: Oscillator = "rsi",
        lookback: int = DIVERGENCE_LOOKBACK,
        as_of: datetime | None = None,
    ) -> DivergencesResponse:
        return await _detect_divergences_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            oscillator=oscillator,
            lookback=lookback,
            as_of=as_of,
        )


__all__ = [
    "DETECT_DIVERGENCES_DESCRIPTION",
    "DivergencesResponse",
    "_detect_divergences_response",
    "register_detect_divergences",
]
