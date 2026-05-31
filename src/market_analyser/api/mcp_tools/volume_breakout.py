"""`volume_breakout` MCP tool (Plan 0021 phase 3, ADR-0023).

Applies the phase-2 `analysis.volume.volume_breakout` condition across a supplied
(capped) symbol list, reading cached bars per symbol through the
`MarketDataProvider` Protocol. Returns the symbols that broke out — each with its
relative-volume multiple, direction, and the price level it cleared — sorted
deterministically (multiple descending, then symbol). Symbols with no cached bars
(or a fetch error) are skipped, logged, and reported in `skipped`; they never fail
the whole scan.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of`, so a scan inherits the layer's anti-
lookahead guarantee. The tool validates at the MCP boundary (list cap, supported
timeframe) and dispatches only through the provider (ADR-0007); each per-symbol
fetch is offloaded with `asyncio.to_thread` so a slow read cannot stall the loop.

The body is factored as `_volume_breakout_scan_response` so the scan / skip paths
are unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.types import VolumeBreakout
from market_analyser.analysis.volume import (
    BREAKOUT_PRICE_LOOKBACK,
    BREAKOUT_VOL_MULTIPLE,
    volume_breakout,
)
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label

logger = logging.getLogger(__name__)

# Bar fan-out bound: one cached read per symbol, so cap the list (Plan 0021 risk
# mitigation). Kept self-contained per scanner tool rather than shared.
MAX_SCAN_SYMBOLS = 25

# Fetch window per symbol: the timeframe's feed-limited history, or a generous
# default for the unbounded cadences — wide enough for the breakout's trailing
# range + relative-volume windows.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

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


def _require_scan_list(symbols: list[str]) -> None:
    if not symbols:
        raise ValueError("symbols must be a non-empty list")
    if len(symbols) > MAX_SCAN_SYMBOLS:
        raise ValueError(f"symbols list of {len(symbols)} exceeds the cap of {MAX_SCAN_SYMBOLS}")
    for symbol in symbols:
        _require_non_empty_symbol(symbol)


async def _volume_breakout_scan_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    vol_multiple: float,
    price_lookback: int,
    as_of: datetime | None,
) -> VolumeBreakoutScanResponse:
    """Body of the `volume_breakout` tool. Validates at the boundary, reads bars
    per symbol through the provider (failed/empty fetches skipped), computes the
    breakout condition, and returns the matches sorted deterministically."""

    _require_scan_list(symbols)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)

    matches: list[VolumeBreakout] = []
    skipped: list[str] = []
    for symbol in symbols:
        try:
            bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
        except Exception:  # one bad symbol must not fail the whole scan
            logger.warning("volume_breakout: fetch failed for %s %s; skipping", symbol, timeframe)
            skipped.append(symbol)
            continue
        if not bars:
            skipped.append(symbol)
            continue
        result = volume_breakout(list(bars), vol_multiple, price_lookback)
        if result.is_breakout:
            matches.append(result)

    # Deterministic order: strongest surge first, ties broken by symbol. All
    # matches have a non-None multiple (is_breakout implies the ratio was defined).
    matches.sort(key=lambda r: (-(r.volume_multiple or 0.0), r.symbol))
    return VolumeBreakoutScanResponse(
        matches=matches, skipped=skipped, scanned_at=datetime.now(tz=UTC)
    )


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
