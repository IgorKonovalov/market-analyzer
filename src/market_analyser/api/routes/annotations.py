"""`GET`/`DELETE /annotations` — agent-written chart annotations. Plan 0006 phase 3.

Reads through `AnnotationsRepository.list_for` directly (no provider layer):
annotations are app-private state, not an external data-source, so the
provider Protocol that exists for OHLCV does not apply here.

`DELETE /annotations/{id}` (Plan 0016) lets the renderer surface remove a single
row by id. It is deliberately renderer-only — there is no MCP delete tool — so
agent writes are append-only from the agent's side and cleanup is a
renderer-surface concern.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response

from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES, Annotation
from market_analyser.persistence.annotations_repository import AnnotationsRepository

router = APIRouter()


@router.get("/annotations", response_model=list[Annotation])
def get_annotations(
    request: Request,
    symbol: str = Query(min_length=1),
    timeframe: str = Query(min_length=1),
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> list[Annotation]:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"timeframe {timeframe!r} not supported (supported: {sorted(SUPPORTED_TIMEFRAMES)})"
            ),
        )
    repo: AnnotationsRepository = request.app.state.annotations_repository
    try:
        return repo.list_for(symbol=symbol, timeframe=timeframe, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/annotations/{annotation_id}", status_code=204)
def delete_annotation(request: Request, annotation_id: str) -> Response:
    """Delete a single annotation by id. 404 when the id is unknown.

    Renderer-bearer-gated by the central middleware in `app.py`; the MCP tenant
    cannot reach it (same cross-tenant rule as the GET route).
    """
    repo: AnnotationsRepository = request.app.state.annotations_repository
    if not repo.delete(annotation_id):
        raise HTTPException(status_code=404, detail=f"no annotation with id {annotation_id!r}")
    return Response(status_code=204)
