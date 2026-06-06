"""`scan_patterns` MCP tool (Plan 0049, ADR-0045).

Sweeps a range for EVERY candlestick pattern and publishes them all on one
`chart.highlight v1` event — the bulk path the agent previously lacked (it would
otherwise have to call `highlight_pattern` once per pattern). Unlike
`highlight_pattern`, a sweep is *derived* data: it writes NO annotation row, so
nothing can go stale against the detectors; reopening Electron re-runs the sweep
(ADR-0045).

The detect→filter→map transform is the shared pure core in
`analysis.markers.markers_for_range`, reused verbatim by the `POST /scan_patterns`
route (phase 4) so the agent and UI triggers emit identical markers. The body is
factored out as `_scan_patterns_response` so the fetch + empty-range paths are
unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.analysis.markers import markers_for_range
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.events import ChartHighlightPayloadV1, EventBus

SCAN_PATTERNS_DESCRIPTION = (
    "Sweep a time range for EVERY candlestick pattern on the cached bars and "
    "highlight them all at once: publishes a single `chart.highlight v1` event "
    "carrying one marker per detected pattern (multi-bar patterns carry a bar "
    "span; doji/neutral patterns are included). Use this instead of calling "
    "`highlight_pattern` once per pattern. Reads cached bars only (backfill via "
    "get_ohlcv first); an empty/uncached range publishes nothing and returns "
    "count=0. Results are derived and NOT persisted — reopening the viewer "
    "re-runs the sweep. Optional `patterns` keeps only the named detectors (e.g. "
    "['morning_star','doji']); optional `min_strength` (0..1) drops weak hits. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


async def _scan_patterns_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    patterns: list[str] | None,
    min_strength: float | None,
) -> dict[str, Any]:
    """Body of the `scan_patterns` tool: validate at the boundary, read cached bars
    through the provider, map them to markers off-thread, and publish one
    `chart.highlight` event when there is anything to show."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)

    bars = await asyncio.to_thread(
        provider.get_ohlcv, symbol, timeframe, range_start, range_end, None
    )
    markers = await asyncio.to_thread(
        markers_for_range, list(bars), patterns=patterns, min_strength=min_strength
    )
    if not markers:
        return {
            "event_published": False,
            "type": "chart.highlight",
            "version": ChartHighlightPayloadV1.VERSION,
            "count": 0,
        }
    payload = ChartHighlightPayloadV1(symbol=symbol, timeframe=timeframe, markers=markers)
    event_bus.publish("chart.highlight", payload)
    return {
        "event_published": True,
        "type": "chart.highlight",
        "version": ChartHighlightPayloadV1.VERSION,
        "count": len(markers),
    }


def register_scan_patterns(
    server: FastMCP, *, provider: MarketDataProvider, event_bus: EventBus
) -> None:
    """Bind the `scan_patterns` tool to `server`. The provider and event bus are
    captured by closure so the tool body keeps the parameters FastMCP introspects
    to build the input schema."""

    @server.tool(description=SCAN_PATTERNS_DESCRIPTION)
    async def scan_patterns(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        patterns: list[str] | None = None,
        min_strength: float | None = None,
    ) -> dict[str, Any]:
        return await _scan_patterns_response(
            provider=provider,
            event_bus=event_bus,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            patterns=patterns,
            min_strength=min_strength,
        )


__all__ = [
    "SCAN_PATTERNS_DESCRIPTION",
    "_scan_patterns_response",
    "register_scan_patterns",
]
