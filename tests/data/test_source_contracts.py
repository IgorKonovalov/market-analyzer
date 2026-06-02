"""Plan 0028 phase 1 done-when: every concrete adapter satisfies its
per-capability source Protocol (ADR-0031).

`@runtime_checkable` `isinstance` only checks method *presence*, so each case
pairs it with an `inspect.signature` comparison that pins the method *shape* —
the same "validate beyond getattr" discipline `contracts.strategy.discover()`
applies to strategy modules. The suite fails if an adapter drops or renames a
contract method (isinstance) or changes its parameter list (signature).

Adapters are constructed with defaults; that wires a real HTTP client but makes
no network call, so this module needs no `network` marker.
"""

from __future__ import annotations

import inspect

import pytest

from market_analyser.data.adapters.crypto_fear_greed import CryptoFearGreedAdapter
from market_analyser.data.adapters.rss_news import RssNewsAdapter
from market_analyser.data.adapters.stocktwits import StockTwitsAdapter
from market_analyser.data.adapters.tradingview_screener import TradingViewScreenerAdapter
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.adapters.yahoo_quote import YahooQuoteAdapter
from market_analyser.data.sources import (
    MarketSentimentSource,
    NewsSource,
    OhlcvSource,
    QuoteSource,
    ScreenerSource,
    SentimentSource,
    SymbolSearchSource,
)

# (label, adapter, capability Protocol, the method the Protocol contracts).
# YahooAdapter appears twice — it carries two capabilities.
_CONTRACTS = [
    ("yahoo/ohlcv", YahooAdapter(), OhlcvSource, "fetch_ohlcv"),
    ("yahoo/search", YahooAdapter(), SymbolSearchSource, "search"),
    ("yahoo_quote", YahooQuoteAdapter(), QuoteSource, "get_quote"),
    ("screener", TradingViewScreenerAdapter(), ScreenerSource, "query"),
    ("rss_news", RssNewsAdapter(), NewsSource, "fetch"),
    ("stocktwits", StockTwitsAdapter(), SentimentSource, "fetch_sentiment"),
    ("crypto_fng", CryptoFearGreedAdapter(), MarketSentimentSource, "fetch_current"),
]


@pytest.mark.parametrize(
    ("label", "adapter", "protocol", "method"),
    _CONTRACTS,
    ids=[c[0] for c in _CONTRACTS],
)
def test_adapter_satisfies_capability(
    label: str, adapter: object, protocol: type, method: str
) -> None:
    # Presence: @runtime_checkable confirms the contract method exists.
    assert isinstance(adapter, protocol)
    # Shape: the adapter's method signature matches the Protocol's, so a renamed
    # parameter or dropped argument is caught too (isinstance alone would not).
    expected = list(inspect.signature(getattr(protocol, method)).parameters)
    actual = list(inspect.signature(getattr(type(adapter), method)).parameters)
    assert actual == expected, f"{label}: {method} signature drifted from {protocol.__name__}"


def test_runtime_checkable_detects_a_dropped_method() -> None:
    """Guard the guard: a type missing the contract method must fail isinstance,
    proving the per-adapter assertions above would catch a real drop."""

    class NotAnOhlcvSource:
        pass

    assert not isinstance(NotAnOhlcvSource(), OhlcvSource)
    assert isinstance(YahooAdapter(), OhlcvSource)
