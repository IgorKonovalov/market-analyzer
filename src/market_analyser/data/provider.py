"""`MarketDataProvider` Protocol — the only data-layer contract downstream code imports.

Per ADR-0007, every method takes an `as_of: datetime | None` argument. Live-mode
callers pass `None`; backtest callers always pass a fixed datetime. The data-layer
implementation must never reach for "future" data when `as_of` is set — that is
the anti-lookahead seam declared at this level.

Method-by-method readiness for Plan 0001:
    get_ohlcv          implemented in phase 2 (this phase).
    get_quote          stubbed — earned by a later plan.
    search_symbols     stubbed — earned by a later plan.
    get_screener       implemented in Plan 0009 (TradingView screener adapter).
    get_sentiment      implemented in Plan 0010 (news-derived VADER sentiment);
                       Plan 0012 adds a `source` selector (rss-vader | stocktwits).
    get_news           implemented in Plan 0010 (RSS news adapter).
    get_market_sentiment  implemented in Plan 0011 (crypto Fear & Greed).
    get_macro_context  implemented in Plan 0022 (CoinGecko crypto macro pulse).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

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


@runtime_checkable
class MarketDataProvider(Protocol):
    """Stable downstream contract for market data. See ADR-0007."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]: ...

    def get_quote(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> Quote: ...

    def search_symbols(
        self,
        query: str,
        as_of: datetime | None = None,
    ) -> Sequence[SymbolInfo]: ...

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]: ...

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: Literal["rss-vader", "stocktwits", "reddit", "x"] = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample: ...

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]: ...

    def get_market_sentiment(
        self,
        market: Literal["crypto"],
        window: str = "current",
        as_of: datetime | None = None,
    ) -> MarketSentimentSample: ...

    def get_macro_context(
        self,
        market: Literal["crypto"] = "crypto",
        as_of: datetime | None = None,
    ) -> MacroContext: ...
