"""Plan 0001 phase 3 + Plan 0004 phase 1: cache policy on DefaultMarketDataProvider.

Asserts coverage-aware behavior:
  (a) cache covers [start, end] entirely        -> no adapter call
  (b) cache covers a head slice                  -> adapter asked for the tail gap only
  (c) cache has a middle hole bigger than the    -> adapter called for the hole only
      gap-fill threshold
  (d) empty cache                                -> adapter called for the full window
  (e) as_of set + partial coverage               -> raises (anti-lookahead)

Plus the pre-existing live-mode and as_of guard cases.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpError
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import RateLimitedError
from market_analyser.data.types import Bar
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import BarRepository


@pytest.fixture
def repo() -> Iterator[BarRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BarRepository(make_session_factory(engine))
    engine.dispose()


def _row(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }


def _daily_rows(start: datetime, days: int) -> list[dict[str, Any]]:
    return [_row((start + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(days)]


def _yahoo_with_calls(
    rows: list[dict[str, Any]],
) -> tuple[YahooAdapter, list[str]]:
    """Fetcher that returns the same `rows` regardless of period (legacy helper)."""
    calls: list[str] = []

    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        calls.append(symbol)
        return rows

    return YahooAdapter(fetcher=fetcher), calls


def _yahoo_with_call_log(
    rows: list[dict[str, Any]],
) -> tuple[YahooAdapter, list[tuple[str, str]]]:
    """Fetcher that records (symbol, period) per call. The adapter still filters
    the response to [start, end], so we can return a generous superset of bars
    and trust the adapter to discard the rest."""
    calls: list[tuple[str, str]] = []

    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        calls.append((symbol, period))
        return rows

    return YahooAdapter(fetcher=fetcher), calls


# -- case (d): empty cache fetches the full window --------------------------


def test_empty_cache_fetches_full_window(repo: BarRepository) -> None:
    yahoo, calls = _yahoo_with_calls([_row("2026-04-15"), _row("2026-04-16")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    bars = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 2
    assert calls == ["AAPL"]


# -- case (a): cache covers [start, end] entirely -> no adapter call --------


def test_full_cache_coverage_no_fetch(repo: BarRepository) -> None:
    # Pre-warm the cache with a daily strip dense enough that no gap exceeds
    # the 10-day fetch threshold. 30 bars across 30 days does it.
    warming_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 30)
    yahoo_warmer, warm_calls = _yahoo_with_call_log(warming_rows)
    provider = DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo)
    provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert warm_calls == [("AAPL", "1mo")]

    # Now build a fresh provider whose adapter would scream if called.
    def explode(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        raise AssertionError(
            f"adapter must not be called for fully cached coverage: {symbol} {period}",
        )

    no_call_provider = DefaultMarketDataProvider(
        yahoo=YahooAdapter(fetcher=explode),
        bar_repository=repo,
    )
    bars = no_call_provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(bars) == 30


# -- case (b): cache covers a head slice -> fetch the tail gap only ---------


def test_head_coverage_fetches_tail_only(repo: BarRepository) -> None:
    # Warm the cache with the first 10 days of April.
    warming_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 10)
    yahoo_warmer, _warm_calls = _yahoo_with_call_log(warming_rows)
    DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo).get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 10, tzinfo=UTC),
    )

    # Now ask for the full month; only the tail gap (>10 days, threshold) should
    # be fetched. Adapter returns a generous strip; its own filter trims to the
    # requested window.
    tail_rows = _daily_rows(datetime(2026, 4, 10, tzinfo=UTC), 21)
    yahoo, calls = _yahoo_with_call_log(tail_rows)
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    bars = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(calls) == 1, f"expected 1 fetch for the tail gap, got {calls}"
    # The cached head bars plus the fetched tail bars cover the month.
    assert {b.event_ts.date().isoformat() for b in bars} >= {
        f"2026-04-{d:02d}" for d in range(1, 11)
    }
    assert any(b.event_ts.date().isoformat() == "2026-04-30" for b in bars)


# -- case (c): cache has a middle hole > threshold -> fetch the hole --------


def test_middle_hole_fetches_only_the_hole(repo: BarRepository) -> None:
    # Cache: April 1-3 and April 25-30. Hole = April 3 -> April 25 = 22 days.
    head_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 3)
    tail_rows = _daily_rows(datetime(2026, 4, 25, tzinfo=UTC), 6)
    yahoo_warmer, _warm_calls = _yahoo_with_call_log(head_rows + tail_rows)
    DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo).get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )

    # Reset the call log: re-query and assert the adapter is called exactly
    # once, for the middle hole only.
    hole_rows = _daily_rows(datetime(2026, 4, 3, tzinfo=UTC), 23)
    yahoo, calls = _yahoo_with_call_log(hole_rows)
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(calls) == 1, f"expected 1 fetch for the middle hole, got {calls}"


# -- case (e): as_of with partial coverage -> raises ------------------------


def test_as_of_with_partial_coverage_raises(repo: BarRepository) -> None:
    # Cache only the first half of April.
    warming_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 10)
    yahoo_warmer, _warm_calls = _yahoo_with_call_log(warming_rows)
    DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo).get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 10, tzinfo=UTC),
    )

    # Adapter that explodes if called: anti-lookahead must short-circuit before
    # any remote fetch.
    def explode(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        raise AssertionError("as_of must never fall through to a remote fetch")

    provider = DefaultMarketDataProvider(
        yahoo=YahooAdapter(fetcher=explode),
        bar_repository=repo,
    )
    with pytest.raises(ValueError, match="anti-lookahead"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 4, 30, tzinfo=UTC),
            as_of=datetime.now(tz=UTC) + timedelta(hours=1),
        )


# -- pre-existing guards ----------------------------------------------------


def test_as_of_with_empty_cache_raises_no_fetch(repo: BarRepository) -> None:
    yahoo, calls = _yahoo_with_calls([_row("2026-04-15")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    with pytest.raises(ValueError, match="anti-lookahead"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            as_of=datetime(2026, 5, 1, tzinfo=UTC),
        )
    assert calls == []


def test_as_of_with_full_coverage_returns_bars(repo: BarRepository) -> None:
    # Warm the cache with a dense daily strip so as_of mode finds no gaps.
    warming_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 30)
    yahoo_warmer, _warm_calls = _yahoo_with_call_log(warming_rows)
    DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo).get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )

    def explode(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        raise AssertionError("as_of with full coverage must not call the adapter")

    provider = DefaultMarketDataProvider(
        yahoo=YahooAdapter(fetcher=explode),
        bar_repository=repo,
    )
    bars = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
        as_of=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    assert len(bars) == 30


def test_no_repo_with_as_of_raises() -> None:
    yahoo, _calls = _yahoo_with_calls([_row("2026-04-15")])
    provider = DefaultMarketDataProvider(yahoo=yahoo)
    with pytest.raises(ValueError, match="as_of requires"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            as_of=datetime(2026, 5, 1, tzinfo=UTC),
        )


# -- Plan 0013 phase 3: partial-failure surfacing via get_ohlcv_with_status ---


def _scripted_yahoo(outcomes: list[list[dict[str, Any]] | Exception]) -> YahooAdapter:
    """A YahooAdapter whose fetcher returns rows or raises by call order — one
    outcome per gap the provider fetches (gaps are fetched in sorted order)."""
    state = {"i": 0}

    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        outcome = outcomes[state["i"]]
        state["i"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return YahooAdapter(fetcher=fetcher)


def _rate_limit_error() -> ResilientHttpError:
    """A transport-level 429 the YahooAdapter classifies into RateLimitedError."""
    return ResilientHttpError(
        source_name="yahoo",
        last_response=HttpResponse(status_code=429, headers={}, body=b"", elapsed_seconds=0.0),
        last_exception=None,
        attempts=4,
    )


def _seed_bar(ts: datetime) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe="1d",
        event_ts=ts,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


def _seed_three_gap_cache(repo: BarRepository) -> tuple[datetime, datetime]:
    """Seed two dense blocks of cached bars so [start, end] has exactly three
    NON-adjacent gaps (head / between-blocks / tail), each wider than the 10-day
    fetch threshold. Single-point cached bars would leave adjacent gaps that
    `_coverage_gaps` merges into one (they share the bar's timestamp as a
    boundary); separating the cache into blocks keeps the three gaps distinct."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)
    block_a = [_seed_bar(datetime(2026, 1, 20, tzinfo=UTC) + timedelta(days=i)) for i in range(12)]
    block_b = [_seed_bar(datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=i)) for i in range(12)]
    repo.upsert_bars(block_a + block_b)
    return start, end


