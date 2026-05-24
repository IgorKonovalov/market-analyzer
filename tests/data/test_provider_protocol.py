"""Plan 0001 phase 2 done-when: the MarketDataProvider Protocol surface.

These tests are the "forgotten stub" tripwire from ADR-0007: every Protocol
method is reachable, `get_ohlcv` works in phase 2, and the still-unimplemented
ones raise `NotImplementedError` with a message identifying Plan 0001 as the
home plan. `get_screener` graduated out of this list in Plan 0009 (positive
coverage lives in `test_tradingview_screener_adapter.py`); `get_news` graduated
in Plan 0010 (positive coverage lives in `test_rss_news_adapter.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

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


@pytest.mark.parametrize(
    "method_name,args",
    [
        ("get_quote", ("AAPL",)),
        ("search_symbols", ("apple",)),
        ("get_sentiment", ("AAPL", "1d")),
    ],
)
def test_unimplemented_methods_are_reachable_and_raise(
    method_name: str, args: tuple[Any, ...]
) -> None:
    provider = DefaultMarketDataProvider(yahoo=_fake_yahoo())
    method = getattr(provider, method_name)
    with pytest.raises(NotImplementedError, match="plan 0001"):
        method(*args)
