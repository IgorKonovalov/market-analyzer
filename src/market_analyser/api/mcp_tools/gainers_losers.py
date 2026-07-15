"""`gainers_losers` MCP tool (Plan 0100 phase 2, ADR-0095).

Ranks a supplied symbol list (watchlist) by trailing close-to-close % change over
one timeframe window, split by direction: the biggest gainer first, the biggest
loser last. Each match carries its signed `change_pct` and coarse `direction`.
Symbols with a single bar (no prior close) or a zero prior close are skipped and
reported in `skipped`; they never fail the whole scan or divide by zero.

The fan-out, cap, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095); this module
supplies only the return scorer + sort key and wraps the result. The body is
factored as `_gainers_losers_response` so the scan / skip paths are unit-testable
on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.scanners import (
    MAX_SCAN_SYMBOLS,
    GainersLosersMatch,
    _scan_symbols,
    score_return,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label

GAINERS_LOSERS_DESCRIPTION = (
    "Rank a supplied symbol list (watchlist) by trailing close-to-close % change "
    "over one timeframe window on cached bars — the biggest gainer first, the "
    "biggest loser last. Returns {matches, skipped, scanned_at}: each match carries "
    "its signed change_pct (latest close vs the prior close, in percent) and a "
    "coarse direction (up when non-negative, down when negative), sorted by "
    "change_pct descending, ties broken by symbol; skipped lists symbols with a "
    "single bar (no prior close) or no cached bars (backfill via get_ohlcv first). "
    f"Max {MAX_SCAN_SYMBOLS} symbols. Pass `as_of` for historical replay (trailing "
    "— no future leak). Conditions only — a raw move is a fact, never buy/sell "
    f"advice. Supported timeframes: {supported_timeframes_label()}."
)


class GainersLosersResponse(BaseModel):
    """`gainers_losers` result. `matches` are the scanned symbols sorted by
    `change_pct` descending (biggest gainer first, biggest loser last), tie-broken
    by symbol; `skipped` lists symbols with no prior close or no cached bars / a
    fetch error; `scanned_at` is the wall-clock run time (provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[GainersLosersMatch]
    skipped: list[str]
    scanned_at: datetime


async def _gainers_losers_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    as_of: datetime | None,
) -> GainersLosersResponse:
    """Body of the `gainers_losers` tool. Delegates the fan-out to `_scan_symbols`
    (ADR-0095), scoring each symbol's close-to-close move and ranking by
    `change_pct` descending (biggest gainer first, ties by symbol)."""

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=score_return,
        sort_key=lambda m: (-m.change_pct, m.symbol),
        as_of=as_of,
        tool_name="gainers_losers",
    )
    return GainersLosersResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


def register_gainers_losers(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `gainers_losers` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="gainers_losers", description=GAINERS_LOSERS_DESCRIPTION)
    async def gainers_losers_tool(
        symbols: list[str],
        timeframe: str,
        as_of: datetime | None = None,
    ) -> GainersLosersResponse:
        return await _gainers_losers_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
        )


__all__ = [
    "GAINERS_LOSERS_DESCRIPTION",
    "GainersLosersResponse",
    "_gainers_losers_response",
    "register_gainers_losers",
]
