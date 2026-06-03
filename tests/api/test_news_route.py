"""Plan 0023 phase 1 done-when: GET /news is a renderer-bearer-gated read route.

Covers:
- happy path with a symbol round-trips both `get_news` and `get_sentiment`
- no-symbol browse returns items with `sentiment == null` (no aggregate)
- boundary validation: bad window / out-of-range limit → 422
- cross-tenant isolation: MCP bearer and no-bearer → 401; renderer bearer → 200
- feed outage (`get_news() == []`) degrades to 200 + zero-tone, never 500
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
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


class _FakeProvider:
    """Returns canned news/sentiment and records the args the route passed.

    Only `get_news`/`get_sentiment` are exercised by `GET /news`; the remaining
    Protocol methods assert if reached so a regression that calls them is loud.
    """

    def __init__(
        self,
        news: Sequence[NewsItem],
        sentiment: SentimentSample,
    ) -> None:
        self._news = list(news)
        self._sentiment = sentiment
        self.news_calls: list[dict[str, Any]] = []
        self.sentiment_calls: list[dict[str, Any]] = []

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        self.news_calls.append(
            {
                "symbol": symbol,
                "window": window,
                "limit": limit,
                "with_sentiment": with_sentiment,
            },
        )
        return self._news

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: Literal["rss-vader", "stocktwits"] = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
        self.sentiment_calls.append({"symbol": symbol, "window": window})
        return self._sentiment

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("news route must not call get_ohlcv")

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise AssertionError("news route must not call get_quote")

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise AssertionError("news route must not call search_symbols")

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise AssertionError("news route must not call get_screener")

    def get_market_sentiment(
        self,
        market: Literal["crypto"],
        window: str = "current",
        as_of: datetime | None = None,
    ) -> MarketSentimentSample:
        raise AssertionError("news route must not call get_market_sentiment")

    def get_macro_context(
        self,
        market: Literal["crypto"] = "crypto",
        as_of: datetime | None = None,
    ) -> MacroContext:
        raise AssertionError("news route must not call get_macro_context")


def _news_rows() -> list[NewsItem]:
    return [
        NewsItem(
            symbol="BTC",
            title="Bitcoin surges to a new all-time high",
            url="https://www.coindesk.com/markets/btc-ath",
            published_at=datetime(2026, 5, 20, 11, 30, tzinfo=UTC),
            source="coindesk",
            summary="A strong rally.",
            compound_sentiment=0.9274,
        ),
        NewsItem(
            symbol="BTC",
            title="Regulators weigh new crypto rules",
            url="https://www.reuters.com/crypto/rules",
            published_at=datetime(2026, 5, 20, 9, 15, tzinfo=UTC),
            source="reuters",
            summary="Mixed signals.",
            compound_sentiment=-0.4019,
        ),
    ]


def _sentiment(symbol: str = "BTC") -> SentimentSample:
    return SentimentSample(
        symbol=symbol,
        score=0.2628,
        window="24h",
        as_of=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        source="rss-vader",
        breakdown={"positive": 1, "negative": 1, "neutral": 0},
    )


def _zero_sentiment(symbol: str) -> SentimentSample:
    return SentimentSample(
        symbol=symbol,
        score=0.0,
        window="24h",
        as_of=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        source="rss-vader",
        breakdown={"positive": 0, "negative": 0, "neutral": 0},
    )


@pytest.fixture
def repo() -> Iterator[AnnotationsRepository]:
    # Setting `mcp_secret` requires an annotations repo (create_app contract);
    # the /news route never touches it, but it makes the MCP bearer in the
    # cross-tenant test a *genuinely valid* MCP secret that still can't reach /news.
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


def _client(provider: _FakeProvider, repo: AnnotationsRepository) -> TestClient:
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=provider,
        annotations_repository=repo,
    )
    return TestClient(app)


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def test_happy_path_with_symbol_round_trips_news_and_sentiment(
    repo: AnnotationsRepository,
) -> None:
    news = _news_rows()
    sentiment = _sentiment()
    provider = _FakeProvider(news, sentiment)
    client = _client(provider, repo)

    response = client.get(
        "/news", params={"symbol": "BTC", "window": "24h"}, headers=_renderer_auth()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == [item.model_dump(mode="json") for item in news]
    assert body["sentiment"] == sentiment.model_dump(mode="json")
    assert isinstance(body["queried_at"], str) and body["queried_at"]
    # The route asks the provider for sentiment-scored news for the right symbol.
    assert provider.news_calls == [
        {"symbol": "BTC", "window": "24h", "limit": 50, "with_sentiment": True}
    ]
    assert provider.sentiment_calls == [{"symbol": "BTC", "window": "24h"}]


def test_no_symbol_browse_returns_null_sentiment(repo: AnnotationsRepository) -> None:
    news = _news_rows()
    provider = _FakeProvider(news, _sentiment())
    client = _client(provider, repo)

    response = client.get("/news", params={"window": "24h"}, headers=_renderer_auth())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == [item.model_dump(mode="json") for item in news]
    assert body["sentiment"] is None
    # No symbol → no aggregate call at all.
    assert provider.sentiment_calls == []


@pytest.mark.parametrize(
    ("params", "label"),
    [
        ({"window": "12h"}, "unsupported window"),
        ({"limit": "0"}, "limit below floor"),
        ({"limit": "101"}, "limit above ceiling"),
    ],
)
def test_boundary_validation_returns_422(
    repo: AnnotationsRepository, params: dict[str, str], label: str
) -> None:
    provider = _FakeProvider(_news_rows(), _sentiment())
    client = _client(provider, repo)

    response = client.get("/news", params=params, headers=_renderer_auth())

    assert response.status_code == 422, f"{label}: {response.text}"


def test_news_without_bearer_returns_401(repo: AnnotationsRepository) -> None:
    provider = _FakeProvider(_news_rows(), _sentiment())
    client = _client(provider, repo)
    assert client.get("/news", params={"symbol": "BTC"}).status_code == 401


def test_news_with_mcp_bearer_returns_401(repo: AnnotationsRepository) -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not authenticate /news."""
    provider = _FakeProvider(_news_rows(), _sentiment())
    client = _client(provider, repo)
    response = client.get(
        "/news",
        params={"symbol": "BTC"},
        headers={"Authorization": f"Bearer {MCP_SECRET}"},
    )
    assert response.status_code == 401


def test_feed_outage_degrades_to_200_with_zero_tone(repo: AnnotationsRepository) -> None:
    """All feeds empty: items == [] and an all-zero-breakdown sentiment, never a 500."""
    provider = _FakeProvider([], _zero_sentiment("XYZ"))
    client = _client(provider, repo)

    response = client.get("/news", params={"symbol": "XYZ"}, headers=_renderer_auth())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["sentiment"]["score"] == 0.0
    assert body["sentiment"]["breakdown"] == {"positive": 0, "negative": 0, "neutral": 0}
