"""Plan 0024 phase 2 done-when: GET /search returns SymbolInfo via the provider.

Hermetic — a fake provider supplies results; the live Yahoo path is covered by
the adapter tests. Covers: 200 + BTC-USD with the renderer bearer, empty query →
200 [], cross-tenant MCP bearer → 401, upstream failure → 502, ValueError → 422.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import UpstreamUnavailableError
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

_RESULTS = [
    SymbolInfo(symbol="BTC-USD", name="Bitcoin USD", exchange="CCC", quote_type="Cryptocurrency"),
    SymbolInfo(symbol="BTC=F", name="Bitcoin Futures", exchange="CME", quote_type="Futures"),
]


class _SearchProvider:
    """Provider stub whose only live method is search_symbols."""

    def __init__(self, results: Sequence[SymbolInfo]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        self.calls.append(query)
        return self._results

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise NotImplementedError

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
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


def _client(provider: _SearchProvider) -> TestClient:
    return TestClient(create_app(secret=RENDERER_SECRET, provider=provider))


def test_search_returns_results_with_auth() -> None:
    provider = _SearchProvider(_RESULTS)
    response = _client(provider).get("/search", params={"q": "BTC"}, headers=_renderer_auth())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list)
    symbols = [row["symbol"] for row in payload]
    assert "BTC-USD" in symbols
    assert payload[0]["quote_type"] == "Cryptocurrency"
    assert provider.calls == ["BTC"]


def test_search_empty_query_returns_empty_list_without_calling_provider() -> None:
    provider = _SearchProvider(_RESULTS)
    response = _client(provider).get("/search", params={"q": "   "}, headers=_renderer_auth())

    assert response.status_code == 200
    assert response.json() == []
    assert provider.calls == []  # blank query short-circuits before the provider


def test_search_returns_401_without_auth() -> None:
    response = _client(_SearchProvider(_RESULTS)).get("/search", params={"q": "BTC"})
    assert response.status_code == 401


def test_search_upstream_error_returns_502() -> None:
    class UpstreamDownProvider(_SearchProvider):
        def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
            raise UpstreamUnavailableError("yahoo: upstream unavailable on symbol search")

    response = _client(UpstreamDownProvider([])).get(
        "/search", params={"q": "BTC"}, headers=_renderer_auth()
    )
    assert response.status_code == 502
    assert "yahoo" in response.json()["detail"]


def test_search_resilient_http_error_returns_502() -> None:
    class RawHttpProvider(_SearchProvider):
        def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
            raise ResilientHttpError(
                source_name="yahoo",
                last_response=None,
                last_exception=ValueError("boom"),
                attempts=1,
            )

    response = _client(RawHttpProvider([])).get(
        "/search", params={"q": "BTC"}, headers=_renderer_auth()
    )
    assert response.status_code == 502


def test_search_value_error_returns_422() -> None:
    class BadInputProvider(_SearchProvider):
        def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
            raise ValueError("simulated bad input")

    response = _client(BadInputProvider([])).get(
        "/search", params={"q": "BTC"}, headers=_renderer_auth()
    )
    assert response.status_code == 422
    assert "simulated" in response.json()["detail"]


@pytest.fixture
def repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def test_search_with_mcp_bearer_returns_401(repo: AnnotationsRepository) -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not authenticate the
    renderer /search route (matches the guard on /ohlcv and /annotations)."""
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=_SearchProvider(_RESULTS),
        annotations_repository=repo,
    )
    with TestClient(app) as client:
        response = client.get(
            "/search",
            params={"q": "BTC"},
            headers={"Authorization": f"Bearer {MCP_SECRET}"},
        )
    assert response.status_code == 401
