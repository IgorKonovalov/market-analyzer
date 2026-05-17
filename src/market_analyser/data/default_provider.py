"""`DefaultMarketDataProvider` — the production implementation of the Protocol.

Phase 2 wires `get_ohlcv` to the Yahoo adapter; every other Protocol method is
a `NotImplementedError` stub naming the plan that lands it. The protocol-
introspection test asserts each method is reachable so a forgotten stub fails
loudly at test time rather than silently in production.

Phase 3 will replace this class (or wrap it) with cache-aware dispatch via the
SQLite bar repository — see Plan 0001 phase 3.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.types import (
    Bar,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)


class DefaultMarketDataProvider:
    """Dispatches across per-source adapters. See ADR-0007."""

    def __init__(self, yahoo: YahooAdapter | None = None) -> None:
        self._yahoo = yahoo if yahoo is not None else YahooAdapter()

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        # Cache + as_of semantics land in phase 3; for phase 2 we ignore as_of
        # and fetch live every call.
        return self._yahoo.fetch_ohlcv(symbol, timeframe, start, end)

    def get_quote(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> Quote:
        raise NotImplementedError(
            "get_quote is not implemented in Plan 0001 — see plan 0001 followups",
        )

    def search_symbols(
        self,
        query: str,
        as_of: datetime | None = None,
    ) -> Sequence[SymbolInfo]:
        raise NotImplementedError(
            "search_symbols is not implemented in Plan 0001 — see plan 0001 followups",
        )

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError(
            "get_screener is not implemented in Plan 0001 — see plan 0001 followups",
        )

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        as_of: datetime | None = None,
    ) -> SentimentSample:
        raise NotImplementedError(
            "get_sentiment is not implemented in Plan 0001 — see plan 0001 followups",
        )

    def get_news(
        self,
        symbol: str,
        window: str,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError(
            "get_news is not implemented in Plan 0001 — see plan 0001 followups",
        )
