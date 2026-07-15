"""`smart_volume` MCP tool (Plan 0021 phase 3, ADR-0023; refactored onto the shared
scan harness in Plan 0100 phase 4, ADR-0095).

Applies the phase-2 `analysis.volume.smart_volume` condition across a supplied
(capped) symbol list, reading cached bars per symbol through the
`MarketDataProvider` Protocol. Returns the symbols whose latest bar shows a volume
surge (volume at least `vol_multiple` times its trailing average) with RSI inside
`[rsi_low, rsi_high]`,
sorted deterministically (multiple descending, then symbol). Symbols with no
cached bars (or a fetch error) are skipped, logged, and reported in `skipped`.

The cap, per-symbol read, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095) — this tool
validates its band order at the boundary and supplies only the qualifying scorer (a
non-qualifying symbol maps to ``None``, so it is dropped, not skipped) and the sort
key. The body is factored as `_smart_volume_scan_response` so the scan / skip paths
are unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.scanners import MAX_SCAN_SYMBOLS, _scan_symbols
from market_analyser.analysis.types import SmartVolumeHit
from market_analyser.analysis.volume import (
    SMART_RSI_HIGH,
    SMART_RSI_LOW,
    SMART_VOL_MULTIPLE,
    smart_volume,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

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
    """Body of the `smart_volume` tool. Validates the band order at the boundary,
    then delegates the fan-out to `_scan_symbols` (ADR-0095), scoring each symbol
    with the qualifying condition — a non-qualifying symbol maps to ``None``
    (dropped) — and ranking by multiple descending, then symbol. Qualifying hits
    have a non-None multiple (qualifies implies the ratio was defined), so the sort
    key's ``or 0.0`` never fires for a real match."""

    if rsi_low > rsi_high:
        raise ValueError(f"rsi_low {rsi_low} must be <= rsi_high {rsi_high}")

    def _score(bars: Sequence[Bar]) -> SmartVolumeHit | None:
        result = smart_volume(bars, rsi_low, rsi_high, vol_multiple)
        return result if result.qualifies else None

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=_score,
        sort_key=lambda r: (-(r.volume_multiple or 0.0), r.symbol),
        as_of=as_of,
        tool_name="smart_volume",
    )
    return SmartVolumeScanResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


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
