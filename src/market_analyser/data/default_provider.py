"""`DefaultMarketDataProvider` — the production implementation of the Protocol.

Phase 3 wires the cache: `get_ohlcv` reads from the `BarRepository`, computes
the gaps between the cached coverage and the requested `[start, end]` window,
and asks the adapter only for those gaps before returning the merged result.

With `as_of` set (backtest mode), the cache is the sole source of truth: any
gap in coverage is a hard error, never a silent remote fetch. This is the
anti-lookahead seam declared in ADR-0007.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from market_analyser.data.adapters.tradingview_screener import TradingViewScreenerAdapter
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

# A gap below this width is treated as "not a real hole" (weekends, holidays,
# overnight sessions) and skipped. Edge gaps below this width are still fetched
# but widened to at least this span so each fetch justifies a round-trip — Yahoo
# picks the smallest period that fits the request anyway, so a 1-day window
# would over-fetch and discard 30 days of bars. Plan 0004 phase 1 baseline.
_MIN_FETCH_SPAN: timedelta = timedelta(days=10)


class DefaultMarketDataProvider:
    """Dispatches across per-source adapters with optional cache. See ADR-0007."""

    def __init__(
        self,
        *,
        yahoo: YahooAdapter | None = None,
        screener: TradingViewScreenerAdapter | None = None,
        bar_repository: BarRepository | None = None,
    ) -> None:
        self._yahoo = yahoo if yahoo is not None else YahooAdapter()
        self._screener = screener if screener is not None else TradingViewScreenerAdapter()
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
            gaps = _coverage_gaps(cached, start, end)
            if gaps:
                raise ValueError(
                    f"as_of={as_of.isoformat()}: cached coverage incomplete for "
                    f"[{start.isoformat()}, {end.isoformat()}] — "
                    f"refusing remote fetch (anti-lookahead)",
                )
            return cached

        cached = self._repo.get_bars(symbol, timeframe, start, end)
        gaps = _coverage_gaps(cached, start, end)
        if not gaps:
            return cached
        merged: dict[datetime, Bar] = {bar.event_ts: bar for bar in cached}
        for gap_start, gap_end in gaps:
            fetched = self._yahoo.fetch_ohlcv(symbol, timeframe, gap_start, gap_end)
            if not fetched:
                continue
            self._repo.upsert_bars(fetched)
            for bar in fetched:
                if start <= bar.event_ts <= end:
                    merged[bar.event_ts] = bar
        return sorted(merged.values(), key=lambda b: b.event_ts)

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
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        # Screener results are wall-clock-sensitive: "RSI < 30 right now" is not
        # the same query five minutes ago. There is no cached/replayable source
        # to honour `as_of` against, so reject it at the boundary (Plan 0009 /
        # ADR-0019). A backtest-replay screener is a future plan with its own
        # snapshot table.
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for screener queries — results are "
                "wall-clock-sensitive (Plan 0009 / ADR-0019)",
            )
        return self._screener.query(filters, market=market, exchange=exchange, limit=limit)

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


def _coverage_gaps(
    cached: Sequence[Bar],
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return the fetch windows needed to combine with `cached` to cover [start, end].

    Head and tail gaps (uncached space at the edges of the requested window) are
    always returned. Gaps between consecutive cached bars are returned only when
    they exceed `_MIN_FETCH_SPAN` — that filters out weekends, overnight breaks,
    and short holiday closures that the source legitimately has no bars for.

    Each returned window is widened to at least `_MIN_FETCH_SPAN` (clamped to
    `[start, end]`) so the adapter does not generate one-bar round-trips.
    Windows that overlap after widening are merged.

    Inputs are expected to be sorted by `event_ts` (the repository guarantees this).
    """
    if start >= end:
        return []

    raw: list[tuple[datetime, datetime]] = []
    if not cached:
        raw.append((start, end))
    else:
        first_ts = cached[0].event_ts
        if first_ts > start:
            raw.append((start, first_ts))
        for prev, curr in pairwise(cached):
            if (curr.event_ts - prev.event_ts) >= _MIN_FETCH_SPAN:
                raw.append((prev.event_ts, curr.event_ts))
        last_ts = cached[-1].event_ts
        if end > last_ts:
            raw.append((last_ts, end))

    widened: list[tuple[datetime, datetime]] = []
    for gap_start, gap_end in raw:
        span = gap_end - gap_start
        if span < _MIN_FETCH_SPAN:
            extra = (_MIN_FETCH_SPAN - span) / 2
            ws = max(start, gap_start - extra)
            we = min(end, gap_end + extra)
            widened.append((ws, we))
        else:
            widened.append((gap_start, gap_end))

    if not widened:
        return []
    widened.sort()
    merged: list[tuple[datetime, datetime]] = [widened[0]]
    for ws, we in widened[1:]:
        last_s, last_e = merged[-1]
        if ws <= last_e:
            merged[-1] = (last_s, max(last_e, we))
        else:
            merged.append((ws, we))
    return merged
