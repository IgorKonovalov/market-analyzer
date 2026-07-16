"""Done-when for Plan 0102 phase 2: the sector momentum engine (ADR-0097).

Pins the engine's contract over a fixture taxonomy: (a) the equal-weight mean math,
(b) the missing-constituent skip + the incomplete-sector floor rule, (c) the sector
ranking order (complete before incomplete, momentum descending), (d) leader/laggard
identification, and (e) no-lookahead via truncation-invariance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis.sector_taxonomy import SectorTaxonomy, load_taxonomy
from market_analyser.analysis.sectors import rank_sectors, score_trailing_return
from market_analyser.analysis.types import SectorMomentum
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

# Every fixture uses a 2-bar lookback: base = close three bars back, latest = last close,
# so a series [100, *, 100 + R] yields exactly an R% trailing return.
_LOOKBACK = 2


def _series(symbol: str, closes: Sequence[float]) -> list[Bar]:
    """Daily flat OHLC bars ending today from an explicit close series."""

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


def _ret(symbol: str, return_pct: float) -> list[Bar]:
    """A 3-bar series whose trailing 2-bar return is exactly `return_pct` percent."""

    return _series(symbol, [100.0, 100.0, 100.0 + return_pct])


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window and
    truncated at `as_of`; symbols in `error_symbols` raise. Non-OHLCV Protocol methods
    are unused here and raise."""

    def __init__(
        self,
        bars_by_key: Mapping[tuple[str, str], Sequence[Bar]],
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


# --------------------------------------------------------------------------- #
# The pure scorer                                                               #
# --------------------------------------------------------------------------- #


def test_score_trailing_return_math() -> None:
    from market_analyser.analysis.types import ConstituentReturn

    match = score_trailing_return(_ret("X", 12.0), _LOOKBACK)
    assert isinstance(match, ConstituentReturn)
    assert match.symbol == "X"
    assert match.return_pct == 12.0


def test_score_skips_short_history_and_zero_base() -> None:
    from market_analyser.analysis.scanners import SCAN_SKIP

    assert score_trailing_return(_series("SHORT", [100.0, 101.0]), _LOOKBACK) is SCAN_SKIP
    assert score_trailing_return(_series("ZERO", [0.0, 50.0, 60.0]), _LOOKBACK) is SCAN_SKIP


# --------------------------------------------------------------------------- #
# The engine                                                                    #
# --------------------------------------------------------------------------- #


def _fixture_taxonomy() -> SectorTaxonomy:
    return load_taxonomy(
        "test",
        (
            ("Big", ("BIGA", "BIGB", "BIGC", "BIGD", "BIGE")),
            ("Cold", ("COLDA", "COLDB")),
            ("Thin", ("THINA", "THINMISS")),
            ("Empty", ("EMPTYA", "EMPTYB")),
        ),
    )


def _fixture_provider() -> _SeededProvider:
    return _SeededProvider(
        {
            ("BIGA", "1d"): _ret("BIGA", 50.0),
            ("BIGB", "1d"): _ret("BIGB", 40.0),
            ("BIGC", "1d"): _ret("BIGC", 30.0),
            ("BIGD", "1d"): _ret("BIGD", 20.0),
            ("BIGE", "1d"): _ret("BIGE", 10.0),
            ("COLDA", "1d"): _ret("COLDA", -10.0),
            ("COLDB", "1d"): _ret("COLDB", -20.0),
            ("THINA", "1d"): _ret("THINA", 5.0),
            # THINMISS, EMPTYA, EMPTYB: no cached bars -> skipped.
        }
    )


def _rank() -> list[SectorMomentum]:
    sectors, scanned_at = asyncio.run(
        rank_sectors(
            provider=_fixture_provider(),
            taxonomy=_fixture_taxonomy(),
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=None,
        )
    )
    assert scanned_at.tzinfo is not None
    return sectors


def test_equal_weight_mean_math() -> None:
    by_name = {s.sector: s for s in _rank()}
    assert by_name["Big"].momentum == 30.0  # mean(50,40,30,20,10)
    assert by_name["Cold"].momentum == -15.0  # mean(-10,-20)
    assert by_name["Thin"].momentum == 5.0  # single priced constituent
    assert by_name["Empty"].momentum is None  # nothing priced -> honest None, not 0


def test_skip_and_incomplete_rule() -> None:
    by_name = {s.sector: s for s in _rank()}
    assert by_name["Thin"].n_priced == 1
    assert by_name["Thin"].skipped == ["THINMISS"]
    assert by_name["Thin"].complete is False  # below the >=2 floor
    assert by_name["Empty"].n_priced == 0
    assert sorted(by_name["Empty"].skipped) == ["EMPTYA", "EMPTYB"]
    assert by_name["Empty"].complete is False
    assert by_name["Big"].complete is True
    assert by_name["Big"].skipped == []


def test_ranking_order_complete_first_then_momentum_desc() -> None:
    order = [s.sector for s in _rank()]
    # Complete sectors first by momentum descending (Big +30, Cold -15), then the
    # incomplete ones (Thin has a defined momentum, Empty is None -> last).
    assert order == ["Big", "Cold", "Thin", "Empty"]


def test_leaders_and_laggards() -> None:
    big = next(s for s in _rank() if s.sector == "Big")
    # k = min(top_n=3, n_priced // 2 = 2) = 2 -> two disjoint leaders/laggards.
    assert [(c.symbol, c.return_pct) for c in big.leaders] == [("BIGA", 50.0), ("BIGB", 40.0)]
    assert [(c.symbol, c.return_pct) for c in big.laggards] == [("BIGE", 10.0), ("BIGD", 20.0)]
    leader_names = {c.symbol for c in big.leaders}
    laggard_names = {c.symbol for c in big.laggards}
    assert leader_names.isdisjoint(laggard_names)


def test_two_priced_sector_has_single_leader_and_laggard() -> None:
    cold = next(s for s in _rank() if s.sector == "Cold")
    # k = min(3, 2 // 2 = 1) = 1.
    assert [c.symbol for c in cold.leaders] == ["COLDA"]  # -10 is the better of the two
    assert [c.symbol for c in cold.laggards] == ["COLDB"]  # -20 the worse


def test_incomplete_sector_reports_no_leaders_or_laggards() -> None:
    thin = next(s for s in _rank() if s.sector == "Thin")
    # k = min(3, 1 // 2 = 0) = 0.
    assert thin.leaders == []
    assert thin.laggards == []


def test_fetch_error_is_skipped_not_fatal() -> None:
    taxonomy = load_taxonomy("t", (("S", ("GOOD", "BOOM")),))
    provider = _SeededProvider({("GOOD", "1d"): _ret("GOOD", 7.0)}, error_symbols={"BOOM"})
    sectors, _ = asyncio.run(
        rank_sectors(
            provider=provider,
            taxonomy=taxonomy,
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=None,
        )
    )
    assert sectors[0].skipped == ["BOOM"]
    assert sectors[0].n_priced == 1


def test_lookback_must_be_positive() -> None:
    try:
        asyncio.run(
            rank_sectors(
                provider=_fixture_provider(),
                taxonomy=_fixture_taxonomy(),
                timeframe="1d",
                lookback=0,
                as_of=None,
            )
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError for lookback=0")


def test_no_lookahead_truncation_invariance() -> None:
    """A read at `as_of=T` equals the same read over bars truncated to `T`. Future bars
    that would change a constituent's return must not leak in."""

    taxonomy = load_taxonomy("t", (("S", ("A", "B")),))
    # A/B carry a bar AFTER the cutoff that, if included, would change the 2-bar return.
    full_a = _series("A", [100.0, 100.0, 120.0, 999.0])  # to cutoff: +20%; full: huge
    full_b = _series("B", [100.0, 100.0, 110.0, 1.0])  # to cutoff: +10%; full: negative
    cutoff = full_a[2].event_ts

    at_t, _ = asyncio.run(
        rank_sectors(
            provider=_SeededProvider({("A", "1d"): full_a, ("B", "1d"): full_b}),
            taxonomy=taxonomy,
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=cutoff,
        )
    )
    truncated, _ = asyncio.run(
        rank_sectors(
            provider=_SeededProvider(
                {
                    ("A", "1d"): [b for b in full_a if b.event_ts <= cutoff],
                    ("B", "1d"): [b for b in full_b if b.event_ts <= cutoff],
                }
            ),
            taxonomy=taxonomy,
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=None,
        )
    )
    assert [s.model_dump() for s in at_t] == [s.model_dump() for s in truncated]
    # And the truncated read is the +15% mean (not contaminated by the future bars).
    assert at_t[0].momentum == 15.0
