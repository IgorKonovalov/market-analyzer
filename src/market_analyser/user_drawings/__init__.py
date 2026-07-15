"""Neutral user-drawings mirror core (Plan 0104, ADR-0099).

The read-only, ephemeral shadow of the renderer's user drawing set — a sibling of
the `ui_events` core (ADR-0065): shared in-memory state the api layer writes (the
`PUT /user_drawings` route) and reads (the `get_chart_drawings` MCP tool), held
here so neither producer nor consumer depends up into the transport layer.
"""

from __future__ import annotations

from market_analyser.user_drawings.mirror import (
    UserDrawingsMirror,
    UserDrawingsSnapshot,
)

__all__ = ["UserDrawingsMirror", "UserDrawingsSnapshot"]
