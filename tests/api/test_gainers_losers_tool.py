"""Phase-2 done-when for Plan 0100: the `gainers_losers` watchlist scanner (ADR-0095).

Exercises the factored `_gainers_losers_response` on a single event loop: the
ordering (largest gainer first, largest loser last), the sign convention (a gain is
positive/`up`, a loss negative/`down`), the single-bar skip (no prior close → not
divided-by-zero), and no-lookahead (a scan at `as_of=t` reads only the last two
bars at-or-before `t`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.api.mcp_tools.gainers_losers import _gainers_losers_response
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
    """Daily bars ending today from an explicit close series (high/low collapse to
    the close — the return scorer reads closes only)."""

    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i],
            low=closes[i],
            close=closes[i],
            volume=100.0,
            source="fixture",
        )
        for i in range(n)
    ]


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


def test_gainers_losers_orders_by_change_with_sign_and_skips_single_bar() -> None:
    provider = _SeededProvider(
        {
            ("GAIN", "1d"): _bars("GAIN", [100.0, 110.0]),  # +10%
            ("LOSE", "1d"): _bars("LOSE", [100.0, 90.0]),  # -10%
            ("SMALL", "1d"): _bars("SMALL", [100.0, 101.0]),  # +1%
            ("ONEBAR", "1d"): _bars("ONEBAR", [100.0]),  # no prior close
        }
    )
    resp = asyncio.run(
        _gainers_losers_response(
            provider=provider,
            symbols=["LOSE", "SMALL", "GAIN", "ONEBAR", "MISSING"],
            timeframe="1d",
            as_of=None,
        )
    )

    # Biggest gainer first, biggest loser last.
    assert [m.symbol for m in resp.matches] == ["GAIN", "SMALL", "LOSE"]
    changes = {m.symbol: m for m in resp.matches}
    assert changes["GAIN"].change_pct == 10.0
    assert changes["GAIN"].direction == "up"
    assert changes["SMALL"].change_pct == 1.0
    assert changes["SMALL"].direction == "up"
    assert changes["LOSE"].change_pct == -10.0
    assert changes["LOSE"].direction == "down"
    # Single bar (no prior close) + missing symbol both skipped, no divide-by-zero.
    assert sorted(resp.skipped) == ["MISSING", "ONEBAR"]
    assert resp.scanned_at.tzinfo is not None


def test_gainers_losers_boundary_validation() -> None:
    provider = _SeededProvider({})
    for symbols, timeframe in (
        ([], "1d"),  # empty list
        (["A", "B"], "5m"),  # unsupported timeframe
        ([f"S{i}" for i in range(26)], "1d"),  # over the cap (MAX_SCAN_SYMBOLS = 25)
    ):
        try:
            asyncio.run(
                _gainers_losers_response(
                    provider=provider, symbols=symbols, timeframe=timeframe, as_of=None
                )
            )
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for symbols={symbols!r} timeframe={timeframe!r}")


def test_gainers_losers_is_no_lookahead() -> None:
    """A scan at `as_of=t` measures the change over the two bars at-or-before `t` —
    a later bar beyond `t` (a huge reversal) must not change the as_of=t result."""

    # Bars: 100 -> 110 (+10% at index 1) then a crash to 10 at index 2.
    full = _bars("X", [100.0, 110.0, 10.0])
    cutoff = full[1].event_ts  # as of the +10% bar, before the crash prints

    at_t = asyncio.run(
        _gainers_losers_response(
            provider=_SeededProvider({("X", "1d"): full}),
            symbols=["X"],
            timeframe="1d",
            as_of=cutoff,
        )
    )
    truncated = asyncio.run(
        _gainers_losers_response(
            provider=_SeededProvider({("X", "1d"): [b for b in full if b.event_ts <= cutoff]}),
            symbols=["X"],
            timeframe="1d",
            as_of=None,
        )
    )

    assert [(m.symbol, m.change_pct, m.direction) for m in at_t.matches] == [("X", 10.0, "up")]
    assert [(m.symbol, m.change_pct, m.direction) for m in truncated.matches] == [("X", 10.0, "up")]
