"""`GET /annotations` — read agent-written annotations for the active chart. Plan 0006 phase 3.

Reads through `AnnotationsRepository.list_for` directly (no provider layer):
annotations are app-private state, not an external data-source, so the
provider Protocol that exists for OHLCV does not apply here.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

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
