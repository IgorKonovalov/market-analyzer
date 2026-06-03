"""Tests for `walk_forward` (Plan 0020 phase 2).

Done-when coverage:

- Fold partitioning math (`_fold_bounds`): contiguous, non-overlapping,
  bar counts sum to the series length.
- Anti-lookahead across folds: fold k's first bar strictly follows fold
  k-1's last; each fold's metrics equal a direct `run()` on that window
  alone (so no fold reads outside its window).
- Per-fold + aggregate: mean/std of total_return and sharpe match
  `statistics` over the per-fold values within 1e-9.
- Determinism: two calls produce byte-identical dumps.
- Degenerate splits: n_splits < 1 and n_splits > bar count raise
  `WalkForwardConfigError`; tiny-but-non-empty folds produce empty-but-
  valid results, not crashes.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import isclose

import pytest

from market_analyser.backtest import (
    WalkForwardConfigError,
    WalkForwardResult,
    run,
    walk_forward,
)
from market_analyser.backtest.walk_forward import _fold_bounds
from market_analyser.data.types import Bar
from market_analyser.strategies import rsi as rsi_strategy

_RSI_PARAMS = {"period": 14, "oversold": 30.0, "overbought": 70.0}


def _make_bars(n: int, *, symbol: str = "AAPL", timeframe: str = "1d") -> list[Bar]:
    """Synthetic sine-plus-trend bars (same shape as the golden generator).

    The 10-bar sine period plus a slow uptrend guarantees RSI cross
    events, so folds over a few hundred bars produce real trades.
    """

    start = datetime(2025, 1, 1, tzinfo=UTC)
    closes = [100.0 + math.sin(i / 10.0) * 12.0 + (i / n) * 18.0 for i in range(n)]
    bars: list[Bar] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        bar_open = prev_close
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                event_ts=start + timedelta(days=i),
                open=bar_open,
                high=max(bar_open, close),
                low=min(bar_open, close),
                close=close,
                volume=0.0,
                source="fixture",
            )
        )
        prev_close = close
    return bars


def _wf(bars: Sequence[Bar], n_splits: int) -> WalkForwardResult:
    return walk_forward(rsi_strategy, bars, _RSI_PARAMS, timeframe="1d", n_splits=n_splits)


# --- Fold partitioning -------------------------------------------------------


def test_fold_bounds_even_division() -> None:
    bounds = _fold_bounds(400, 4)
    assert bounds == [(0, 100), (100, 200), (200, 300), (300, 400)]
    # Contiguous, non-overlapping, covers the whole series.
    assert bounds[0][0] == 0
    assert bounds[-1][1] == 400
    for prev, nxt in pairwise(bounds):
        assert prev[1] == nxt[0]
    assert sum(end - start for start, end in bounds) == 400


def test_fold_bounds_uneven_division_front_loads_remainder() -> None:
    # 403 = 4 * 100 + 3 → first three folds get 101, last gets 100.
    bounds = _fold_bounds(403, 4)
    assert [end - start for start, end in bounds] == [101, 101, 101, 100]
    assert sum(end - start for start, end in bounds) == 403
    for prev, nxt in pairwise(bounds):
        assert prev[1] == nxt[0]


def test_fold_count_matches_n_splits() -> None:
    result = _wf(_make_bars(400), 4)
    assert result.n_splits == 4
    assert len(result.folds) == 4
    assert [fold.fold_index for fold in result.folds] == [0, 1, 2, 3]


# --- Anti-lookahead ----------------------------------------------------------


def test_folds_are_contiguous_and_strictly_increasing_in_time() -> None:
    bars = _make_bars(400)
    result = _wf(bars, 4)
    # Fold k's first bar strictly follows fold k-1's last bar.
    for prev, nxt in pairwise(result.folds):
        assert nxt.range_start > prev.range_end
    # Window endpoints line up with the documented [0,100),[100,200),... math.
    expected_bounds = _fold_bounds(len(bars), 4)
    for fold, (start, end) in zip(result.folds, expected_bounds, strict=True):
        assert fold.range_start == bars[start].event_ts
        assert fold.range_end == bars[end - 1].event_ts


def test_each_fold_equals_direct_run_on_its_window_only() -> None:
    """Per-fold metrics == `run()` on the isolated slice → no cross-fold leak."""

    bars = _make_bars(400)
    result = _wf(bars, 4)
    for fold, (start, end) in zip(result.folds, _fold_bounds(len(bars), 4), strict=True):
        direct = run(rsi_strategy, bars[start:end], _RSI_PARAMS, timeframe="1d")
        assert fold.metrics.model_dump() == direct.metrics.model_dump()
        assert fold.trade_count == len(direct.trades)


# --- Aggregate + baseline ----------------------------------------------------


def test_aggregate_matches_statistics_over_folds() -> None:
    bars = _make_bars(400)
    result = _wf(bars, 4)
    total_returns = [fold.metrics.total_return for fold in result.folds]
    sharpes = [fold.metrics.sharpe for fold in result.folds]

    assert result.aggregate["total_return_mean"] is not None
    assert isclose(
        result.aggregate["total_return_mean"], statistics.fmean(total_returns), abs_tol=1e-9
    )
    assert result.aggregate["total_return_std"] is not None
    assert isclose(
        result.aggregate["total_return_std"], statistics.stdev(total_returns), abs_tol=1e-9
    )
    assert result.aggregate["sharpe_mean"] is not None
    assert isclose(result.aggregate["sharpe_mean"], statistics.fmean(sharpes), abs_tol=1e-9)
    assert result.aggregate["sharpe_std"] is not None
    assert isclose(result.aggregate["sharpe_std"], statistics.stdev(sharpes), abs_tol=1e-9)


def test_full_run_baseline_equals_run_over_all_bars() -> None:
    bars = _make_bars(400)
    result = _wf(bars, 4)
    baseline = run(rsi_strategy, bars, _RSI_PARAMS, timeframe="1d")
    assert result.full_run_baseline.model_dump() == baseline.metrics.model_dump()


def test_single_fold_has_no_std() -> None:
    result = _wf(_make_bars(200), 1)
    assert len(result.folds) == 1
    assert result.aggregate["total_return_std"] is None
    assert result.aggregate["sharpe_std"] is None
    assert result.aggregate["total_return_mean"] is not None
    assert result.aggregate["sharpe_mean"] is not None


# --- Determinism -------------------------------------------------------------


def test_determinism_identical_inputs_identical_result() -> None:
    bars = _make_bars(400)
    a = _wf(bars, 4)
    b = _wf(bars, 4)
    # WalkForwardResult stores no run_id / timestamps, so dumps are equal outright.
    assert a.model_dump() == b.model_dump()


# --- Degenerate splits -------------------------------------------------------


def test_n_splits_below_one_raises() -> None:
    with pytest.raises(WalkForwardConfigError):
        _wf(_make_bars(100), 0)


def test_n_splits_exceeding_bar_count_raises() -> None:
    bars = _make_bars(10)
    with pytest.raises(WalkForwardConfigError) as excinfo:
        _wf(bars, 11)
    assert "exceeds bar count" in str(excinfo.value)


def test_empty_bars_raises() -> None:
    with pytest.raises(ValueError):
        walk_forward(rsi_strategy, [], _RSI_PARAMS, timeframe="1d", n_splits=2)


def test_tiny_folds_produce_empty_but_valid_results() -> None:
    """Folds too short for RSI to warm up yield zero closed trades, not a crash."""

    bars = _make_bars(20)
    result = _wf(bars, 10)  # 10 folds of 2 bars each
    assert len(result.folds) == 10
    for fold in result.folds:
        assert fold.metrics.trade_count == 0
        # Degenerate metrics follow the ADR-0024 None / 0.0 conventions.
        assert fold.metrics.profit_factor is None
        assert fold.metrics.sortino == 0.0


def test_n_splits_equals_bar_count_one_bar_per_fold() -> None:
    bars = _make_bars(12)
    result = _wf(bars, 12)
    assert len(result.folds) == 12
    for fold, bar in zip(result.folds, bars, strict=True):
        assert fold.range_start == bar.event_ts
        assert fold.range_end == bar.event_ts
        assert fold.metrics.trade_count == 0
