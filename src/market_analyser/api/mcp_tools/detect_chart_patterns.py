"""`detect_chart_patterns` MCP tool (Plan 0052 phase 2, ADR-0048, ADR-0049).

Detects classical chart patterns (head & shoulders, doubles, triangles, wedges)
over cached bars AND auto-draws their defining geometry in one call: publishes a
single `chart.show v1` event carrying one `TrendlineSpec` per hit line — the
neckline of an H&S / double, the two bounding trendlines of a triangle / wedge —
with `style="dashed"` for `forming` hits and `"solid"` for `confirmed` ones
(the renderer's provisional-vs-fact cue). `chart.show` (not `chart.update`) so
the one call also mounts the symbol/timeframe and window when no chart is up
yet, mirroring `detect_levels`.

Hits are *derived* data: nothing is persisted; reopening the viewer re-runs the
detection. The math is the pure `analysis.chart_patterns.detect_chart_patterns`
(trailing, deterministic — ADR-0048). The body is factored out as
`_detect_chart_patterns_response` so the fetch + filter + empty-cache paths are
unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from market_analyser.analysis.chart_patterns import CHART_PATTERNS, detect_chart_patterns
from market_analyser.analysis.types import ChartPatternHit
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.events import ChartShowPayloadV1, EventBus, TrendlineSpec, TrendPoint

_PATTERN_STATES = ("forming", "confirmed")

DETECT_CHART_PATTERNS_DESCRIPTION = (
    "Detect classical chart patterns on the cached bars and draw them on the "
    "chart in one call: recognises head & shoulders (+inverse), double "
    "top/bottom, ascending/descending/symmetrical triangles, and "
    "rising/falling wedges over confirmed swing pivots, returns the typed hits "
    "as data (pattern, forming/confirmed state, direction, pivots, defining "
    "lines, measured-move target, strength), AND publishes a single "
    "`chart.show v1` event carrying one trendline per hit line (dashed = "
    "forming, solid = confirmed). Strictly trailing: a hit at bar i reads only "
    "bars up to i; `forming` means the geometry is complete but the breakout "
    "close has not happened, `confirmed` means a close broke the "
    "neckline/trendline by the ATR-scaled margin. Optional `patterns` / "
    f"`states` filter the hits (patterns from {', '.join(CHART_PATTERNS)}; "
    "states from forming, confirmed). Reads cached bars only (backfill via "
    "get_ohlcv first); an empty/uncached range publishes nothing and returns "
    "count=0. Results are derived and NOT persisted. Conditions only — hits "
    "are geometry facts, never buy/sell advice. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


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
    publish one `chart.show` event when there is anything to draw."""

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
            "type": "chart.show",
            "version": ChartShowPayloadV1.VERSION,
            "count": 0,
        }
    payload = ChartShowPayloadV1(
        symbol=symbol,
        timeframe=timeframe,
        range_start=range_start,
        range_end=range_end,
        trendlines=_hit_trendlines(hits),
    )
    event_bus.publish("chart.show", payload)
    return {
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "event_published": True,
        "type": "chart.show",
        "version": ChartShowPayloadV1.VERSION,
        "count": len(hits),
    }


def register_detect_chart_patterns(
    server: FastMCP, *, provider: MarketDataProvider, event_bus: EventBus
) -> None:
    """Bind the `detect_chart_patterns` tool to `server`. The provider and event
    bus are captured by closure so the tool body keeps the parameters FastMCP
    introspects to build the input schema."""

    @server.tool(description=DETECT_CHART_PATTERNS_DESCRIPTION)
    async def detect_chart_patterns(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        patterns: list[str] | None = None,
        states: list[str] | None = None,
    ) -> dict[str, Any]:
        return await _detect_chart_patterns_response(
            provider=provider,
            event_bus=event_bus,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            patterns=patterns,
            states=states,
        )


__all__ = [
    "DETECT_CHART_PATTERNS_DESCRIPTION",
    "_detect_chart_patterns_response",
    "register_detect_chart_patterns",
]
