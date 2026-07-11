"""`show_chart` MCP tool (Plan 0007; extracted Plan 0017).

Publishes a `chart.show v1` event to the SSE stream so the Electron viewer mounts
the requested symbol/timeframe and window. Returns immediately whether or not a
viewer is connected — events are ephemeral and not replayed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import (
    _parse_overlays,
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.events import ChartShowPayloadV1, EventBus


def register_show_chart(server: FastMCP, *, event_bus: EventBus) -> None:
    """Bind the `show_chart` tool to `server`. The event bus is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(
        description=(
            "Render a chart in the Electron viewer. Publishes a `chart.show v1` "
            "event to the SSE stream. The renderer mounts/switches to the "
            "requested symbol+timeframe and renders the requested window with "
            "the supplied overlays. Overlay `kind`s: indicator overlays "
            "`ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`/`ichimoku`/`obv` "
            "(computed and drawn client-side — `supertrend` takes an optional "
            "`multiplier`, `ichimoku` optional `conversion`/`base`/`span_b`/"
            "`displacement` periods defaulting to 9/26/52/26, `obv` carries no "
            "fields and draws in its own pane) and `price_line` (a labelled "
            "horizontal line for support/resistance). Returns immediately whether "
            "or not a viewer is connected — events are ephemeral; reopening "
            "Electron after a call to this tool will not replay it."
        ),
    )
    def show_chart(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        overlays: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        _require_ordered_range(range_start, range_end)
        payload = ChartShowPayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            overlays=_parse_overlays(overlays),
        )
        event_bus.publish("chart.show", payload)
        return {
            "event_published": True,
            "type": "chart.show",
            "version": ChartShowPayloadV1.VERSION,
        }


__all__ = ["register_show_chart"]
