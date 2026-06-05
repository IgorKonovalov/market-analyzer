"""`GET /quote` — a live, timeframe-independent quote for one symbol (Plan 0047).

The renderer's price header polls this so the displayed "current price" is a
symbol-level live value, not the last bar's close of whatever timeframe happens
to be selected (which changes as you switch 1h→1d and is only as fresh as the
cache). It wraps the existing `provider.get_quote` — the `YahooQuoteAdapter`
behind the `quote_for`/`market_snapshot` MCP tools (ADR-0019) — so the route adds
no new upstream, only a renderer-facing surface.

Renderer-bearer-gated by the central middleware in `app.py`; a request bearing
the MCP secret is rejected cross-tenant (the agent uses the `quote_for` MCP tool
instead). Error mapping mirrors `routes/ohlcv.py`: an unknown symbol → 404, a
throttle → 429, any upstream/adapter failure → 502, a bad-input `ValueError` →
422 — never a bare 500.

`QuoteResponse` is the frozen renderer envelope: a deliberately narrow projection
of the richer `Quote` domain model (the renderer needs price + change + currency
+ timestamp, not the full Yahoo meta block). It lives here as a named
`response_model` so `gen-types.mjs` emits it into `renderer/types/sidecar/`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Quote

router = APIRouter()


class QuoteResponse(BaseModel):
    """Renderer envelope for `GET /quote`: the symbol-level live price the price
    header shows, plus its day change, currency, and upstream timestamp."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    price: float
    change_pct: float | None
    """Day change vs previous close, as a fraction/percentage per the adapter;
    `None` when the upstream omitted previous close."""
    currency: str
    as_of: datetime
    """The quote's own upstream timestamp (Yahoo `regularMarketTime`), ISO 8601 UTC."""

    @classmethod
    def from_quote(cls, quote: Quote) -> QuoteResponse:
        return cls(
            symbol=quote.symbol,
            price=quote.price,
            change_pct=quote.change_pct,
            currency=quote.currency,
            as_of=quote.as_of,
        )


@router.get("/quote", response_model=QuoteResponse)
def get_quote(
    request: Request,
    symbol: str = Query(min_length=1),
) -> QuoteResponse:
    provider: MarketDataProvider = request.app.state.provider
    try:
        quote = provider.get_quote(symbol=symbol)
    except UnknownSymbolError as exc:
        # The symbol doesn't exist upstream — 404, distinct from a transient outage.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RateLimitedError as exc:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )
        raise HTTPException(status_code=429, detail=str(exc), headers=headers) from exc
    except (UpstreamUnavailableError, UpstreamDataError, ResilientHttpError) as exc:
        # Upstream failed or exhausted retries — a clean 502, never a bare 500.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QuoteResponse.from_quote(quote)
