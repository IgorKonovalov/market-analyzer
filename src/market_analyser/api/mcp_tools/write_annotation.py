"""`write_annotation` MCP tool (Plan 0006; extracted Plan 0017).

Persists an agent-written chart marker and returns the populated `Annotation`
(with `id` and `created_at` filled by the model's default factories) so the
agent can reference its writes.
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.annotations.types import Annotation, AnnotationKind
from market_analyser.persistence.annotations_repository import AnnotationsRepository


def register_write_annotation(
    server: FastMCP,
    *,
    annotations_repository: AnnotationsRepository,
) -> None:
    """Bind the `write_annotation` tool to `server`. The repository is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(
        description=(
            "Write a chart annotation (bullish/bearish marker on a single "
            "candle). Returns the persisted record with its id and created_at. "
            "`label` is the hover text; `agent_id` is your opaque identifier "
            "(defaults to 'unknown')."
        ),
    )
    def write_annotation(
        symbol: str,
        timeframe: str,
        event_ts: datetime,
        kind: AnnotationKind,
        label: str | None = None,
        agent_id: str = "unknown",
    ) -> Annotation:
        annotation = Annotation(
            symbol=symbol,
            timeframe=timeframe,
            event_ts=event_ts,
            kind=kind,
            label=label,
            agent_id=agent_id,
        )
        annotations_repository.insert(annotation)
        return annotation


__all__ = ["register_write_annotation"]
