"""`PUT /user_drawings/{symbol}` — mirror the renderer's user drawing set (Plan 0104, ADR-0099).

The write half of the drawing read-back loop. Renderer-bearer-gated by the central
middleware (an agent on `/mcp` cannot PUT here — the ADR-0014 cross-tenant split).
The renderer sends the FULL user drawing set for `symbol` as a declarative replace
(the whole set each sync, ADR-0099), which lands in the in-memory
`UserDrawingsMirror`; the agent reads it back through the `get_chart_drawings`
MCP tool.

Every spec must carry `provenance="user"` — a user drawing never claims agent
provenance on the way in — else 422 (the mirror is user-drawings-only, the inverse
of the agent-only `chart.annotations` wire). Malformed geometry is rejected 422 by
the `DrawingSpec` model itself before it reaches the mirror.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from market_analyser.events.drawing_types import DrawingSpec
from market_analyser.user_drawings import UserDrawingsMirror

router = APIRouter(tags=["user-drawings"])


@router.put("/user_drawings/{symbol}")
def put_user_drawings(
    symbol: str,
    drawings: list[DrawingSpec],
    request: Request,
) -> dict[str, Any]:
    """Declaratively replace `symbol`'s mirrored user drawing set. Returns the
    stamped `synced_at` so the renderer can confirm the sync landed."""
    for spec in drawings:
        if spec.provenance != "user":
            raise HTTPException(
                status_code=422,
                detail=(
                    "user_drawings mirrors user-provenance drawings only; got "
                    f"provenance {spec.provenance!r} on drawing {spec.id!r}"
                ),
            )
    mirror: UserDrawingsMirror = request.app.state.user_drawings_mirror
    snapshot = mirror.replace(symbol, drawings, synced_at=datetime.now(tz=UTC))
    return {
        "symbol": snapshot.symbol,
        "drawing_count": len(snapshot.drawings),
        "synced_at": snapshot.synced_at.isoformat() if snapshot.synced_at else None,
    }
