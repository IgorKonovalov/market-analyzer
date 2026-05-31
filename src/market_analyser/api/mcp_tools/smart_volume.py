"""`smart_volume` MCP tool (Plan 0021 phase 3, ADR-0023).

Applies the phase-2 `analysis.volume.smart_volume` condition across a supplied
(capped) symbol list, reading cached bars per symbol through the
`MarketDataProvider` Protocol. Returns the symbols whose latest bar shows a volume
surge (volume at least `vol_multiple` times its trailing average) with RSI inside
`[rsi_low, rsi_high]`,
sorted deterministically (multiple descending, then symbol). Symbols with no
cached bars (or a fetch error) are skipped, logged, and reported in `skipped`.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider,
which truncates to `event_ts <= as_of` (anti-lookahead replay). The tool validates
at the MCP boundary (list cap, supported timeframe, band order) and dispatches only
through the provider (ADR-0007); each per-symbol fetch is offloaded with
`asyncio.to_thread`.

The body is factored as `_smart_volume_scan_response` so the scan / skip paths are
unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.types import SmartVolumeHit
from market_analyser.analysis.volume import (
    SMART_RSI_HIGH,
    SMART_RSI_LOW,
    SMART_VOL_MULTIPLE,
    smart_volume,
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
# default for the unbounded cadences — wide enough for the RSI + relative-volume
# windows.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

SMART_VOLUME_DESCRIPTION = (
    "Scan a supplied symbol list (watchlist) for a volume surge with RSI in a "
    "band on cached bars. A symbol qualifies when its latest bar's volume is at "
    "least `vol_multiple` times its trailing average AND the latest RSI sits "
    "inside [rsi_low, rsi_high]. Returns {matches, skipped, scanned_at}: matches "
    "are the qualifying symbols only, each with volume_multiple and rsi, sorted "
    "by multiple descending then symbol; skipped lists symbols with no cached "
    f"bars (backfill via get_ohlcv first). Max {MAX_SCAN_SYMBOLS} symbols. Pass "
    "`as_of` for historical replay (trailing — no future leak). Conditions only — "
    f"never buy/sell advice. Supported timeframes: {supported_timeframes_label()}."
)


class SmartVolumeScanResponse(BaseModel):
    """`smart_volume` scan result. `matches` are the qualifying symbols only
    (sorted multiple-desc, then symbol); `skipped` lists symbols with no cached
    bars or a fetch error; `scanned_at` is the wall-clock run time (provenance)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[SmartVolumeHit]
    skipped: list[str]
    scanned_at: datetime


def _require_scan_list(symbols: list[str]) -> None:
    if not symbols:
        raise ValueError("symbols must be a non-empty list")
    if len(symbols) > MAX_SCAN_SYMBOLS:
        raise ValueError(f"symbols list of {len(symbols)} exceeds the cap of {MAX_SCAN_SYMBOLS}")
    for symbol in symbols:
        _require_non_empty_symbol(symbol)


async def _smart_volume_scan_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    rsi_low: float,
    rsi_high: float,
    vol_multiple: float,
    as_of: datetime | None,
) -> SmartVolumeScanResponse:
    """Body of the `smart_volume` tool. Validates at the boundary, reads bars per
    symbol through the provider (failed/empty fetches skipped), computes the
    condition, and returns the qualifying matches sorted deterministically."""

    _require_scan_list(symbols)
    _require_supported_timeframe(timeframe)
    if rsi_low > rsi_high:
        raise ValueError(f"rsi_low {rsi_low} must be <= rsi_high {rsi_high}")

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)

    matches: list[SmartVolumeHit] = []
    skipped: list[str] = []
    for symbol in symbols:
        try:
            bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
        except Exception:  # one bad symbol must not fail the whole scan
            logger.warning("smart_volume: fetch failed for %s %s; skipping", symbol, timeframe)
            skipped.append(symbol)
            continue
        if not bars:
            skipped.append(symbol)
            continue
        result = smart_volume(list(bars), rsi_low, rsi_high, vol_multiple)
        if result.qualifies:
            matches.append(result)

    # Deterministic order: strongest surge first, ties broken by symbol. Qualifying
    # hits have a non-None multiple (qualifies implies the ratio was defined).
    matches.sort(key=lambda r: (-(r.volume_multiple or 0.0), r.symbol))
    return SmartVolumeScanResponse(
        matches=matches, skipped=skipped, scanned_at=datetime.now(tz=UTC)
    )


def register_smart_volume(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `smart_volume` tool to `server`. The provider is captured by
    closure so the tool body keeps the parameters FastMCP introspects."""

    # Explicit `name=` so the MCP tool is `smart_volume` regardless of the
    # closure's Python function name (suffixed to avoid shadowing the import).
    @server.tool(name="smart_volume", description=SMART_VOLUME_DESCRIPTION)
    async def smart_volume_tool(
        symbols: list[str],
        timeframe: str,
        rsi_low: float = SMART_RSI_LOW,
        rsi_high: float = SMART_RSI_HIGH,
        vol_multiple: float = SMART_VOL_MULTIPLE,
        as_of: datetime | None = None,
    ) -> SmartVolumeScanResponse:
        return await _smart_volume_scan_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            rsi_low=rsi_low,
            rsi_high=rsi_high,
            vol_multiple=vol_multiple,
            as_of=as_of,
        )


__all__ = [
    "MAX_SCAN_SYMBOLS",
    "SMART_VOLUME_DESCRIPTION",
    "SmartVolumeScanResponse",
    "_smart_volume_scan_response",
    "register_smart_volume",
]
