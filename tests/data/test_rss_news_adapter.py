"""Plan 0010 phase 1 — offline tests for the RSS news adapter.

Three captured feed fixtures (CoinDesk / Yahoo / CNBC) drive the adapter through
a `ResilientHttpClient` whose transport seam (`_perform_request`) is monkeypatched
per-URL, so the suite never touches the network. Wall-clock time is frozen via the
module-level `_now` seam. The two catalog feeds without a dedicated fixture
(cointelegraph, marketwatch) return an empty-but-valid RSS document, so the
cross-feed counts are determined solely by the three captured fixtures.

Fixture timeline, relative to the frozen now (2026-05-20 12:00 UTC):
    coindesk:  ATH (30 min) · crash (90 min) · weekly roundup (>1 week, out of 24h)
    yahoo:     "Together they invest" (3 h) · Apple (5 h)
    cnbc:      stocks rally (4 h) · "BTC ETF" (6 h)
So six items fall inside 24h; one inside 1h; two mention BTC as a token.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser import __version__
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

_ATH_TITLE = "Bitcoin surges to a new all-time high"
_BTC_ETF_TITLE = "BTC ETF sees record inflows"
_DECOY_TITLE = "Together they invest in private markets"
_CRASH_TITLE = "Crypto market crashes amid regulatory fears"

# Phase 2 sentiment thresholds for directional-correctness assertions (explicit
# constants, not magic numbers in the test body).
_POSITIVE_BAR = 0.3
_NEGATIVE_BAR = -0.3


def _bodies() -> dict[str, bytes]:
    return {
        _FEED_CATALOG["coindesk"].url: (_FIXTURES / "rss_news_coindesk.xml").read_bytes(),
        _FEED_CATALOG["cointelegraph"].url: _EMPTY_RSS,
        _FEED_CATALOG["yahoo_finance"].url: (_FIXTURES / "rss_news_yahoo.xml").read_bytes(),
        _FEED_CATALOG["marketwatch"].url: _EMPTY_RSS,
        _FEED_CATALOG["cnbc"].url: (_FIXTURES / "rss_news_cnbc.xml").read_bytes(),
    }


def _make_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_urls: frozenset[str] = frozenset(),
) -> tuple[RssNewsAdapter, list[tuple[str, dict[str, str]]]]:
    """Return an adapter wired to the fixture bytes plus a per-call request log of
    `(url, per-call-headers)`. `max_retries=0` so a failing feed raises at once
    (no backoff sleeps)."""
    monkeypatch.setattr(rss_news, "_now", lambda: _FROZEN_NOW)
    bodies = _bodies()
    request_log: list[tuple[str, dict[str, str]]] = []
    client = ResilientHttpClient(source_name="rss-test", max_retries=0)

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        request_log.append((url, dict(headers or {})))
        if url in fail_urls:
            raise ConnectionError("simulated feed outage")
        return HttpResponse(status_code=200, headers={}, body=bodies[url], elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return RssNewsAdapter(http_client=client), request_log


def test_fetch_parses_all_feeds_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol=None, window="24h")

    assert len(items) == 6  # six within 24h; the weekly roundup (>1 week) is excluded
    for item in items:
        assert item.title
        assert item.url
        assert item.source
        assert item.published_at.tzinfo is not None
        assert item.symbol == ""  # no-filter sentinel


def test_symbol_filter_keeps_token_matches_and_tags_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol="BTC", window="24h")

    titles = {item.title for item in items}
    assert _ATH_TITLE in titles  # "BTC" appears in the summary
    assert _BTC_ETF_TITLE in titles  # "BTC" appears in the title
    assert _DECOY_TITLE not in titles  # no BTC token
    assert len(items) == 2
    assert all(item.symbol == "BTC" for item in items)  # carries the applied filter


def test_window_filter_one_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol=None, window="1h")

    assert len(items) == 1  # only the 30-min ATH item; 90-min and 5-h are excluded
    assert items[0].title == _ATH_TITLE


def test_quirk_feeds_send_explicit_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, request_log = _make_adapter(monkeypatch)

    adapter.fetch(symbol=None, window="24h")

    headers_by_url = dict(request_log)
    expected_ua = f"market-analyser/{__version__}"
    # needs_ua feeds carry an explicit per-call User-Agent...
    assert headers_by_url[_FEED_CATALOG["yahoo_finance"].url].get("User-Agent") == expected_ua
    assert headers_by_url[_FEED_CATALOG["cnbc"].url].get("User-Agent") == expected_ua
    # ...feeds without the quirk do not add one (the client default applies at the transport).
    assert "User-Agent" not in headers_by_url[_FEED_CATALOG["coindesk"].url]
    assert "User-Agent" not in headers_by_url[_FEED_CATALOG["marketwatch"].url]


def test_one_feed_down_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    coindesk_url = _FEED_CATALOG["coindesk"].url
    adapter, _ = _make_adapter(monkeypatch, fail_urls=frozenset({coindesk_url}))

    with caplog.at_level(logging.WARNING):
        items = adapter.fetch(symbol=None, window="24h")

    # CoinDesk's items are absent; the four healthy feeds still return, no raise.
    assert all(item.source != "coindesk" for item in items)
    assert len(items) == 4  # yahoo (2) + cnbc (2); cointelegraph/marketwatch are empty
    assert any("coindesk" in record.getMessage() for record in caplog.records)


def test_sorted_by_published_descending(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol=None, window="24h")

    published = [item.published_at for item in items]
    assert published == sorted(published, reverse=True)


def test_provider_get_news_matches_direct_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)
    provider = DefaultMarketDataProvider(news=adapter)

    via_provider = provider.get_news(symbol="BTC", window="24h")
    via_adapter = adapter.fetch(symbol="BTC", window="24h")

    assert [item.model_dump() for item in via_provider] == [
        item.model_dump() for item in via_adapter
    ]


def test_provider_get_news_rejects_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)
    provider = DefaultMarketDataProvider(news=adapter)

    with pytest.raises(ValueError, match="as_of"):
        provider.get_news(symbol="BTC", window="24h", as_of=datetime(2026, 1, 1, tzinfo=UTC))


def test_with_sentiment_populates_compound(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol=None, window="24h", with_sentiment=True)

    assert all(item.compound_sentiment is not None for item in items)
    assert all(
        item.compound_sentiment is not None and -1.0 <= item.compound_sentiment <= 1.0
        for item in items
    )
    by_title = {item.title: item.compound_sentiment for item in items}
    ath, crash = by_title[_ATH_TITLE], by_title[_CRASH_TITLE]
    assert ath is not None and ath > _POSITIVE_BAR
    assert crash is not None and crash < _NEGATIVE_BAR


def test_without_sentiment_leaves_compound_none(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _make_adapter(monkeypatch)

    items = adapter.fetch(symbol=None, window="24h")

    assert all(item.compound_sentiment is None for item in items)
