"""`momentum_scan` MCP tool (Plan 0100 phase 3, ADR-0095 / ADR-0023).

Filters/ranks a supplied symbol list (watchlist) by an RSI band and an optional
requested trend, read from each symbol's trailing condition snapshot on cached
bars. NO volume gate — this is the deliberate, un-volume-gated complement to
`smart_volume` (which requires a volume surge). Matches are sorted by RSI
descending (strongest momentum first). Symbols with too short a history for RSI (or
no cached bars / a fetch error) are skipped; a symbol scanned but out of band or of
the wrong trend is simply dropped (not a match, not skipped).

The fan-out, cap, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095); this module
supplies only the band/trend validation, the momentum scorer, and the sort key. The
body is factored as `_momentum_scan_response` so the scan / skip / filter paths are
unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.scanners import (
    MAX_SCAN_SYMBOLS,
    MomentumScanMatch,
    _scan_symbols,
    _ScanSkip,
    score_momentum,
)
from market_analyser.analysis.types import Trend
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

_TREND_VALUES = tuple(t.value for t in Trend)

MOMENTUM_SCAN_DESCRIPTION = (
    "Filter/rank a supplied symbol list (watchlist) by an RSI band and an optional "
    "trend on cached bars — NO volume gate (the un-volume-gated complement to "
    "smart_volume, which requires a volume surge). For each symbol the latest RSI, "
    "trend, and momentum stance are read from its trailing condition snapshot. "
    "Returns {matches, skipped, scanned_at}: matches are only the symbols whose RSI "
    "is within [rsi_min, rsi_max] (boundary-inclusive) and, when `trend` is given "
    f"(one of {', '.join(_TREND_VALUES)}), whose trend matches — each carrying its "
    "rsi, trend, and momentum, sorted by rsi descending then symbol; skipped lists "
    "symbols with too short a history for RSI or no cached bars (backfill via "
    f"get_ohlcv first). Max {MAX_SCAN_SYMBOLS} symbols. Pass `as_of` for historical "
    "replay (trailing — no future leak). Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class MomentumScanResponse(BaseModel):
    """`momentum_scan` result. `matches` are the in-band, trend-matching symbols
    sorted by `rsi` descending (strongest momentum first), tie-broken by symbol;
    `skipped` lists symbols with too short a history for RSI or no cached bars / a
    fetch error; `scanned_at` is the wall-clock run time (provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[MomentumScanMatch]
    skipped: list[str]
    scanned_at: datetime


async def _momentum_scan_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    rsi_min: float = 0.0,
    rsi_max: float = 100.0,
    trend: str | None = None,
    as_of: datetime | None,
) -> MomentumScanResponse:
    """Body of the `momentum_scan` tool. Validates the band + trend at the boundary,
    then delegates the fan-out to `_scan_symbols` (ADR-0095), scoring each symbol's
    momentum and ranking by RSI descending (ties by symbol)."""

    if rsi_min > rsi_max:
        raise ValueError(f"rsi_min {rsi_min} must be <= rsi_max {rsi_max}")
    if trend is not None and trend not in _TREND_VALUES:
        raise ValueError(f"trend {trend!r} not supported (one of {list(_TREND_VALUES)})")

    def _score(bars: Sequence[Bar]) -> MomentumScanMatch | _ScanSkip | None:
        return score_momentum(bars, timeframe, rsi_min=rsi_min, rsi_max=rsi_max, trend=trend)

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=_score,
        sort_key=lambda m: (-m.rsi, m.symbol),
        as_of=as_of,
        tool_name="momentum_scan",
    )
    return MomentumScanResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


def register_momentum_scan(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `momentum_scan` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="momentum_scan", description=MOMENTUM_SCAN_DESCRIPTION)
    async def momentum_scan_tool(
        symbols: list[str],
        timeframe: str,
        rsi_min: float = 0.0,
        rsi_max: float = 100.0,
        trend: str | None = None,
        as_of: datetime | None = None,
    ) -> MomentumScanResponse:
        return await _momentum_scan_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
            trend=trend,
            as_of=as_of,
        )


__all__ = [
    "MOMENTUM_SCAN_DESCRIPTION",
    "MomentumScanResponse",
    "_momentum_scan_response",
    "register_momentum_scan",
]
