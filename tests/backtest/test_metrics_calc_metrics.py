"""Tests for `_calc_metrics`.

Per Plan 0008 phase 1 done-when:

- Hand-built equity curve [10000, 10500, 11000, 10500, 11000] with one closed
  win → total_return=0.10, trade_count=1, win_rate=1.0,
  max_drawdown ≈ -0.04545, max_drawdown_duration_bars=1. Sharpe annualized
  with sqrt(252) for timeframe="1d".
- NaN-safe: zero trades → win_rate=0.0; flat curve → sharpe=0.0.
- Unknown timeframe → raises `UnknownTimeframeError`.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import isclose

import pytest

from market_analyser.backtest import (
    EquityPoint,
    Trade,
    UnknownTimeframeError,
    _calc_metrics,
)


def _curve(equities: Sequence[float]) -> list[EquityPoint]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [EquityPoint(ts=start + timedelta(days=i), equity=eq) for i, eq in enumerate(equities)]


def test_total_return_from_equity_endpoints() -> None:
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_500.0, 11_000.0, 10_500.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert isclose(metrics.total_return, 0.10, abs_tol=1e-9)


def test_trade_count_and_win_rate_for_one_winning_trade() -> None:
    metrics = _calc_metrics(
        trades=[
            Trade(
                entry_bar_index=1,
                exit_bar_index=3,
                entry_price=100.0,
                exit_price=110.0,
                kind="long",
            )
        ],
        equity_curve=_curve([10_000.0, 10_500.0, 11_000.0, 10_500.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 1
    assert metrics.win_rate == 1.0


def test_max_drawdown_depth_and_duration() -> None:
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_500.0, 11_000.0, 10_500.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    # peak=11_000 at idx 2; trough=10_500 at idx 3; recovery at idx 4.
    assert metrics.max_drawdown < 0
    assert isclose(metrics.max_drawdown, (10_500.0 - 11_000.0) / 11_000.0, abs_tol=1e-9)
    # Duration: idx 3 is the only bar strictly below the peak → 1 bar.
    assert metrics.max_drawdown_duration_bars == 1


def test_sharpe_uses_sqrt_252_for_1d() -> None:
    equities = [10_000.0, 10_500.0, 11_000.0, 10_500.0, 11_000.0]
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve(equities),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    returns = [equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities))]
    expected_sharpe = statistics.fmean(returns) / statistics.stdev(returns) * math.sqrt(252)
    assert isclose(metrics.sharpe, expected_sharpe, abs_tol=1e-9)


def test_zero_trades_win_rate_is_zero_not_nan() -> None:
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_000.0, 10_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 0
    assert metrics.win_rate == 0.0
    assert not math.isnan(metrics.win_rate)


def test_flat_curve_sharpe_is_zero_not_nan() -> None:
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_000.0, 10_000.0, 10_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.sharpe == 0.0
    assert not math.isnan(metrics.sharpe)


def test_unknown_timeframe_raises() -> None:
    with pytest.raises(UnknownTimeframeError) as excinfo:
        _calc_metrics(
            trades=[],
            equity_curve=_curve([10_000.0, 10_500.0, 11_000.0]),
            initial_capital=10_000.0,
            timeframe="5m",
        )
    assert "5m" in str(excinfo.value)


def test_dangling_trade_does_not_count_toward_win_rate() -> None:
    metrics = _calc_metrics(
        trades=[
            Trade(
                entry_bar_index=1,
                exit_bar_index=None,
                entry_price=100.0,
                exit_price=None,
                kind="long",
            )
        ],
        equity_curve=_curve([10_000.0, 10_500.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 0
    assert metrics.win_rate == 0.0


def test_losing_trade_drops_win_rate_to_zero() -> None:
    metrics = _calc_metrics(
        trades=[
            Trade(
                entry_bar_index=1,
                exit_bar_index=3,
                entry_price=100.0,
                exit_price=90.0,
                kind="long",
            )
        ],
        equity_curve=_curve([10_000.0, 9_500.0, 9_200.0, 9_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 1
    assert metrics.win_rate == 0.0


def test_buy_and_hold_return_passthrough() -> None:
    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
        buy_and_hold_return=0.42,
    )
    assert metrics.buy_and_hold_return == 0.42


def test_sharpe_annualization_changes_with_timeframe() -> None:
    equities = [10_000.0, 10_100.0, 9_950.0, 10_200.0, 10_400.0]
    m_1d = _calc_metrics(
        trades=[],
        equity_curve=_curve(equities),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    m_1h = _calc_metrics(
        trades=[],
        equity_curve=_curve(equities),
        initial_capital=10_000.0,
        timeframe="1h",
    )
    # ratio = sqrt(252*24) / sqrt(252) = sqrt(24)
    ratio = m_1h.sharpe / m_1d.sharpe
    assert isclose(ratio, math.sqrt(24), abs_tol=1e-9)
