"""`DefaultMarketDataProvider` — the production implementation of the Protocol.

Phase 3 wires the cache: `get_ohlcv` reads from the `BarRepository` first and
only calls the Yahoo adapter when the cache is empty for the requested range.
With `as_of` set (backtest mode), the cache is the sole source of truth and a
missing range is an error — never a silent remote fetch. This is the anti-
lookahead seam declared in ADR-0007.
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
from market_analyser.persistence.repository import BarRepository


class DefaultMarketDataProvider:
    """Dispatches across per-source adapters with optional cache. See ADR-0007."""

    def __init__(
        self,
        *,
        yahoo: YahooAdapter | None = None,
        bar_repository: BarRepository | None = None,
    ) -> None:
        self._yahoo = yahoo if yahoo is not None else YahooAdapter()
        self._repo = bar_repository

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        # No cache wired: live-only (phase-2 fallback for tests that don't need persistence).
        if self._repo is None:
            if as_of is not None:
                raise ValueError(
                    "as_of requires a configured BarRepository — no remote fetch when as_of is set",
                )
            return self._yahoo.fetch_ohlcv(symbol, timeframe, start, end)

        if as_of is not None:
            cached = self._repo.get_bars(symbol, timeframe, start, end, as_of=as_of)
            if not cached:
                raise ValueError(
                    f"as_of={as_of.isoformat()}: no cached bars in [{start.isoformat()}, "
                    f"{end.isoformat()}] — refusing remote fetch (anti-lookahead)",
                )
            return cached

        cached = self._repo.get_bars(symbol, timeframe, start, end)
        if cached:
            return cached
        fetched = self._yahoo.fetch_ohlcv(symbol, timeframe, start, end)
        if fetched:
            self._repo.upsert_bars(fetched)
        return self._repo.get_bars(symbol, timeframe, start, end)

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
