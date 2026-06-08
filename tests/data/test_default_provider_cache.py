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
from market_analyser.data.errors import HistoryExceededError, RateLimitedError
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

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        calls.append(symbol)
        return rows

    return YahooAdapter(fetcher=fetcher), calls


def _yahoo_with_call_log(
    rows: list[dict[str, Any]],
) -> tuple[YahooAdapter, list[tuple[str, str]]]:
    """Fetcher that records (symbol, interval) per call. The adapter still filters
    the response to [start, end], so we can return a generous superset of bars
    and trust the adapter to discard the rest."""
    calls: list[tuple[str, str]] = []

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        calls.append((symbol, interval))
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
    assert warm_calls == [("AAPL", "1d")]

    # Now build a fresh provider whose adapter would scream if called.
    def explode(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        raise AssertionError(
            f"adapter must not be called for fully cached coverage: "
            f"{symbol} [{start.isoformat()}, {end.isoformat()}]",
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
    def explode(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
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

    def explode(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
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

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
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

    def explode(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
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


# -- Plan 0025 phase 1: coverage math uses the registry bar duration ----------


def _tf_bar(timeframe: str, ts: datetime) -> Bar:
    return Bar(
        symbol="AAPL",
        timeframe=timeframe,
        event_ts=ts,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000.0,
        source="yahoo",
    )


def test_coverage_15m_detects_a_sub_day_hole(repo: BarRepository) -> None:
    # 15m threshold = 15m * 10 = 2.5h. Bars 2h apart bound the present regions
    # (2h < 2.5h, not a hole); the 4h interior gap (>= 2.5h) is the one real hole.
    # Under the OLD flat 10-day threshold a 4h gap would be skipped entirely —
    # so this exactly-one-gap result proves the threshold now scales with the
    # registry bar duration.
    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    present = [t0, t0 + timedelta(hours=2), t0 + timedelta(hours=4)]
    present += [t0 + timedelta(hours=8), t0 + timedelta(hours=10), t0 + timedelta(hours=12)]
    repo.upsert_bars([_tf_bar("15m", ts) for ts in present])
    provider = DefaultMarketDataProvider(bar_repository=repo)

    cov = provider.coverage("AAPL", "15m", t0, t0 + timedelta(hours=12))

    assert cov.gaps == [(t0 + timedelta(hours=4), t0 + timedelta(hours=8))]


def test_coverage_1w_uses_weekly_threshold(repo: BarRepository) -> None:
    # 1w threshold = 7d * 10 = 70d. Monthly-spaced present bars (~31d < 70d) are
    # within tolerance; only the ~242-day hole counts. Distinguishes the registry
    # duration from any timeframe-blind flat threshold.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 12, 31, tzinfo=UTC)
    cached_ts = [
        start,
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
        end,
    ]
    repo.upsert_bars([_tf_bar("1w", ts) for ts in cached_ts])
    provider = DefaultMarketDataProvider(bar_repository=repo)

    cov = provider.coverage("AAPL", "1w", start, end)

    assert cov.gaps == [(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))]


# -- Plan 0050 phase 4.5: monthly is variable-duration; the gap threshold is tight --


def _monthly_first_of_month(year_start: int, n_months: int) -> list[datetime]:
    """First-of-month UTC timestamps for `n_months` consecutive months starting at
    January of `year_start`."""
    out: list[datetime] = []
    y, m = year_start, 1
    for _ in range(n_months):
        out.append(datetime(y, m, 1, tzinfo=UTC))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def test_coverage_1mo_no_false_gap_across_a_full_year_including_february(
    repo: BarRepository,
) -> None:
    # 1mo threshold = 1.5 * 31d = ~46.5d. Every month-to-month step is 28-31d
    # (< 46.5d), INCLUDING the 28-day February steps — so a fully-present 24-month
    # span reports NO gaps. Under the old 10x tolerance this was 310d (also no
    # false gap), but the tight bound is what lets the NEXT test flag a real hole.
    months = _monthly_first_of_month(2025, 24)  # spans Feb 2025 and Feb 2026
    repo.upsert_bars([_tf_bar("1mo", ts) for ts in months])
    provider = DefaultMarketDataProvider(bar_repository=repo)

    cov = provider.coverage("AAPL", "1mo", months[0], months[-1])

    assert cov.gaps == []


def test_coverage_1mo_flags_a_single_omitted_month(repo: BarRepository) -> None:
    # Drop one interior month (April 2025): the surrounding bars are then ~59-61d
    # apart (Mar 1 -> May 1 = 61d), which exceeds the ~46.5d threshold and is
    # flagged. The old 310d tolerance would have masked it entirely — this is the
    # bug ADR-0047's tight-bound reading fixes.
    months = _monthly_first_of_month(2025, 24)
    omitted = datetime(2025, 4, 1, tzinfo=UTC)
    kept = [ts for ts in months if ts != omitted]
    repo.upsert_bars([_tf_bar("1mo", ts) for ts in kept])
    provider = DefaultMarketDataProvider(bar_repository=repo)

    cov = provider.coverage("AAPL", "1mo", months[0], months[-1])

    assert cov.gaps == [(datetime(2025, 3, 1, tzinfo=UTC), datetime(2025, 5, 1, tzinfo=UTC))]


# -- Plan 0025 phase 2: 4h is derived on read from a single 1h fetch ----------


def _intraday_row(dt_str: str, *, close: float) -> dict[str, Any]:
    return {
        "date": dt_str,
        "open": 100.0,
        "high": close + 1.0,
        "low": 99.0,
        "close": close,
        "volume": 1_000.0,
    }


def test_get_ohlcv_4h_resamples_from_one_1h_fetch(repo: BarRepository) -> None:
    # A 4h request fetches the native 1h base over the window (one fetch, interval
    # "1h" — never a "4h" upstream call) and returns the resample. The 04:00-08:00
    # and 08:00-12:00 1h bars collapse into two 4h buckets.
    intervals: list[str] = []

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        intervals.append(interval)
        return [
            _intraday_row(f"2026-01-05 {hour:02d}:00", close=100.0 + hour) for hour in range(4, 12)
        ]

    provider = DefaultMarketDataProvider(yahoo=YahooAdapter(fetcher=fetcher), bar_repository=repo)

    bars = provider.get_ohlcv(
        "AAPL",
        "4h",
        datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
    )

    assert intervals == ["1h"], f"expected exactly one 1h fetch, got {intervals}"
    assert [b.event_ts for b in bars] == [
        datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
    ]
    assert all(b.timeframe == "4h" for b in bars)
    # close of each 4h bar is the last 1h close in the bucket: 07:00 -> 107, 11:00 -> 111.
    assert bars[0].close == 107.0
    assert bars[1].close == 111.0


def test_get_ohlcv_4h_with_status_carries_base_partial_reason(repo: BarRepository) -> None:
    # The partial-surfacing path also routes through the 1h base: a 4h request
    # resamples the base result and carries its partial_reason/message through.
    start = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
    end = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    yahoo = _scripted_yahoo([_rate_limit_error()])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)

    with pytest.raises(RateLimitedError):
        # Total failure on the only (1h base) gap stays loud through the resample.
        provider.get_ohlcv_with_status("AAPL", "4h", start, end)


# -- Plan 0025 phase 3: per-timeframe history caps surface honestly -----------


def _no_fetch_provider(repo: BarRepository) -> DefaultMarketDataProvider:
    def explode(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        raise AssertionError("over-history request must not reach the adapter")

    return DefaultMarketDataProvider(yahoo=YahooAdapter(fetcher=explode), bar_repository=repo)


def test_get_ohlcv_with_status_history_cap_surfaces_honest_partial(repo: BarRepository) -> None:
    # 15m history is ~60 days; a 90-day window exceeds it. The result is the
    # cache-honest shape — partial_reason set + a human message — not a crash and
    # not a misleading empty success (partial_reason None). No fetch is attempted.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)  # ~90 days

    result = _no_fetch_provider(repo).get_ohlcv_with_status("AAPL", "15m", start, end)

    assert result.partial_reason == "history_exceeded"
    assert result.message is not None and "history" in result.message.lower()
    assert result.bars == []  # nothing cached, but honestly empty *with* a reason


def test_get_ohlcv_history_cap_raises_loud(repo: BarRepository) -> None:
    # The fail-loud path raises the typed, non-retryable error for the same window.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 4, 1, tzinfo=UTC)

    with pytest.raises(HistoryExceededError, match="history"):
        _no_fetch_provider(repo).get_ohlcv("AAPL", "15m", start, end)


def test_get_ohlcv_within_history_cap_is_not_flagged(repo: BarRepository) -> None:
    # A 15m window inside the ~60-day cap proceeds normally (no history_exceeded).
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    yahoo, _calls = _yahoo_with_call_log([_intraday_row("2026-01-01 00:00", close=100.0)])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)

    result = provider.get_ohlcv_with_status("AAPL", "15m", start, end)

    assert result.partial_reason is None