def test_get_ohlcv_with_status_partial_failure_surfaces_reason(repo: BarRepository) -> None:
    start, end = _seed_three_gap_cache(repo)
    yahoo = _scripted_yahoo(
        [
            [_row("2026-01-15")],  # gap 1 (head) succeeds
            _rate_limit_error(),  # gap 2 (middle) rate-limited
            [_row("2026-03-20")],  # gap 3 (tail) succeeds
        ],
    )
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)

    result = provider.get_ohlcv_with_status("AAPL", "1d", start, end)

    assert result.partial_reason == "rate_limited"
    assert result.message  # carries the upstream detail
    timestamps = [bar.event_ts for bar in result.bars]
    # cached (two 12-bar blocks = 24) + gap1 bar + gap3 bar; the failed middle
    # gap contributed nothing.
    assert datetime(2026, 1, 15, tzinfo=UTC) in timestamps
    assert datetime(2026, 3, 20, tzinfo=UTC) in timestamps
    assert len(result.bars) == 26


def test_get_ohlcv_raises_loud_on_partial_failure(repo: BarRepository) -> None:
    """The plain get_ohlcv stays fail-loud on any gap failure — the HTTP route +
    backtests want a loud error, not a silent partial."""
    start, end = _seed_three_gap_cache(repo)
    yahoo = _scripted_yahoo(
        [[_row("2026-01-15")], _rate_limit_error(), [_row("2026-03-20")]],
    )
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)

    with pytest.raises(RateLimitedError):
        provider.get_ohlcv("AAPL", "1d", start, end)


def test_get_ohlcv_with_status_all_gaps_fail_raises(repo: BarRepository) -> None:
    start, end = _seed_three_gap_cache(repo)
    yahoo = _scripted_yahoo([_rate_limit_error(), _rate_limit_error(), _rate_limit_error()])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)

    with pytest.raises(RateLimitedError):
        provider.get_ohlcv_with_status("AAPL", "1d", start, end)


def test_get_ohlcv_with_status_full_cache_hit_is_clean(repo: BarRepository) -> None:
    warming_rows = _daily_rows(datetime(2026, 4, 1, tzinfo=UTC), 30)
    yahoo_warmer, _warm = _yahoo_with_call_log(warming_rows)
    DefaultMarketDataProvider(yahoo=yahoo_warmer, bar_repository=repo).get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )

    def explode(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        raise AssertionError("full cache hit must not fetch")

    provider = DefaultMarketDataProvider(yahoo=YahooAdapter(fetcher=explode), bar_repository=repo)
    result = provider.get_ohlcv_with_status(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )

    assert result.partial_reason is None
    assert result.message is None
    assert len(result.bars) == 30
