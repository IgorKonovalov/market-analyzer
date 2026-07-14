"""`annotate_chart` MCP tool (Plan 0097, ADR-0091).

Publishes a `chart.annotations v1` event carrying the agent's freeform-drawing
set for a symbol — a declarative REPLACE of the agent annotation set, mirroring
how `update_chart` replaces the agent overlay set. The renderer merges these
with the user's local drawings (one render path, provenance-scoped editing:
agent drawings are hide-only) and never persists them — re-issue after a
viewer reload.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import _require_non_empty_symbol
from market_analyser.events import ChartAnnotationsPayloadV1, DrawingSpec, EventBus


def _parse_drawings(raw: list[dict[str, Any]]) -> list[DrawingSpec]:
    """Validate each drawing dict into a `DrawingSpec`, stamping agent
    provenance. A spec claiming any other provenance is rejected loudly — the
    tool never silently rewrites what the caller asserted (user drawings never
    cross the wire, ADR-0091)."""
    specs: list[DrawingSpec] = []
    for item in raw:
        claimed = item.get("provenance")
        if claimed is not None and claimed != "agent":
            raise ValueError(
                "annotate_chart places agent drawings only; "
                f"got provenance {claimed!r} (omit the field or pass 'agent')"
            )
        specs.append(DrawingSpec.model_validate({**item, "provenance": "agent"}))
    return specs


def register_annotate_chart(server: FastMCP, *, event_bus: EventBus) -> None:
    """Bind the `annotate_chart` tool to `server`. The event bus is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects
    to build the input schema."""

    @server.tool(
        description=(
            "Place freeform drawings (annotations) on a symbol's chart. "
            "Publishes a `chart.annotations v1` event that declaratively "
            "REPLACES your previous annotation set for `symbol` — send the "
            "full set each time; an empty `drawings` list clears it. Each "
            "drawing is `{kind, points, id?, style?}` with `points` as "
            "`[{ts, price}, ...]` anchors: `trendline` (segment, 2 points), "
            "`ray` (through 2 points, extended right), `hline` (horizontal "
            "line at the point's price, 1 point), `vline` (vertical line at "
            "the point's ts, 1 point), `rect` (zone between 2 corner points), "
            "`fib` (Fibonacci retracement grid between 2 anchor points). "
            "Drawings are per-symbol and render on every timeframe (anchored "
            "to time+price, not bars). Supply your own stable `id` per drawing "
            "so the user's hide choices survive a re-push; `style` is optional "
            "`{color?, width?}`. Agent drawings render hide-only for the user "
            "(their own drawings stay editable) and are not persisted by the "
            "viewer — re-issue after a reload."
        ),
    )
    def annotate_chart(
        symbol: str,
        drawings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        payload = ChartAnnotationsPayloadV1(
            symbol=symbol,
            drawings=_parse_drawings(drawings),
        )
        event_bus.publish("chart.annotations", payload)
        return {
            "event_published": True,
            "type": "chart.annotations",
            "version": ChartAnnotationsPayloadV1.VERSION,
        }


__all__ = ["register_annotate_chart"]
