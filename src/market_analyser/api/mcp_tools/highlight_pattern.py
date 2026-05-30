"""`highlight_pattern` MCP tool (Plan 0007; extracted Plan 0017).

Publishes a `chart.highlight v1` event AND persists each marker as an annotation
row so the highlight survives a viewer reload. Persist-then-publish order is
preserved exactly: the annotation insert happens before `event_bus.publish`, so a
persistence failure surfaces as an MCP error before the live event lands.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.annotations.types import Annotation, AnnotationKind
from market_analyser.api.events import ChartHighlightPayloadV1, EventBus, Marker
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository


def register_highlight_pattern(
    server: FastMCP,
    *,
    annotations_repository: AnnotationsRepository,
    event_bus: EventBus,
) -> None:
    """Bind the `highlight_pattern` tool to `server`. The repository and event bus
    are captured by closure so the tool body keeps the declared parameters FastMCP
    introspects to build the input schema."""

    @server.tool(
        description=(
            "Highlight a pattern on a chart. Publishes a `chart.highlight v1` "
            "event AND persists each marker as an annotation row (so the "
            "highlight survives a viewer reload). Use this for patterns you "
            "detected NOW; use `write_annotation` for the lower-level "
            "persist-only primitive."
        ),
    )
    def highlight_pattern(
        symbol: str,
        timeframe: str,
        event_ts: datetime,
        kind: AnnotationKind,
        label: str | None = None,
        agent_id: str = "unknown",
    ) -> dict[str, Any]:
        _require_non_empty_symbol(symbol)
        _require_supported_timeframe(timeframe)
        # `AnnotationKind` is a `StrEnum`; its value is one of the literal
        # strings `Marker.kind` accepts. Pydantic accepts the enum at runtime,
        # but mypy can't widen the StrEnum to the Literal — coerce to plain
        # str so the type-checker sees the narrowed value.
        marker = Marker(event_ts=event_ts, kind=str(kind), label=label)  # type: ignore[arg-type]
        payload = ChartHighlightPayloadV1(
            symbol=symbol,
            timeframe=timeframe,
            markers=[marker],
        )
        # Persist first (so re-opening Electron sees the marker via the
        # annotations table), then publish (so the live viewer renders it
        # immediately). Failure to persist surfaces as an MCP error before
        # the live event lands.
        annotation = Annotation(
            symbol=symbol,
            timeframe=timeframe,
            event_ts=event_ts,
            kind=kind,
            label=label,
            agent_id=agent_id,
        )
        annotations_repository.insert(annotation)
        event_bus.publish("chart.highlight", payload)
        return {
            "event_published": True,
            "type": "chart.highlight",
            "version": ChartHighlightPayloadV1.VERSION,
        }


__all__ = ["register_highlight_pattern"]
