"""Phase-4 done-when for Plan 0090: the `counter_trend_volume` MCP tool.

The body is factored into `_counter_trend_volume_response` so the fetch, empty-
cache, and `as_of`-replay paths run on a single event loop (no live MCP server).
A `_SeededProvider` returns canned bars for one `(symbol, timeframe)`, honouring
the window + `as_of` truncation. The tool anchors the decomposition to the
snapshot's canonical trend — so the test pins that the anchor equals
`analyze_symbol`'s `trend` on the same bars, plus the no-bars and trailing-replay
paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis.volume import COUNTER_TREND_LOOKBACK, counter_trend_volume
from market_analyser.api.mcp_tools.analyze_symbol import _analyze_symbol_response
from market_analyser.api.mcp_tools.counter_trend_volume import _counter_trend_volume_response
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

_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _uptrend_with_counter_bars(symbol: str, n: int = 90) -> list[Bar]:
    """A steady rise (closes +1/bar → a clear UP trend) where most bars are bullish
    intrabar (close > open) but every 5th recent bar is bearish intrabar (open >
    close) — still closing above the prior close, so the trend stays up while the
    window carries genuine counter-trend down-bars."""

    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + i
        bearish = i >= n - 20 and i % 5 == 0
        if bearish:
            o, c = base + 0.4, base - 0.4  # down-bar intrabar; counter to the UP trend
            v = 300.0  # heavier
        else:
            o, c = base - 0.4, base + 0.4  # up-bar intrabar
            v = 100.0
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=o,
                high=max(o, c) + 0.3,
                low=min(o, c) - 0.3,
                close=c,
                volume=v,
                source="fixture",
            )
        )
    return bars


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window
    and truncated at `as_of`. Every other Protocol method raises."""

    def __init__(self, bars_by_key: dict[tuple[str, str], Sequence[Bar]]) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        bars = [b for b in self._by_key.get((symbol, timeframe), []) if start <= b.event_ts <= end]
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def test_counter_trend_detail_anchors_to_snapshot_trend() -> None:
    """A populated symbol returns the per-bar decomposition, and its trend anchor is
    exactly the label `analyze_symbol` reports on the same bars (ADR-0083)."""

    bars = _uptrend_with_counter_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})

    resp = asyncio.run(
        _counter_trend_volume_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=COUNTER_TREND_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.result.symbol == "A"
    assert len(resp.result.bars) == COUNTER_TREND_LOOKBACK
    assert resp.scanned_at.tzinfo is not None

    # The anchor equals analyze_symbol's trend on the identical bars.
    analyzed = asyncio.run(
        _analyze_symbol_response(
            provider=provider, symbol="A", timeframe="1d", lookback="1y", as_of=None
        )
    )
    assert analyzed.snapshot is not None
    assert resp.result.trend == analyzed.snapshot.trend
    # The fixture's heavy down-bars register as counter-trend under the up-trend.
    assert any(b.is_counter_trend for b in resp.result.bars)


def test_counter_trend_detail_no_bars() -> None:
    provider = _SeededProvider({})
    resp = asyncio.run(
        _counter_trend_volume_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=COUNTER_TREND_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_counter_trend_detail_as_of_is_trailing() -> None:
    """`as_of` truncates to bars at or before it — the decomposition never sees a
    future bar, and it matches a direct computation on the truncated series."""

    bars = _uptrend_with_counter_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    as_of = bars[70].event_ts

    resp = asyncio.run(
        _counter_trend_volume_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=COUNTER_TREND_LOOKBACK,
            as_of=as_of,
        )
    )
    assert resp.result is not None
    assert all(b.ts <= as_of for b in resp.result.bars)

    truncated = [b for b in bars if b.event_ts <= as_of]
    direct = counter_trend_volume(truncated, resp.result.trend, COUNTER_TREND_LOOKBACK)
    assert resp.result.bars == direct.bars
    assert resp.result.counter_trend_volume_share == direct.counter_trend_volume_share

    # And the trailing read differs from the full-series one (future bars excluded).
    full = asyncio.run(
        _counter_trend_volume_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            lookback=COUNTER_TREND_LOOKBACK,
            as_of=None,
        )
    )
    assert full.result is not None
    assert full.result.bars[-1].ts != resp.result.bars[-1].ts
