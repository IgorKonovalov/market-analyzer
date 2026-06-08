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
from market_analyser.backtest.metrics import _TIMEFRAME_BARS_PER_YEAR


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


@pytest.mark.parametrize(
    ("timeframe", "expected_bars_per_year"),
    [
        ("15m", 252 * 24 * 4),  # 24192
        ("1h", 252 * 24),  # 6048
        ("4h", 252 * 6),  # 1512
        ("1d", 252),
        ("1w", 252 // 7),  # 36
    ],
)
def test_bars_per_year_for_each_supported_timeframe(
    timeframe: str, expected_bars_per_year: int
) -> None:
    """Every data-layer timeframe annualizes on the 252-day/24h basis (Plan 0050 ph1)."""
    assert _TIMEFRAME_BARS_PER_YEAR[timeframe] == expected_bars_per_year


def test_metrics_table_keys_match_data_registry() -> None:
    """The annualization table covers exactly the supported timeframe set, no more."""
    from market_analyser.data.timeframes import registry_timeframes

    assert set(_TIMEFRAME_BARS_PER_YEAR) == set(registry_timeframes())


def test_added_timeframes_annualize_finite_and_correctly_scaled() -> None:
    """A 4h and a 1w run return finite Sharpe, scaled by sqrt(bars_per_year).

    Same equity curve under two timeframes differs only by the annualization
    factor, so the Sharpe ratio between them is sqrt(bpy_a / bpy_b) — this pins
    'correctly-scaled' rather than merely 'finite, no UnknownTimeframeError'.
    """
    curve = _curve([10_000.0, 10_500.0, 11_000.0, 10_500.0, 11_000.0])

    sharpe_1d = _calc_metrics(
        trades=[], equity_curve=curve, initial_capital=10_000.0, timeframe="1d"
    ).sharpe
    sharpe_4h = _calc_metrics(
        trades=[], equity_curve=curve, initial_capital=10_000.0, timeframe="4h"
    ).sharpe
    sharpe_1w = _calc_metrics(
        trades=[], equity_curve=curve, initial_capital=10_000.0, timeframe="1w"
    ).sharpe

    for sharpe in (sharpe_4h, sharpe_1w):
        assert math.isfinite(sharpe)

    assert isclose(
        sharpe_4h / sharpe_1d,
        math.sqrt((252 * 6) / 252),
        rel_tol=1e-9,
    )
    assert isclose(
        sharpe_1w / sharpe_1d,
        math.sqrt((252 // 7) / 252),
        rel_tol=1e-9,
    )


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


# --- Extended metrics (ADR-0024) ---------------------------------------------


def _two_win_one_loss_trades() -> list[Trade]:
    """Three closed long trades: +10%, +20%, -10% (entry-to-exit, pre-curve)."""

    return [
        Trade(
            entry_bar_index=1, exit_bar_index=2, entry_price=100.0, exit_price=110.0, kind="long"
        ),
        Trade(
            entry_bar_index=3, exit_bar_index=4, entry_price=100.0, exit_price=120.0, kind="long"
        ),
        Trade(entry_bar_index=5, exit_bar_index=6, entry_price=100.0, exit_price=90.0, kind="long"),
    ]


def test_extended_metrics_on_two_win_one_loss_fixture() -> None:
    """Every new metric equals its hand-worked / ADR-formula value within 1e-9.

    Per-trade returns are [+0.10, +0.20, -0.10] (independent of the equity
    curve). The curve [10000, 11000, 9900, 12000] gives total_return=0.20
    and max_drawdown=-0.10 (the dip from the 11000 peak to 9900).
    """

    equities = [10_000.0, 11_000.0, 9_900.0, 12_000.0]
    metrics = _calc_metrics(
        trades=_two_win_one_loss_trades(),
        equity_curve=_curve(equities),
        initial_capital=10_000.0,
        timeframe="1d",
    )

    # Per-trade metrics — hand-worked from [+0.10, +0.20, -0.10].
    assert metrics.trade_count == 3
    assert isclose(metrics.win_rate, 2.0 / 3.0, abs_tol=1e-9)
    assert metrics.profit_factor is not None
    assert isclose(metrics.profit_factor, (0.10 + 0.20) / 0.10, abs_tol=1e-9)  # = 3.0
    assert metrics.expectancy is not None
    assert isclose(metrics.expectancy, 0.20 / 3.0, abs_tol=1e-9)  # mean of the three returns
    assert metrics.best_trade_return is not None
    assert isclose(metrics.best_trade_return, 0.20, abs_tol=1e-9)
    assert metrics.worst_trade_return is not None
    assert isclose(metrics.worst_trade_return, -0.10, abs_tol=1e-9)

    # Calmar — annualized_total_return / |max_drawdown| (ADR-0024).
    assert isclose(metrics.max_drawdown, -0.10, abs_tol=1e-9)
    n_bars = len(equities)
    annualized = (12_000.0 / 10_000.0) ** (252 / n_bars) - 1.0
    assert metrics.calmar is not None
    assert isclose(metrics.calmar, annualized / 0.10, rel_tol=1e-9)

    # Sortino — mean(returns) / stdev(downside) * sqrt(252) (ADR-0024).
    returns = [equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities))]
    downside = [min(r, 0.0) for r in returns]
    expected_sortino = statistics.fmean(returns) / statistics.stdev(downside) * math.sqrt(252)
    assert isclose(metrics.sortino, expected_sortino, abs_tol=1e-9)


def test_zero_trades_extended_metrics_are_none_and_sortino_zero() -> None:
    """No closed trades + flat curve: ratios/per-trade are None; sortino 0.0."""

    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_000.0, 10_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 0
    assert metrics.profit_factor is None
    assert metrics.expectancy is None
    assert metrics.best_trade_return is None
    assert metrics.worst_trade_return is None
    # Sortino is Sharpe-family — flat curve has no downside, so 0.0 not None.
    assert metrics.sortino == 0.0
    assert not math.isnan(metrics.sortino)
    # Flat curve never dips → max_drawdown 0.0 → Calmar undefined → None.
    assert metrics.calmar is None


def test_all_wins_profit_factor_is_none() -> None:
    """Zero losing trades → profit_factor None (no gross loss to divide by)."""

    metrics = _calc_metrics(
        trades=[
            Trade(
                entry_bar_index=1,
                exit_bar_index=2,
                entry_price=100.0,
                exit_price=110.0,
                kind="long",
            ),
            Trade(
                entry_bar_index=3,
                exit_bar_index=4,
                entry_price=100.0,
                exit_price=105.0,
                kind="long",
            ),
        ],
        equity_curve=_curve([10_000.0, 11_000.0, 11_500.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.trade_count == 2
    assert metrics.profit_factor is None
    # Expectancy / best / worst are still defined (all positive here).
    assert metrics.expectancy is not None
    assert isclose(metrics.expectancy, (0.10 + 0.05) / 2.0, abs_tol=1e-9)
    assert metrics.best_trade_return is not None
    assert isclose(metrics.best_trade_return, 0.10, abs_tol=1e-9)
    assert metrics.worst_trade_return is not None
    assert isclose(metrics.worst_trade_return, 0.05, abs_tol=1e-9)


def test_calmar_none_when_curve_never_dips() -> None:
    """Monotonic-up curve → max_drawdown 0.0 → Calmar None (not 0.0)."""

    metrics = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_500.0, 11_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    assert metrics.max_drawdown == 0.0
    assert metrics.calmar is None
    # All-positive returns → no downside → sortino 0.0.
    assert metrics.sortino == 0.0


def test_no_extended_metric_is_ever_nan() -> None:
    """Across both a normal and a degenerate run, no float metric is NaN."""

    normal = _calc_metrics(
        trades=_two_win_one_loss_trades(),
        equity_curve=_curve([10_000.0, 11_000.0, 9_900.0, 12_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    degenerate = _calc_metrics(
        trades=[],
        equity_curve=_curve([10_000.0, 10_000.0]),
        initial_capital=10_000.0,
        timeframe="1d",
    )
    for metrics in (normal, degenerate):
        for value in (
            metrics.sharpe,
            metrics.sortino,
            metrics.calmar,
            metrics.profit_factor,
            metrics.expectancy,
            metrics.best_trade_return,
            metrics.worst_trade_return,
        ):
            assert value is None or not math.isnan(value)
