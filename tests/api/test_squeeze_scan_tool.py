"""Phase-1 done-when for Plan 0100: the `squeeze_scan` watchlist scanner (ADR-0095).

Exercises the factored `_squeeze_scan_response` on a single event loop (no live MCP
server): the ranking order (tightest coil — lowest `bb_width_pct90` — first), the
skip path (no bars, short history), and truncation-invariance (a scan at `as_of=t`
equals the same scan run later with the window truncated to `t` — no future leak).

A `_SeededProvider` returns canned per-(symbol, timeframe) bars, filtered to the
window and truncated at `as_of`; symbols in `error_symbols` raise, exercising
graceful degradation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.api.mcp_tools.squeeze_scan import (
    SqueezeScanResponse,
    _squeeze_scan_response,
)
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
    """Daily bars ending today from an explicit close series; high/low collapse to
    the close (a degenerate but valid OHLC band — the squeeze trio is driven by the
    close-series band-width, ADR-0083)."""

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


def _volatile_then_flat(symbol: str, *, switch: int = 80, n: int = 100) -> list[Bar]:
    """A wide-swinging series that recently calmed: alternating ±5 for the first
    `switch` bars, then flat. The latest 20-window band-width sits at the low end of
    the symbol's own history → a *low* `bb_width_pct90` (tight coil)."""

    closes = [100.0 + (5.0 if i % 2 == 0 else -5.0) if i < switch else 100.0 for i in range(n)]
    return _bars(symbol, closes)


def _flat_then_volatile(symbol: str, *, switch: int = 80, n: int = 100) -> list[Bar]:
    """The mirror: flat for the first `switch` bars, then wide-swinging. The latest
    band-width sits at the high end of the symbol's own history → a *high*
    `bb_width_pct90` (no coil)."""

    closes = [100.0 if i < switch else 100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(n)]
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


def test_squeeze_scan_ranks_tightest_first_and_skips_uncomputable() -> None:
    provider = _SeededProvider(
        {
            ("WIDE", "1d"): _flat_then_volatile("WIDE"),  # latest band-width is wide
            ("TIGHT", "1d"): _volatile_then_flat("TIGHT"),  # latest band-width is tight
            ("SHORT", "1d"): _bars("SHORT", [100.0] * 10),  # too few bars for the percentile
        }
    )
    resp = asyncio.run(
        _squeeze_scan_response(
            provider=provider,
            symbols=["WIDE", "TIGHT", "SHORT", "MISSING"],
            timeframe="1d",
            as_of=None,
        )
    )

    # Ranked by bb_width_pct90 ascending — the tightest coil first.
    assert [m.symbol for m in resp.matches] == ["TIGHT", "WIDE"]
    assert resp.matches[0].bb_width_pct90 < resp.matches[1].bb_width_pct90
    for m in resp.matches:
        assert isinstance(m.squeeze_on, bool)  # the trio's flag is present
        assert m.bb_width >= 0.0  # a fully-flat recent window is a valid 0.0 coil
    # SHORT: percentile undefined → skipped, not crashed. MISSING: no bars → skipped.
    assert sorted(resp.skipped) == ["MISSING", "SHORT"]
    assert resp.scanned_at.tzinfo is not None


def test_squeeze_scan_boundary_validation() -> None:
    provider = _SeededProvider({})
    for symbols, timeframe in (
        ([], "1d"),  # empty list
        (["A", "B"], "5m"),  # unsupported timeframe
        ([f"S{i}" for i in range(26)], "1d"),  # over the cap (MAX_SCAN_SYMBOLS = 25)
    ):
        try:
            asyncio.run(
                _squeeze_scan_response(
                    provider=provider, symbols=symbols, timeframe=timeframe, as_of=None
                )
            )
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for symbols={symbols!r} timeframe={timeframe!r}")


def test_squeeze_scan_is_truncation_invariant() -> None:
    """A scan at `as_of=t` reads only bars[..t]: it equals the same scan run later
    on a provider whose bars are already truncated to `t` (no future leak)."""

    full_wide = _flat_then_volatile("WIDE")
    full_tight = _volatile_then_flat("TIGHT")
    cutoff = full_wide[90].event_ts  # 10 bars before the series end

    at_t = asyncio.run(
        _squeeze_scan_response(
            provider=_SeededProvider({("WIDE", "1d"): full_wide, ("TIGHT", "1d"): full_tight}),
            symbols=["WIDE", "TIGHT"],
            timeframe="1d",
            as_of=cutoff,
        )
    )
    truncated = asyncio.run(
        _squeeze_scan_response(
            provider=_SeededProvider(
                {
                    ("WIDE", "1d"): [b for b in full_wide if b.event_ts <= cutoff],
                    ("TIGHT", "1d"): [b for b in full_tight if b.event_ts <= cutoff],
                }
            ),
            symbols=["WIDE", "TIGHT"],
            timeframe="1d",
            as_of=None,
        )
    )

    def _key(resp: SqueezeScanResponse) -> list[tuple[str, float, float, bool]]:
        return [(m.symbol, m.bb_width, m.bb_width_pct90, m.squeeze_on) for m in resp.matches]

    assert _key(at_t) == _key(truncated)
    assert at_t.skipped == truncated.skipped
