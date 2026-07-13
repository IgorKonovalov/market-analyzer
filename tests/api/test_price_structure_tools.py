"""Phase-4 done-when for Plan 0092: the four price-structure MCP tools.

Each tool body is factored into a `_<tool>_response` coroutine so the fetch,
empty-cache, and `as_of`-replay paths run on a single event loop (no live MCP
server). A `_SeededProvider` returns canned bars for one `(symbol, timeframe)`,
honouring the window + `as_of` truncation.

Covers `fibonacci_levels`, `market_structure`, `pivot_points`, and `anchored_vwap`:
each drives end-to-end on a populated symbol, returns `no_bars`/`None` on an empty
cache, and (where relevant) honours the auto-anchor, explicit anchor, `no_swing`,
and trailing-replay paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis.levels import pivot_points
from market_analyser.analysis.structure import market_structure as compute_market_structure
from market_analyser.api.mcp_tools.anchored_vwap import _anchored_vwap_response
from market_analyser.api.mcp_tools.fibonacci_levels import _fibonacci_levels_response
from market_analyser.api.mcp_tools.market_structure import _market_structure_response
from market_analyser.api.mcp_tools.pivot_points import _pivot_points_response
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


def _swing_bars(symbol: str, n: int = 80) -> list[Bar]:
    """A triangle-wave path (period 20, 90<->140) — repeated clear swing highs and
    lows so the dominant-swing auto-anchor and the HH/HL structure both have real
    pivots to work with."""

    bars: list[Bar] = []
    for i in range(n):
        phase = i % 20
        v = 90.0 + (phase * 5.0 if phase <= 10 else (20 - phase) * 5.0)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=v,
                high=v + 0.5,
                low=v - 0.5,
                close=v,
                volume=1000.0,
                source="fixture",
            )
        )
    return bars


def _flat_bars(symbol: str, n: int = 40) -> list[Bar]:
    """A dead-flat band: no strict swing pivots, so no dominant swing to anchor to."""

    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
            source="fixture",
        )
        for i in range(n)
    ]


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window and
    truncated at `as_of`. Every other Protocol method raises."""

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


# --------------------------------------------------------------------------- #
# fibonacci_levels                                                             #
# --------------------------------------------------------------------------- #


def test_fibonacci_levels_auto_anchors_on_populated_symbol() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _fibonacci_levels_response(
            provider=provider, symbol="A", timeframe="1d", kind="retracement", as_of=None
        )
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.result.kind == "retracement"
    assert set(resp.result.levels) == {"0.236", "0.382", "0.5", "0.618", "0.786"}
    assert resp.scanned_at.tzinfo is not None


def test_fibonacci_levels_extension_projects_off_last_close() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _fibonacci_levels_response(
            provider=provider, symbol="A", timeframe="1d", kind="extension", as_of=None
        )
    )
    assert resp.result is not None
    assert resp.result.kind == "extension"
    assert set(resp.result.levels) == {"1.272", "1.618", "2.0", "2.618"}


def test_fibonacci_levels_no_bars() -> None:
    resp = asyncio.run(
        _fibonacci_levels_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", kind="retracement", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_fibonacci_levels_no_swing_on_flat_series() -> None:
    provider = _SeededProvider({("A", "1d"): _flat_bars("A")})
    resp = asyncio.run(
        _fibonacci_levels_response(
            provider=provider, symbol="A", timeframe="1d", kind="retracement", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_swing"


# --------------------------------------------------------------------------- #
# market_structure                                                            #
# --------------------------------------------------------------------------- #


def test_market_structure_on_populated_symbol() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    resp = asyncio.run(
        _market_structure_response(provider=provider, symbol="A", timeframe="1d", as_of=None)
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.result.structural_trend in {"up", "down", "range"}
    assert resp.result == compute_market_structure(bars)  # tool matches the pure read


def test_market_structure_no_bars() -> None:
    resp = asyncio.run(
        _market_structure_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_market_structure_as_of_is_trailing() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    as_of = bars[50].event_ts
    resp = asyncio.run(
        _market_structure_response(provider=provider, symbol="A", timeframe="1d", as_of=as_of)
    )
    assert resp.result is not None
    truncated = [b for b in bars if b.event_ts <= as_of]
    assert resp.result == compute_market_structure(truncated)  # no future bar leaks in
    # Every event is knowable within the truncated window.
    assert all(e.bar_index < len(truncated) for e in resp.result.events)


# --------------------------------------------------------------------------- #
# pivot_points                                                                 #
# --------------------------------------------------------------------------- #


def test_pivot_points_on_populated_symbol_matches_pure_read() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    for method in ("floor", "camarilla", "woodie"):
        resp = asyncio.run(
            _pivot_points_response(
                provider=provider, symbol="A", timeframe="1d", method=method, as_of=None
            )
        )
        assert resp.partial_reason is None
        assert resp.result is not None
        assert resp.result == pivot_points(bars, method)  # tool == pure read on same bars


def test_pivot_points_no_bars() -> None:
    resp = asyncio.run(
        _pivot_points_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", method="floor", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


# --------------------------------------------------------------------------- #
# anchored_vwap                                                                #
# --------------------------------------------------------------------------- #


def test_anchored_vwap_auto_anchor_on_populated_symbol() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _anchored_vwap_response(
            provider=provider, symbol="A", timeframe="1d", anchor_index=None, as_of=None
        )
    )
    assert resp.partial_reason is None
    assert resp.result is not None
    assert resp.result.value is not None  # volume is positive → a defined value
    assert 0 <= resp.result.anchor_index < 80


def test_anchored_vwap_explicit_anchor() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    resp = asyncio.run(
        _anchored_vwap_response(
            provider=provider, symbol="A", timeframe="1d", anchor_index=0, as_of=None
        )
    )
    assert resp.result is not None
    assert resp.result.anchor_index == 0
    assert resp.result.anchor_ts == bars[0].event_ts


def test_anchored_vwap_out_of_range_anchor_raises() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(
            _anchored_vwap_response(
                provider=provider, symbol="A", timeframe="1d", anchor_index=999, as_of=None
            )
        )


def test_anchored_vwap_no_bars() -> None:
    resp = asyncio.run(
        _anchored_vwap_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", anchor_index=None, as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"
