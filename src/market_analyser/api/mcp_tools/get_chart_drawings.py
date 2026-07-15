"""`get_chart_drawings` MCP tool (Plan 0104, ADR-0099) — read the user's drawings.

Returns the renderer's mirrored user drawing set for a symbol plus a `synced_at`
timestamp. This is the agent's read side of the drawing loop: the user draws a
resistance line and asks "what do you think about this level?", and this tool is
how the agent sees the actual drawn geometry.

Read-only shadow — the renderer owns the drawings (ADR-0091/0099); this tool never
writes. `synced_at` is `null` when nothing has synced since the sidecar booted, so
a closed viewer ("never synced") reads distinctly from "synced, no drawings". The
mirror is ephemeral (cleared on restart), so the agent must read `synced_at` to
judge staleness before trusting the set.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import _require_non_empty_symbol
from market_analyser.user_drawings import UserDrawingsMirror, UserDrawingsSnapshot

GET_CHART_DRAWINGS_DESCRIPTION = (
    "Read the drawings the USER placed on a symbol's chart (trendlines, rays, "
    "h/v-lines, rectangles, fib grids, long/short position boxes, and date/price "
    "range measures) — use this to see and reason about what the user drew, e.g. "
    "'what do you think about this resistance line I drew?'. Returns `{symbol, "
    "drawings, synced_at}` where each drawing is `{kind, points, id, ...}` "
    "anchored at `(ts, price)`. `synced_at` is an ISO timestamp of the last sync, "
    "or null when the viewer has not synced this symbol since the sidecar started "
    "— null (or a stale timestamp) means the set may not reflect what is on screen "
    "now, so read it before trusting an empty list. This is a READ-ONLY mirror; "
    "the user owns their drawings — place your own with annotate_chart instead."
)


def register_get_chart_drawings(
    server: FastMCP, *, user_drawings_mirror: UserDrawingsMirror
) -> None:
    """Bind the `get_chart_drawings` tool to `server`. The mirror is captured by
    closure — the same instance the `PUT /user_drawings` route writes."""

    @server.tool(description=GET_CHART_DRAWINGS_DESCRIPTION)
    def get_chart_drawings(symbol: str) -> UserDrawingsSnapshot:
        _require_non_empty_symbol(symbol)
        return user_drawings_mirror.snapshot(symbol)


__all__ = ["GET_CHART_DRAWINGS_DESCRIPTION", "register_get_chart_drawings"]
