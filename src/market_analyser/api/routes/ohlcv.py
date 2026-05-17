"""`GET /ohlcv` — OHLCV history for one symbol. Plan 0001 phase 2.

The handler delegates to `request.app.state.provider.get_ohlcv(...)`. The
provider implementation is injected at `create_app` time so tests can substitute
a `FakeMarketDataProvider`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar

router = APIRouter()


@router.get("/ohlcv", response_model=list[Bar])
def get_ohlcv(
    request: Request,
    symbol: str = Query(min_length=1),
    timeframe: str = Query(default="1d", min_length=1),
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> Sequence[Bar]:
    provider: MarketDataProvider = request.app.state.provider
    try:
        return provider.get_ohlcv(symbol=symbol, timeframe=timeframe, start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
