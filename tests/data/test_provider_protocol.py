"""Plan 0001 phase 2 done-when: the MarketDataProvider Protocol surface.

These tests are the "forgotten stub" tripwire from ADR-0007: every Protocol
method is reachable and `get_ohlcv` works in phase 2. The tripwire has now fully
discharged — every method has graduated to its own positive-coverage suite:
`get_screener` in Plan 0009 (`test_tradingview_screener_adapter.py`), `get_news`
and `get_sentiment` in Plan 0010 (`test_rss_news_adapter.py`,
`test_sentiment_news_aggregation.py`), `search_symbols` in Plan 0024
(`test_yahoo_search_adapter.py`), and `get_quote` in Plan 0019
(`test_yahoo_quote_adapter.py`). No `NotImplementedError` stub remains, so this
module now only pins protocol conformance and the `get_ohlcv` happy path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar


def _fake_yahoo() -> YahooAdapter:
    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        return [
            {
                "date": "2026-04-15",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
            },
        ]

    return YahooAdapter(fetcher=fetcher)


def test_default_provider_satisfies_protocol() -> None:
    provider = DefaultMarketDataProvider(yahoo=_fake_yahoo())
    assert isinstance(provider, MarketDataProvider)


def test_get_ohlcv_is_implemented() -> None:
    provider = DefaultMarketDataProvider(yahoo=_fake_yahoo())
    bars: Sequence[Bar] = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
