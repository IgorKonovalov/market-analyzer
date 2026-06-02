"""Plan 0012 phase 2 — `get_sentiment` source dispatch.

The Protocol method gained a `source: Literal["rss-vader", "stocktwits"]`
parameter (default `rss-vader`, preserving Plan 0010 callers). These tests pin
the dispatch: the default routes to the news-VADER path byte-identically, an
explicit `stocktwits` source hits only the StockTwits adapter, an unknown source
raises, and `as_of` is rejected for every source. Adapters are mocked so the
suite is offline; `_now` is frozen so the news path's `as_of` is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from market_analyser.data import default_provider
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.types import SentimentSample

_FROZEN = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)


def _stocktwits_sample() -> SentimentSample:
    return SentimentSample(
        symbol="AAPL",
        score=0.5,
        window="24h",
        as_of=_FROZEN,
        source="stocktwits",
        breakdown={"positive": 3, "negative": 1, "neutral": 2},
    )


def test_default_source_matches_explicit_rss_vader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(default_provider, "_now", lambda: _FROZEN)
    news = MagicMock()
    news.fetch.return_value = []  # no items -> deterministic zero sample
    provider = DefaultMarketDataProvider(news=news)

    default_call = provider.get_sentiment(symbol="BTC", window="24h")
    explicit_call = provider.get_sentiment(symbol="BTC", window="24h", source="rss-vader")

    assert default_call.model_dump() == explicit_call.model_dump()  # byte-identical
    assert default_call.source == "rss-vader"


def test_stocktwits_source_hits_only_the_adapter() -> None:
    news = MagicMock()
    stocktwits = MagicMock()
    stocktwits.fetch_sentiment.return_value = _stocktwits_sample()
    provider = DefaultMarketDataProvider(news=news, stocktwits=stocktwits)

    sample = provider.get_sentiment(symbol="AAPL", window="24h", source="stocktwits")

    assert sample.source == "stocktwits"
    stocktwits.fetch_sentiment.assert_called_once_with(symbol="AAPL", window="24h")
    news.fetch.assert_not_called()


def test_rss_vader_source_does_not_touch_stocktwits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(default_provider, "_now", lambda: _FROZEN)
    news = MagicMock()
    news.fetch.return_value = []
    stocktwits = MagicMock()
    provider = DefaultMarketDataProvider(news=news, stocktwits=stocktwits)

    provider.get_sentiment(symbol="BTC", window="24h", source="rss-vader")

    stocktwits.fetch_sentiment.assert_not_called()
    news.fetch.assert_called_once()


def test_unknown_source_raises_value_error() -> None:
    provider = DefaultMarketDataProvider()

    with pytest.raises(ValueError, match="unknown sentiment source"):
        provider.get_sentiment(symbol="AAPL", window="24h", source="bogus")  # type: ignore[arg-type]


def test_registry_entry_dispatches_a_fake_source() -> None:
    """A new sentiment source is a one-entry registry add, not a dispatch-body
    edit (ADR-0031): inserting a fake `SentimentSource` makes `get_sentiment`
    route to it without any change to the dispatch method."""
    fake_sample = SentimentSample(
        symbol="AAPL",
        score=0.25,
        window="24h",
        as_of=_FROZEN,
        source="fake",
        breakdown={"positive": 2, "negative": 1, "neutral": 0},
    )
    fake = MagicMock()
    fake.fetch_sentiment.return_value = fake_sample
    provider = DefaultMarketDataProvider()
    provider._sentiment_sources["fake"] = fake  # the entire cost of adding a source

    result = provider.get_sentiment(symbol="AAPL", window="24h", source="fake")  # type: ignore[arg-type]

    assert result.source == "fake"
    fake.fetch_sentiment.assert_called_once_with(symbol="AAPL", window="24h")


@pytest.mark.parametrize("source", ["rss-vader", "stocktwits"])
def test_as_of_rejected_for_every_source(source: str) -> None:
    provider = DefaultMarketDataProvider()

    with pytest.raises(ValueError, match="as_of"):
        provider.get_sentiment(
            symbol="AAPL",
            window="24h",
            source=source,  # type: ignore[arg-type]
            as_of=_FROZEN,
        )
