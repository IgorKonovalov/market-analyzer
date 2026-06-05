"""Plan 0047 phase 3 done-when: GET /quote returns a live QuoteResponse.

Hermetic — a fake provider supplies the quote; the live Yahoo path is covered by
the adapter tests. Covers: 200 + BTC-USD with the renderer bearer, 401 without
it, cross-tenant MCP bearer → 401, unknown symbol → 404, throttle → 429, upstream
failure → 502, and a bad-input ValueError → 422 (never a bare 500).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret"

_QUOTE = Quote(
    symbol="BTC-USD",
    price=61_335.75,
    as_of=datetime(2026, 6, 5, 14, 30, tzinfo=UTC),
    source="yahoo",
    change_pct=2.41,
    previous_close=59_891.0,
    currency="USD",
    market_state="REGULAR",
)


class _QuoteProvider:
    """Provider stub whose only live method is get_quote."""

    def __init__(self, quote: Quote) -> None:
        self._quote = quote
        self.calls: list[str] = []

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        self.calls.append(symbol)
        return self._quote

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _client(provider: _QuoteProvider) -> TestClient:
    return TestClient(create_app(secret=RENDERER_SECRET, provider=provider))


def test_quote_returns_envelope_with_auth() -> None:
    provider = _QuoteProvider(_QUOTE)
    response = _client(provider).get(
        "/quote", params={"symbol": "BTC-USD"}, headers=_renderer_auth()
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["symbol"] == "BTC-USD"
    assert payload["price"] == 61_335.75
    assert payload["change_pct"] == 2.41
    assert payload["currency"] == "USD"
    assert payload["as_of"].startswith("2026-06-05T14:30:00")
    # The narrow envelope drops the Quote's richer Yahoo-meta fields.
    assert "previous_close" not in payload
    assert provider.calls == ["BTC-USD"]


def test_quote_returns_401_without_auth() -> None:
    response = _client(_QuoteProvider(_QUOTE)).get("/quote", params={"symbol": "BTC-USD"})
    assert response.status_code == 401


def test_quote_unknown_symbol_returns_404() -> None:
    class UnknownProvider(_QuoteProvider):
        def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
            raise UnknownSymbolError("yahoo: no such symbol", symbol=symbol)

    response = _client(UnknownProvider(_QUOTE)).get(
        "/quote", params={"symbol": "NOPE-USD"}, headers=_renderer_auth()
    )
    assert response.status_code == 404


def test_quote_rate_limited_returns_429_with_retry_after() -> None:
    class ThrottledProvider(_QuoteProvider):
        def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
            raise RateLimitedError("yahoo: throttled", retry_after_seconds=7)

    response = _client(ThrottledProvider(_QUOTE)).get(
        "/quote", params={"symbol": "BTC-USD"}, headers=_renderer_auth()
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


def test_quote_upstream_error_returns_502() -> None:
    class DownProvider(_QuoteProvider):
        def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
            raise UpstreamUnavailableError("yahoo: upstream unavailable on quote")

    response = _client(DownProvider(_QUOTE)).get(
        "/quote", params={"symbol": "BTC-USD"}, headers=_renderer_auth()
    )
    assert response.status_code == 502
    assert "yahoo" in response.json()["detail"]


def test_quote_resilient_http_error_returns_502() -> None:
    class RawHttpProvider(_QuoteProvider):
        def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
            raise ResilientHttpError(
                source_name="yahoo",
                last_response=None,
                last_exception=ValueError("boom"),
                attempts=1,
            )

    response = _client(RawHttpProvider(_QUOTE)).get(
        "/quote", params={"symbol": "BTC-USD"}, headers=_renderer_auth()
    )
    assert response.status_code == 502


def test_quote_value_error_returns_422() -> None:
    class BadInputProvider(_QuoteProvider):
        def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
            raise ValueError("simulated bad input")

    response = _client(BadInputProvider(_QUOTE)).get(
        "/quote", params={"symbol": "BTC-USD"}, headers=_renderer_auth()
    )
    assert response.status_code == 422
    assert "simulated" in response.json()["detail"]


@pytest.fixture
def repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def test_quote_with_mcp_bearer_returns_401(repo: AnnotationsRepository) -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not authenticate the
    renderer /quote route (the agent uses the quote_for MCP tool instead)."""
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=_QuoteProvider(_QUOTE),
        annotations_repository=repo,
    )
    with TestClient(app) as client:
        response = client.get(
            "/quote",
            params={"symbol": "BTC-USD"},
            headers={"Authorization": f"Bearer {MCP_SECRET}"},
        )
    assert response.status_code == 401
