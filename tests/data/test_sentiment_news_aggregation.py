"""Plan 0010 phase 2 — provider sentiment aggregation over news items.

`DefaultMarketDataProvider.get_sentiment` fetches the symbol's news with VADER
scoring on and aggregates: mean compound score + a positive/negative/neutral
breakdown. Driven offline through the same fixtures and frozen clock as the news
adapter tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import rss_news
from market_analyser.data.adapters.rss_news import _FEED_CATALOG, RssNewsAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURES = Path(__file__).parent / "fixtures"
_FROZEN_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)

_EMPTY_RSS = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<rss version="2.0"><channel><title>empty</title></channel></rss>'
)

# Hand-computed over the fixtures: the two BTC-token items inside the 24h window
# are the ATH item (compound 0.9274) and the BTC-ETF item (compound -0.128);
# their mean is the expected aggregate score. VADER scores both pinned at
# fixture-creation time against vaderSentiment==3.3.2.
_ATH_COMPOUND = 0.9274
_BTC_ETF_COMPOUND = -0.128
_EXPECTED_BTC_MEAN = (_ATH_COMPOUND + _BTC_ETF_COMPOUND) / 2  # == 0.3997


def _provider(monkeypatch: pytest.MonkeyPatch) -> DefaultMarketDataProvider:
    monkeypatch.setattr(rss_news, "_now", lambda: _FROZEN_NOW)
    bodies = {
        _FEED_CATALOG["coindesk"].url: (_FIXTURES / "rss_news_coindesk.xml").read_bytes(),
        _FEED_CATALOG["cointelegraph"].url: _EMPTY_RSS,
        _FEED_CATALOG["yahoo_finance"].url: (_FIXTURES / "rss_news_yahoo.xml").read_bytes(),
        _FEED_CATALOG["marketwatch"].url: _EMPTY_RSS,
        _FEED_CATALOG["cnbc"].url: (_FIXTURES / "rss_news_cnbc.xml").read_bytes(),
    }
    client = ResilientHttpClient(source_name="rss-test", max_retries=0)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=200, headers={}, body=bodies[url], elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return DefaultMarketDataProvider(news=RssNewsAdapter(http_client=client))


def test_get_sentiment_aggregates_btc_items(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch)

    sample = provider.get_sentiment(symbol="BTC", window="24h")

    assert sample.symbol == "BTC"
    assert sample.window == "24h"
    assert sample.source == "rss-vader"
    assert sample.score == pytest.approx(_EXPECTED_BTC_MEAN, abs=1e-9)
    # ATH is positive (> 0.05), BTC-ETF is negative (< -0.05): one of each.
    assert sample.breakdown == {"positive": 1, "negative": 1, "neutral": 0}


def test_get_sentiment_zero_news_is_defined(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch)

    sample = provider.get_sentiment(symbol="XYZ_NEVER_IN_NEWS", window="24h")

    assert sample.score == 0.0  # no news = zero (neutral), not unknown
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


def test_get_sentiment_rejects_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch)

    with pytest.raises(ValueError, match="as_of"):
        provider.get_sentiment(symbol="BTC", window="24h", as_of=datetime(2026, 1, 1, tzinfo=UTC))
