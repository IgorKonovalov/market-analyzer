"""`detect_levels` MCP tool (Plan 0051 phase 3, ADR-0023, ADR-0017).

Computes clustered, volume-weighted support/resistance levels over cached bars
AND auto-draws them in one call: publishes a single `chart.show v1` event
carrying one `price_line` overlay per level (`role` support/resistance, ranked
labels `S1`/`R1`/...). `chart.show` (not `chart.update`) so the one call also
mounts the symbol/timeframe and window when no chart is up yet — the plan's
"compute the levels and draw them" promise needs no prior viewer state.

Levels are *derived* data: nothing is persisted; reopening the viewer re-runs
the detection. The math is the pure `analysis.levels.support_resistance_levels`
(trailing, deterministic — ADR-0023). The body is factored out as
`_detect_levels_response` so the fetch + empty-cache paths are unit-testable on
a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.analysis.levels import DEFAULT_MAX_LEVELS, support_resistance_levels
from market_analyser.analysis.types import Level
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.events import ChartShowPayloadV1, EventBus, OverlaySpec

DETECT_LEVELS_DESCRIPTION = (
    "Detect support/resistance levels on the cached bars and draw them on the "
    "chart in one call: clusters confirmed swing pivots into zones, ranks each "
    "zone's strength by touch count weighted by the volume traded at that price "
    "(volume-by-price), returns the ranked levels as data, AND publishes a "
    "single `chart.show v1` event carrying one `price_line` overlay per level "
    "(role support/resistance, labels S1/R1/... in strength order). Reads "
    "cached bars only (backfill via get_ohlcv first); an empty/uncached range "
    "publishes nothing and returns count=0. `max_levels` caps how many levels "
    "per role survive (strongest first). Results are derived and NOT persisted "
    "— reopening the viewer re-runs the detection. Conditions only — levels are "
    "chart geometry, never buy/sell advice. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


def _level_overlays(levels: list[Level]) -> list[OverlaySpec]:
    """One `price_line` overlay per level, in the levels' (strength-descending)
    order, labelled `S1`/`S2`/... and `R1`/`R2`/... by per-role rank."""

    counters = {"support": 0, "resistance": 0}
    overlays: list[OverlaySpec] = []
    for level in levels:
        counters[level.role] += 1
        prefix = "S" if level.role == "support" else "R"
        overlays.append(
            OverlaySpec(
                kind="price_line",
                price=level.price,
                label=f"{prefix}{counters[level.role]}",
                role=level.role,
            )
        )
    return overlays


async def _detect_levels_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    max_levels: int,
) -> dict[str, Any]:
    """Body of the `detect_levels` tool: validate at the boundary, read cached
    bars through the provider, compute the ranked levels off-thread, and publish
    one `chart.show` event when there is anything to draw."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)
    if max_levels < 1:
        raise ValueError(f"max_levels must be >= 1, got {max_levels}")

    bars = await asyncio.to_thread(
        provider.get_ohlcv, symbol, timeframe, range_start, range_end, None
    )
    levels = await asyncio.to_thread(support_resistance_levels, list(bars), max_levels=max_levels)
    if not levels:
        return {
            "levels": [],
            "event_published": False,
            "type": "chart.show",
            "version": ChartShowPayloadV1.VERSION,
            "count": 0,
        }
    payload = ChartShowPayloadV1(
        symbol=symbol,
        timeframe=timeframe,
        range_start=range_start,
        range_end=range_end,
        overlays=_level_overlays(levels),
    )
    event_bus.publish("chart.show", payload)
    return {
        "levels": [level.model_dump(mode="json") for level in levels],
        "event_published": True,
        "type": "chart.show",
        "version": ChartShowPayloadV1.VERSION,
        "count": len(levels),
    }


def register_detect_levels(
    server: FastMCP, *, provider: MarketDataProvider, event_bus: EventBus
) -> None:
    """Bind the `detect_levels` tool to `server`. The provider and event bus are
    captured by closure so the tool body keeps the parameters FastMCP introspects
    to build the input schema."""

    @server.tool(description=DETECT_LEVELS_DESCRIPTION)
    async def detect_levels(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        max_levels: int = DEFAULT_MAX_LEVELS,
    ) -> dict[str, Any]:
        return await _detect_levels_response(
            provider=provider,
            event_bus=event_bus,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            max_levels=max_levels,
        )


__all__ = [
    "DETECT_LEVELS_DESCRIPTION",
    "_detect_levels_response",
    "register_detect_levels",
]
