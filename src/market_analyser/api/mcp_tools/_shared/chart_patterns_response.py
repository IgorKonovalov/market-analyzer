"""The `detect_chart_patterns` detection body, shared by the tool and the route.

Validate at the boundary, read cached bars through the provider, run the pure
`analysis.chart_patterns.detect_chart_patterns` off-thread, filter, and publish
one `chart.trendlines v1` event when there is anything to draw. Both entry
points — the `detect_chart_patterns` MCP tool and the `POST /scan_chart_patterns`
route (Plan 0064) — call this one core so the fetch + filter + empty-cache paths
cannot drift and are unit-testable on a single event loop.

Moved out of `mcp_tools/detect_chart_patterns.py` into `_shared` in Plan 0072
phase 3 so the route stops importing a `_`-private out of the tool module.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from market_analyser.analysis.chart_patterns import CHART_PATTERNS, detect_chart_patterns
from market_analyser.analysis.types import ChartPatternHit
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.events import ChartTrendlinesPayloadV1, EventBus, TrendlineSpec, TrendPoint

_PATTERN_STATES = ("forming", "confirmed")


def _hit_trendlines(hits: list[ChartPatternHit]) -> list[TrendlineSpec]:
    """One `TrendlineSpec` per hit line, in hit order: anchors on the line's
    real pivot endpoints, dashed for forming / solid for confirmed, labelled
    with the pattern id + state so the chart reads unambiguously."""

    specs: list[TrendlineSpec] = []
    for hit in hits:
        style: Literal["solid", "dashed"] = "dashed" if hit.state == "forming" else "solid"
        for line in hit.lines:
            specs.append(
                TrendlineSpec(
                    points=[
                        TrendPoint(ts=line.start.ts, price=line.start.price),
                        TrendPoint(ts=line.end.ts, price=line.end.price),
                    ],
                    role=line.role,
                    style=style,
                    label=f"{hit.pattern} ({hit.state})",
                    pattern=hit.pattern,
                )
            )
    return specs


async def _detect_chart_patterns_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    patterns: list[str] | None = None,
    states: list[str] | None = None,
) -> dict[str, Any]:
    """Body of the `detect_chart_patterns` tool: validate at the boundary, read
    cached bars through the provider, run detection off-thread, filter, and
    publish one `chart.trendlines` event when there is anything to draw.

    Shared by the `POST /scan_chart_patterns` route (Plan 0064 phase 3), which is
    why the fetch + filter + empty-cache paths live here rather than in the tool
    closure."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)
    if patterns is not None:
        unknown = sorted(set(patterns) - set(CHART_PATTERNS))
        if unknown:
            raise ValueError(
                f"unknown patterns: {', '.join(unknown)}; supported: {', '.join(CHART_PATTERNS)}"
            )
    if states is not None:
        bad_states = sorted(set(states) - set(_PATTERN_STATES))
        if bad_states:
            raise ValueError(
                f"unknown states: {', '.join(bad_states)}; supported: forming, confirmed"
            )

    bars = await asyncio.to_thread(
        provider.get_ohlcv, symbol, timeframe, range_start, range_end, None
    )
    hits = await asyncio.to_thread(detect_chart_patterns, list(bars))
    if patterns is not None:
        hits = [h for h in hits if h.pattern in patterns]
    if states is not None:
        hits = [h for h in hits if h.state in states]

    if not hits:
        return {
            "hits": [],
            "event_published": False,
            "type": "chart.trendlines",
            "version": ChartTrendlinesPayloadV1.VERSION,
            "count": 0,
        }
    payload = ChartTrendlinesPayloadV1(
        symbol=symbol,
        timeframe=timeframe,
        trendlines=_hit_trendlines(hits),
    )
    event_bus.publish("chart.trendlines", payload)
    return {
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "event_published": True,
        "type": "chart.trendlines",
        "version": ChartTrendlinesPayloadV1.VERSION,
        "count": len(hits),
    }


__all__ = ["_detect_chart_patterns_response"]
