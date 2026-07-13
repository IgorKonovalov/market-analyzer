"""Phase-5 done-when for Plan 0091: the `detect_divergences` MCP tool.

The body is factored into `_detect_divergences_response` so the fetch, empty-cache,
and `as_of`-replay paths run on a single event loop (no live MCP server). A
`_SeededProvider` returns canned bars for one `(symbol, timeframe)`, honouring the
window + `as_of` truncation. The tool wraps the pure `analysis.divergence`
detector, so the test pins that its result equals the standalone detector on the
same bars, plus the no-bars and trailing-replay paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis.divergence import DIVERGENCE_LOOKBACK, detect_divergences
from market_analyser.api.mcp_tools.detect_divergences import _detect_divergences_response
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

# A lower-high price path with heavy up-volume / light down-volume: OBV accumulates
# to a HIGHER high at the lower price peak -> a hidden bearish divergence on OBV.
_ACCUMULATION_CLOSES: list[float] = [
    100.0, 100.0, 100.0, 100.0,
    104.0, 108.0, 112.0, 116.0, 120.0,  # rally1 peak1 = 120
    116.0, 112.0, 108.0, 105.0,  # pull1
    108.0, 111.0, 114.0, 115.0,  # rally2 peak2 = 115 (lower high)
    112.0, 109.0, 107.0,  # pull2
]  # fmt: skip


def _accumulation_bars(symbol: str) -> list[Bar]:
    closes = _ACCUMULATION_CLOSES
    n = len(closes)
    bars: list[Bar] = []
    prev: float | None = None
    for i, c in enumerate(closes):
        v = 500.0 if prev is None else (1000.0 if c > prev else 100.0 if c < prev else 300.0)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=c,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                volume=v,
                source="fixture",
            )
        )
        prev = c
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


def test_detect_divergences_matches_standalone_detector() -> None:
    """A populated symbol returns the divergences the pure detector reports on the
    identical bars — the tool is a thin, faithful wrapper."""

    bars = _accumulation_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})

    resp = asyncio.run(
        _detect_divergences_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            oscillator="obv",
            lookback=DIVERGENCE_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.scanned_at.tzinfo is not None
    assert resp.result == detect_divergences(bars, "obv", DIVERGENCE_LOOKBACK)
    # The known hidden-bearish OBV divergence is present with sane anchors.
    hidden = [d for d in resp.result if d.kind == "hidden_bearish"]
    assert len(hidden) == 1
    assert hidden[0].price_pivots[1].price < hidden[0].price_pivots[0].price
    assert hidden[0].oscillator_pivots[1].price > hidden[0].oscillator_pivots[0].price


def test_detect_divergences_no_bars() -> None:
    provider = _SeededProvider({})
    resp = asyncio.run(
        _detect_divergences_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            oscillator="rsi",
            lookback=DIVERGENCE_LOOKBACK,
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_detect_divergences_as_of_is_trailing() -> None:
    """`as_of` truncates to bars at or before it — the detection never sees a future
    bar, and it matches a direct computation on the truncated series."""

    bars = _accumulation_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    as_of = bars[16].event_ts  # at peak2, before the confirming pullback

    resp = asyncio.run(
        _detect_divergences_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            oscillator="obv",
            lookback=DIVERGENCE_LOOKBACK,
            as_of=as_of,
        )
    )
    assert resp.result is not None
    truncated = [b for b in bars if b.event_ts <= as_of]
    assert resp.result == detect_divergences(truncated, "obv", DIVERGENCE_LOOKBACK)
    # Truncated before the pivot is confirmed -> the divergence is not yet knowable.
    assert resp.result == []
