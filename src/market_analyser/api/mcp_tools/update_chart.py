"""`update_chart` MCP tool (Plan 0007; extracted Plan 0017).

Publishes a `chart.update v1` delta to the SSE stream. Any subset of {overlays,
range_start, range_end, focus_bar} may be supplied; unset fields are not carried
on the wire (the renderer merges the delta into its current state).
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
from market_analyser.events import ChartUpdatePayloadV1, EventBus


def register_update_chart(server: FastMCP, *, event_bus: EventBus) -> None:
    """Bind the `update_chart` tool to `server`. The event bus is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(
        description=(
            "Apply a delta to the currently-rendered chart. Publishes a "
            "`chart.update v1` event. Any subset of {overlays, range_start, "
            "range_end, focus_bar} may be supplied; unset fields are not "
            "carried on the wire (the renderer merges the delta into its "
            "current state). If no chart for `symbol`+`timeframe` is currently "
            "open in the viewer, the renderer treats this as a `chart.show`."
        ),
    )
    def update_chart(
        symbol: str,
        timeframe: str,
        overlays: list[dict[str, Any]] | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        focus_bar: datetime | None = None,
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        _require_ordered_range(range_start, range_end)
        payload = ChartUpdatePayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            overlays=_parse_overlays(overlays),
            range_start=range_start,
            range_end=range_end,
            focus_bar=focus_bar,
        )
        event_bus.publish("chart.update", payload)
        return {
            "event_published": True,
            "type": "chart.update",
            "version": ChartUpdatePayloadV1.VERSION,
        }


__all__ = ["register_update_chart"]
