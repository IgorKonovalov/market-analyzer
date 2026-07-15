"""`volume_breakout` MCP tool (Plan 0021 phase 3, ADR-0023; refactored onto the
shared scan harness in Plan 0100 phase 4, ADR-0095).

Applies the phase-2 `analysis.volume.volume_breakout` condition across a supplied
(capped) symbol list, reading cached bars per symbol through the
`MarketDataProvider` Protocol. Returns the symbols that broke out — each with its
relative-volume multiple, direction, and the price level it cleared — sorted
deterministically (multiple descending, then symbol). Symbols with no cached bars
(or a fetch error) are skipped, logged, and reported in `skipped`; they never fail
the whole scan.

The cap, per-symbol read, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095) — this tool
supplies only the breakout scorer (a non-breakout maps to ``None``, so it is
dropped, not skipped) and the sort key. The body is factored as
`_volume_breakout_scan_response` so the scan / skip paths are unit-testable on a
single event loop (no live MCP server needed).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.scanners import MAX_SCAN_SYMBOLS, _scan_symbols
from market_analyser.analysis.types import VolumeBreakout
from market_analyser.analysis.volume import (
    BREAKOUT_PRICE_LOOKBACK,
    BREAKOUT_VOL_MULTIPLE,
    volume_breakout,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

VOLUME_BREAKOUT_DESCRIPTION = (
    "Scan a supplied symbol list (watchlist) for price+volume breakouts on cached "
    "bars. A symbol breaks out when its latest bar's volume is at least "
    "`vol_multiple` times its trailing average AND the close clears its trailing "
    "`price_lookback`-bar high (bullish) or low (bearish). Returns {matches, "
    "skipped, scanned_at}: matches are the breakouts only, each with direction, "
    "volume_multiple, and the broken price level, sorted by multiple descending "
    f"then symbol; skipped lists symbols with no cached bars (backfill via "
    f"get_ohlcv first). Max {MAX_SCAN_SYMBOLS} symbols. Pass `as_of` for "
    "historical replay (trailing — no future leak). Conditions only — never "
    f"buy/sell advice. Supported timeframes: {supported_timeframes_label()}."
)


class VolumeBreakoutScanResponse(BaseModel):
    """`volume_breakout` scan result. `matches` are the breakout symbols only
    (sorted multiple-desc, then symbol); `skipped` lists symbols with no cached
    bars or a fetch error; `scanned_at` is the wall-clock run time (provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[VolumeBreakout]
    skipped: list[str]
    scanned_at: datetime


async def _volume_breakout_scan_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    vol_multiple: float,
    price_lookback: int,
    as_of: datetime | None,
) -> VolumeBreakoutScanResponse:
    """Body of the `volume_breakout` tool. Delegates the fan-out to `_scan_symbols`
    (ADR-0095), scoring each symbol with the breakout condition — a non-breakout
    maps to ``None`` (dropped) — and ranking by multiple descending, then symbol.
    All matches have a non-None multiple (is_breakout implies the ratio was
    defined), so the sort key's ``or 0.0`` never fires for a real match."""

    def _score(bars: Sequence[Bar]) -> VolumeBreakout | None:
        result = volume_breakout(bars, vol_multiple, price_lookback)
        return result if result.is_breakout else None

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=_score,
        sort_key=lambda r: (-(r.volume_multiple or 0.0), r.symbol),
        as_of=as_of,
        tool_name="volume_breakout",
    )
    return VolumeBreakoutScanResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


def register_volume_breakout(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `volume_breakout` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    # Explicit `name=` so the MCP tool is `volume_breakout` regardless of the
    # closure's Python function name (which is suffixed to avoid shadowing the
    # imported analysis function).
    @server.tool(name="volume_breakout", description=VOLUME_BREAKOUT_DESCRIPTION)
    async def volume_breakout_tool(
        symbols: list[str],
        timeframe: str,
        vol_multiple: float = BREAKOUT_VOL_MULTIPLE,
        price_lookback: int = BREAKOUT_PRICE_LOOKBACK,
        as_of: datetime | None = None,
    ) -> VolumeBreakoutScanResponse:
        return await _volume_breakout_scan_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            vol_multiple=vol_multiple,
            price_lookback=price_lookback,
            as_of=as_of,
        )


__all__ = [
    "MAX_SCAN_SYMBOLS",
    "VOLUME_BREAKOUT_DESCRIPTION",
    "VolumeBreakoutScanResponse",
    "_volume_breakout_scan_response",
    "register_volume_breakout",
]
