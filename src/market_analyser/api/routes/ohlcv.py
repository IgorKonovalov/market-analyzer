"""`GET /ohlcv` — OHLCV history for one symbol. Plan 0001 phase 2.

The handler delegates to `request.app.state.provider.get_ohlcv(...)`. The
provider implementation is injected at `create_app` time so tests can substitute
a `FakeMarketDataProvider`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from market_analyser.data._http import ResilientHttpError
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
    except ResilientHttpError as exc:
        # Upstream (Yahoo) failed or exhausted retries — surface a clean 502 so
        # the caller sees an honest "upstream unavailable" instead of a 500 with a
        # stack trace. The richer typed-error taxonomy is Plan 0013's scope.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
