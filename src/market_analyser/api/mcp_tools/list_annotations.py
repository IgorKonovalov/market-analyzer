"""`list_annotations` MCP tool (Plan 0006; extracted Plan 0017).

Reads annotations for a symbol/timeframe window with the same boundary-inclusive
semantics as the renderer's GET /annotations. Uses the shared
`_require_supported_timeframe` helper (resolving the prior inline-vs-helper
inconsistency where this tool hand-rolled its own timeframe check).
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.annotations.types import Annotation
from market_analyser.api.mcp_tools._validation import _require_supported_timeframe
from market_analyser.persistence.annotations_repository import AnnotationsRepository


def register_list_annotations(
    server: FastMCP,
    *,
    annotations_repository: AnnotationsRepository,
) -> None:
    """Bind the `list_annotations` tool to `server`. The repository is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(
        description=(
            "List annotations for a symbol/timeframe over a [start, end] window. "
            "Boundary-inclusive on both ends. Returns annotations from all agents "
            "(no per-agent_id filter)."
        ),
    )
    def list_annotations(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Annotation]:
        _require_supported_timeframe(timeframe)
        return annotations_repository.list_for(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )


__all__ = ["register_list_annotations"]
