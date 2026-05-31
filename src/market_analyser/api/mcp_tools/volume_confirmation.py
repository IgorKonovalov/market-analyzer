"""`volume_confirmation` MCP tool (Plan 0021 phase 3, ADR-0023).

A single-symbol detail tool: reads cached bars through the `MarketDataProvider`
Protocol and runs the phase-2 `analysis.volume.volume_confirmation`, returning the
0..1 score of how well volume backs the recent price move plus the supporting
figures (net direction, supportive vs opposing volume). `result` is `None` with
`partial_reason="no_bars"` when nothing is cached for the symbol.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay for free). The tool
validates at the MCP boundary and dispatches only through the provider (ADR-0007);
the fetch is offloaded with `asyncio.to_thread`.

The body is factored as `_volume_confirmation_response` so the fetch / empty-cache
paths are unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.types import VolumeConfirmation
from market_analyser.analysis.volume import CONFIRMATION_LOOKBACK, volume_confirmation
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label

# Fetch window: the timeframe's feed-limited history, or a generous default for
# the unbounded cadences — wide enough for the confirmation lookback window.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

VOLUME_CONFIRMATION_DESCRIPTION = (
    "Report how well volume backs one symbol's recent price move on cached bars. "
    "Returns {result, partial_reason, scanned_at}: result.score is a 0..1 share "
    "of directional volume aligned with the net move over the trailing `lookback` "
    "bars (high when the move is carried by trend volume, low on a counter-trend "
    "divergence), with result.confirmed, direction, and the supportive/opposing "
    "volume figures. result is null with partial_reason='no_bars' when nothing is "
    "cached (backfill via get_ohlcv first). Pass `as_of` for historical replay "
    "(trailing — no future leak). Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class VolumeConfirmationResponse(BaseModel):
    """`volume_confirmation` result. `result` is the confirmation read, or `None`
    with `partial_reason="no_bars"` when the cache holds nothing for the symbol.
    `scanned_at` is the wall-clock run time (run provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: VolumeConfirmation | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


async def _volume_confirmation_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    lookback: int,
    as_of: datetime | None,
) -> VolumeConfirmationResponse:
    """Body of the `volume_confirmation` tool. Validates at the boundary, reads
    bars through the provider, and computes the confirmation off the fetched
    bars."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return VolumeConfirmationResponse(result=None, partial_reason="no_bars", scanned_at=now)
    result = volume_confirmation(list(bars), lookback)
    return VolumeConfirmationResponse(result=result, partial_reason=None, scanned_at=now)


def register_volume_confirmation(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `volume_confirmation` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    # Explicit `name=` so the MCP tool is `volume_confirmation` regardless of the
    # closure's Python function name (suffixed to avoid shadowing the import).
    @server.tool(name="volume_confirmation", description=VOLUME_CONFIRMATION_DESCRIPTION)
    async def volume_confirmation_tool(
        symbol: str,
        timeframe: str,
        lookback: int = CONFIRMATION_LOOKBACK,
        as_of: datetime | None = None,
    ) -> VolumeConfirmationResponse:
        return await _volume_confirmation_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            as_of=as_of,
        )


__all__ = [
    "VOLUME_CONFIRMATION_DESCRIPTION",
    "VolumeConfirmationResponse",
    "_volume_confirmation_response",
    "register_volume_confirmation",
]
