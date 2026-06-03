"""`GET /news` — recent headlines + aggregate tone for the renderer (Plan 0023).

A renderer read route, gated by the per-launch renderer bearer via the central
middleware in `app.py` (a request carrying the MCP secret is rejected
cross-tenant — the agent uses the `news_for`/`sentiment_for_news` MCP tools
instead). Delegates to `request.app.state.provider`:

- `get_news(..., with_sentiment=True)` for the headline list, and
- `get_sentiment(...)` for the aggregate tone, *only when a symbol is supplied*.

The second call hits Plan 0010's resilience layer (5-minute feed-body TTL cache,
same feeds), so it costs no extra network round-trip. There is **no `as_of`
parameter** — news is wall-clock-sensitive (Plan 0010 / ADR-0019); the route
exposes no replay surface by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request

from market_analyser.api.routes._news_models import NewsResponse
from market_analyser.data._windows import SentimentWindow
from market_analyser.data.provider import MarketDataProvider

router = APIRouter()


def _now() -> datetime:
    """Wall-clock seam (mirrors `default_provider._now`) so `queried_at` is mockable."""
    return datetime.now(UTC)


@router.get("/news", response_model=NewsResponse)
def get_news(
    request: Request,
    symbol: str | None = None,
    window: SentimentWindow = "24h",
    limit: int = Query(default=50, ge=1, le=100),
) -> NewsResponse:
    provider: MarketDataProvider = request.app.state.provider
    items = provider.get_news(
        symbol=symbol,
        window=window,
        limit=limit,
        with_sentiment=True,
    )
    # No symbol → no per-symbol aggregate. A blank/whitespace symbol is treated
    # as "no symbol" so a just-cleared input box browses all feeds, never 422s.
    sentiment = (
        provider.get_sentiment(symbol=symbol, window=window) if symbol and symbol.strip() else None
    )
    return NewsResponse(items=list(items), sentiment=sentiment, queried_at=_now())
