"""`detect_chart_patterns` MCP tool (Plan 0052 phase 2, ADR-0048, ADR-0049).

Detects classical chart patterns (head & shoulders, doubles, triangles, wedges)
over cached bars AND draws their defining geometry in one call: publishes a
single `chart.trendlines v1` event carrying one `TrendlineSpec` per hit line —
the neckline of an H&S / double, the two bounding trendlines of a triangle /
wedge — with `style="dashed"` for `forming` hits and `"solid"` for `confirmed`
ones (the renderer's provisional-vs-fact cue). The event is layer-only and
active-chart-gated (ADR-0059), like `highlight_pattern`: it draws onto the chart
already showing that symbol/timeframe rather than mounting one, so a subsequent
`chart.show` can no longer race and wipe the lines.

Hits are *derived* data: nothing is persisted. The renderer re-derives them from
the current bars — the `POST /scan_chart_patterns` route runs this exact
detection on chart load / visible-range change (Plan 0064 phase 5), so reopening
the viewer or panning re-runs the detection rather than reading a stale cache.
The math is the pure `analysis.chart_patterns.detect_chart_patterns` (trailing,
deterministic — ADR-0048). The body is factored out as
`_detect_chart_patterns_response` (in `mcp_tools/_shared`, Plan 0072 phase 3) so
both entry points (this tool and the route) share one core and the fetch +
filter + empty-cache paths are unit-testable on a single event loop (no live MCP
server needed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.analysis.chart_patterns import CHART_PATTERNS
from market_analyser.api.mcp_tools._shared.chart_patterns_response import (
    _detect_chart_patterns_response,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.events import EventBus

DETECT_CHART_PATTERNS_DESCRIPTION = (
    "Detect classical chart patterns on the cached bars and draw them on the "
    "chart in one call: recognises head & shoulders (+inverse), double "
    "top/bottom, ascending/descending/symmetrical triangles, and "
    "rising/falling wedges over confirmed swing pivots, returns the typed hits "
    "as data (pattern, forming/confirmed state, direction, pivots, defining "
    "lines, measured-move target, strength), AND publishes a single "
    "`chart.trendlines v1` event carrying one trendline per hit line (dashed = "
    "forming, solid = confirmed) onto the chart already showing that "
    "symbol/timeframe. Strictly trailing: a hit at bar i reads only "
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
    "register_detect_chart_patterns",
]
