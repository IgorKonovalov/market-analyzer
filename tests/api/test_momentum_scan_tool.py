"""Phase-3 done-when for Plan 0100: the `momentum_scan` watchlist scanner (ADR-0095).

Exercises the factored `_momentum_scan_response` on a single event loop: the RSI
band boundary-inclusivity (a symbol at an exact bound is in; just beyond is out),
the trend filter (only the requested trend passes), and no-lookahead (a scan at
`as_of=t` reads only bars at-or-before `t`).

Fixtures: a clean uptrend (RSI high, trend up), a clean downtrend (RSI low, trend
down), and an oscillation (RSI mid, trend sideways).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.api.mcp_tools.momentum_scan import _momentum_scan_response
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


def _bars(symbol: str, closes: Sequence[float]) -> list[Bar]:
    """Daily bars ending today from an explicit close series; high/low bracket the
    close by ±0.5 so ADX/ATR are well-defined for the trend classifier."""

    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i] + 0.5,
            low=closes[i] - 0.5,
            close=closes[i],
            volume=100.0,
            source="fixture",
        )
        for i in range(n)
    ]


def _uptrend(symbol: str, n: int = 120) -> list[Bar]:
    """Monotonic rise → RSI high, trend up."""

    return _bars(symbol, [100.0 + i for i in range(n)])


def _downtrend(symbol: str, n: int = 120) -> list[Bar]:
    """Monotonic fall → RSI low, trend down."""

    return _bars(symbol, [100.0 + (n - i) for i in range(n)])


def _oscillating(symbol: str, n: int = 120) -> list[Bar]:
    """Alternating closes → RSI ≈ mid, trend sideways."""

    closes: list[float] = []
    close = 100.0
    for i in range(n):
        close += 1.0 if i % 2 == 0 else -1.0
        closes.append(close)
    return _bars(symbol, closes)


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window
    and truncated at `as_of`. Symbols in `error_symbols` raise; every non-OHLCV
    Protocol method raises (this scanner only reads bars)."""

    def __init__(
        self,
        bars_by_key: dict[tuple[str, str], Sequence[Bar]],
        error_symbols: Iterable[str] = (),
    ) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}
        self._errors = set(error_symbols)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        if symbol in self._errors:
            raise RuntimeError(f"simulated fetch failure for {symbol}")
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


def test_momentum_scan_band_is_boundary_inclusive() -> None:
    provider = _SeededProvider({("MID", "1d"): _oscillating("MID")})

    # Discover MID's RSI with a full-range band.
    wide = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rsi_min=0.0,
            rsi_max=100.0,
            as_of=None,
        )
    )
    assert [m.symbol for m in wide.matches] == ["MID"]
    rsi = wide.matches[0].rsi

    # Band collapsed to the exact RSI on both sides → included (both bounds inclusive).
    exact = asyncio.run(
        _momentum_scan_response(
            provider=provider, symbols=["MID"], timeframe="1d", rsi_min=rsi, rsi_max=rsi, as_of=None
        )
    )
    assert [m.symbol for m in exact.matches] == ["MID"]

    # Just above the RSI → excluded (upper bound is a real bound, not open).
    above = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rsi_min=rsi + 0.01,
            rsi_max=100.0,
            as_of=None,
        )
    )
    assert above.matches == []
    # Just below the RSI → excluded (lower bound is a real bound, not open).
    below = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rsi_min=0.0,
            rsi_max=rsi - 0.01,
            as_of=None,
        )
    )
    assert below.matches == []


def test_momentum_scan_trend_filter_and_sort() -> None:
    provider = _SeededProvider(
        {
            ("UP", "1d"): _uptrend("UP"),
            ("DOWN", "1d"): _downtrend("DOWN"),
            ("MID", "1d"): _oscillating("MID"),
        }
    )

    # No trend filter, full band → all three, sorted by RSI descending.
    everything = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["MID", "DOWN", "UP"],
            timeframe="1d",
            as_of=None,
        )
    )
    assert [m.symbol for m in everything.matches] == ["UP", "MID", "DOWN"]
    by_symbol = {m.symbol: m for m in everything.matches}
    assert by_symbol["UP"].trend == "up"
    assert by_symbol["DOWN"].trend == "down"
    assert by_symbol["UP"].rsi > by_symbol["MID"].rsi > by_symbol["DOWN"].rsi

    # trend="up" keeps only the uptrend name; the others are dropped (not skipped).
    up_only = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["MID", "DOWN", "UP"],
            timeframe="1d",
            trend="up",
            as_of=None,
        )
    )
    assert [m.symbol for m in up_only.matches] == ["UP"]
    assert up_only.skipped == []  # filtered-out symbols are dropped, not skipped


def test_momentum_scan_skips_short_history() -> None:
    provider = _SeededProvider(
        {
            ("UP", "1d"): _uptrend("UP"),
            ("SHORT", "1d"): _bars("SHORT", [100.0, 101.0]),  # too few bars for RSI(14)
        }
    )
    resp = asyncio.run(
        _momentum_scan_response(
            provider=provider,
            symbols=["UP", "SHORT", "MISSING"],
            timeframe="1d",
            as_of=None,
        )
    )
    assert [m.symbol for m in resp.matches] == ["UP"]
    assert sorted(resp.skipped) == ["MISSING", "SHORT"]


def test_momentum_scan_boundary_validation() -> None:
    provider = _SeededProvider({})
    # empty list / unsupported timeframe / over the cap / inverted band / bad trend.
    bad_calls: list[dict[str, object]] = [
        {"symbols": [], "timeframe": "1d"},
        {"symbols": ["A", "B"], "timeframe": "5m"},
        {"symbols": [f"S{i}" for i in range(26)], "timeframe": "1d"},
        {"symbols": ["A"], "timeframe": "1d", "rsi_min": 70.0, "rsi_max": 30.0},
        {"symbols": ["A"], "timeframe": "1d", "trend": "up_and_to_the_right"},
    ]
    for kw in bad_calls:
        try:
            asyncio.run(_momentum_scan_response(provider=provider, as_of=None, **kw))  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kw!r}")


def test_momentum_scan_is_no_lookahead() -> None:
    """A scan at `as_of=t` reads only bars[..t]: it equals the same scan on a
    provider whose bars are already truncated to `t` (no future leak)."""

    full = _uptrend("UP")
    cutoff = full[90].event_ts

    at_t = asyncio.run(
        _momentum_scan_response(
            provider=_SeededProvider({("UP", "1d"): full}),
            symbols=["UP"],
            timeframe="1d",
            as_of=cutoff,
        )
    )
    truncated = asyncio.run(
        _momentum_scan_response(
            provider=_SeededProvider({("UP", "1d"): [b for b in full if b.event_ts <= cutoff]}),
            symbols=["UP"],
            timeframe="1d",
            as_of=None,
        )
    )

    assert [(m.symbol, m.rsi, m.trend, m.momentum) for m in at_t.matches] == [
        (m.symbol, m.rsi, m.trend, m.momentum) for m in truncated.matches
    ]
    assert at_t.matches  # the uptrend actually produced a match at t
