"""`squeeze_scan` MCP tool (Plan 0100 phase 1, ADR-0095 / ADR-0083).

Ranks a supplied symbol list (watchlist) by squeeze tightness on cached bars: for
each symbol it reads the ADR-0083 squeeze trio (`bb_width`, `bb_width_pct90`,
`squeeze_on`) from the trailing condition snapshot and returns the symbols sorted
by `bb_width_pct90` ascending — the most-coiled names first. Symbols with too short
a history for the percentile (or no cached bars / a fetch error) are skipped and
reported in `skipped`; they never fail the whole scan.

The fan-out, cap, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095); this module
supplies only the squeeze scorer + sort key and wraps the result. The body is
factored as `_squeeze_scan_response` so the scan / skip paths are unit-testable on
a single event loop (no live MCP server needed).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.scanners import (
    MAX_SCAN_SYMBOLS,
    SqueezeScanMatch,
    _scan_symbols,
    _ScanSkip,
    score_squeeze,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

SQUEEZE_SCAN_DESCRIPTION = (
    "Rank a supplied symbol list (watchlist) by squeeze tightness on cached bars. "
    "For each symbol the TTM squeeze trio is read from its trailing condition "
    "snapshot (ADR-0083): bb_width (Bollinger band-width, the compression metric), "
    "bb_width_pct90 (its trailing 90-window percentile — lower = tighter coil), and "
    "squeeze_on (Bollinger inside Keltner on the latest bar). Returns {matches, "
    "skipped, scanned_at}: matches are the whole watchlist ranked by bb_width_pct90 "
    "ascending (most-coiled first), each carrying its trio, ties broken by symbol; "
    "skipped lists symbols with too short a history for the percentile or no cached "
    f"bars (backfill via get_ohlcv first). Max {MAX_SCAN_SYMBOLS} symbols. Pass "
    "`as_of` for historical replay (trailing — no future leak). Conditions only — "
    f"never buy/sell advice. Supported timeframes: {supported_timeframes_label()}."
)


class SqueezeScanResponse(BaseModel):
    """`squeeze_scan` result. `matches` are the scanned symbols ranked by
    `bb_width_pct90` ascending (tightest coil first), tie-broken by symbol;
    `skipped` lists symbols with too short a history for the trio or no cached
    bars / a fetch error; `scanned_at` is the wall-clock run time (provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[SqueezeScanMatch]
    skipped: list[str]
    scanned_at: datetime


async def _squeeze_scan_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    as_of: datetime | None,
) -> SqueezeScanResponse:
    """Body of the `squeeze_scan` tool. Delegates the fan-out to `_scan_symbols`
    (ADR-0095), scoring each symbol with the squeeze trio and ranking by
    `bb_width_pct90` ascending (tightest first, ties by symbol)."""

    def _score(bars: Sequence[Bar]) -> SqueezeScanMatch | _ScanSkip:
        return score_squeeze(bars, timeframe)

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=_score,
        sort_key=lambda m: (m.bb_width_pct90, m.symbol),
        as_of=as_of,
        tool_name="squeeze_scan",
    )
    return SqueezeScanResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


def register_squeeze_scan(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `squeeze_scan` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="squeeze_scan", description=SQUEEZE_SCAN_DESCRIPTION)
    async def squeeze_scan_tool(
        symbols: list[str],
        timeframe: str,
        as_of: datetime | None = None,
    ) -> SqueezeScanResponse:
        return await _squeeze_scan_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
        )


__all__ = [
    "SQUEEZE_SCAN_DESCRIPTION",
    "SqueezeScanResponse",
    "_squeeze_scan_response",
    "register_squeeze_scan",
]
