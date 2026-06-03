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
from market_analyser.data.errors import (
    HistoryExceededError,
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
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
    except UnknownSymbolError as exc:
        # The symbol doesn't exist upstream. Distinct from an empty *historical*
        # window, which the adapter returns as `[]` (200), not this error
        # (ADR-0033) — so 404 here means "no such symbol", never "ran out of history".
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitedError as exc:
        # Surface the upstream throttle so the caller can back off; carry the
        # upstream `Retry-After` (whole seconds) as the standard HTTP header.
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except HistoryExceededError as exc:
        # The window reaches past the timeframe's Yahoo horizon — not retryable;
        # narrowing the window or using a coarser timeframe is the fix.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UpstreamUnavailableError, UpstreamDataError, ResilientHttpError) as exc:
        # Upstream (Yahoo) failed or exhausted retries. The adapter re-classifies a
        # ResilientHttpError into the typed taxonomy (Plan 0013), so the real path
        # raises UpstreamUnavailableError / the UpstreamDataError base; the bare
        # ResilientHttpError stays as a backstop for any path that doesn't reclassify.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
