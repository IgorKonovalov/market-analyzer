"""Plan 0001 phase 2 done-when: GET /ohlcv returns Bars via the injected provider.

Uses a `FakeMarketDataProvider` so the test is hermetic; the network smoke test
lives under tests/network/.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)

SECRET = "test-secret"


class FakeMarketDataProvider:
    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = bars
        self.calls: list[dict[str, object]] = []

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "start": start, "end": end},
        )
        return self.bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
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
        self, symbol: str, window: str, as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
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


def _sample_bar() -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


@pytest.fixture
def provider() -> FakeMarketDataProvider:
    return FakeMarketDataProvider(bars=[_sample_bar()])


@pytest.fixture
def client(provider: FakeMarketDataProvider) -> TestClient:
    return TestClient(create_app(secret=SECRET, provider=provider))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {SECRET}"}


def test_ohlcv_returns_bars_with_auth(client: TestClient, provider: FakeMarketDataProvider) -> None:
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["symbol"] == "AAPL"
    assert payload[0]["source"] == "yahoo"
    assert provider.calls[0]["symbol"] == "AAPL"
    assert provider.calls[0]["timeframe"] == "1d"


def test_ohlcv_returns_401_without_auth(client: TestClient) -> None:
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
    )
    assert response.status_code == 401


def test_ohlcv_value_error_returns_422(client: TestClient) -> None:
    class FailingProvider(FakeMarketDataProvider):
        def get_ohlcv(
            self,
            symbol: str,
            timeframe: str,
            start: datetime,
            end: datetime,
            as_of: datetime | None = None,
        ) -> Sequence[Bar]:
            raise ValueError("simulated provider validation error")

    failing = FailingProvider(bars=[])
    client = TestClient(create_app(secret=SECRET, provider=failing))
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers=_auth(),
    )
    assert response.status_code == 422
    assert "simulated" in response.json()["detail"]
