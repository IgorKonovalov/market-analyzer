"""Tests for `_apply_costs`.

Per Plan 0008 phase 1 done-when:

- Empty trades + zero costs return `[]`.
- One closed long trade with `c=10`, `s=5` (total 15 bps):
    entry adjusted up by `1 + 15/10_000` (= 100.15 from 100.0).
    exit  adjusted down by `1 - 15/10_000` (= 109.835 from 110.0).
- A dangling trade (`exit_price=None`) has entry adjusted, exit untouched.
"""

from __future__ import annotations

from math import isclose

from market_analyser.backtest import Trade, _apply_costs


def test_empty_trades_with_zero_costs_return_empty() -> None:
    assert _apply_costs([], commission_bps=0.0, slippage_bps=0.0) == []


def test_empty_trades_with_nonzero_costs_return_empty() -> None:
    assert _apply_costs([], commission_bps=10.0, slippage_bps=5.0) == []


def test_closed_long_trade_applies_bps_per_side() -> None:
    trade = Trade(
        entry_bar_index=5,
        exit_bar_index=10,
        entry_price=100.0,
        exit_price=110.0,
        kind="long",
    )
    [adjusted] = _apply_costs([trade], commission_bps=10.0, slippage_bps=5.0)
    assert isclose(adjusted.entry_price, 100.15, abs_tol=1e-9)
    assert adjusted.exit_price is not None
    assert isclose(adjusted.exit_price, 109.835, abs_tol=1e-9)
    assert adjusted.entry_bar_index == 5
    assert adjusted.exit_bar_index == 10
    assert adjusted.kind == "long"


def test_dangling_trade_adjusts_entry_only() -> None:
    trade = Trade(
        entry_bar_index=5,
        exit_bar_index=None,
        entry_price=200.0,
        exit_price=None,
        kind="long",
    )
    [adjusted] = _apply_costs([trade], commission_bps=10.0, slippage_bps=5.0)
    assert isclose(adjusted.entry_price, 200.0 * (1.0 + 15.0 / 10_000.0), abs_tol=1e-9)
    assert adjusted.exit_price is None
    assert adjusted.exit_bar_index is None


def test_zero_costs_passthrough_preserves_prices() -> None:
    trade = Trade(
        entry_bar_index=5,
        exit_bar_index=10,
        entry_price=100.0,
        exit_price=110.0,
        kind="long",
    )
    [adjusted] = _apply_costs([trade], commission_bps=0.0, slippage_bps=0.0)
    assert adjusted.entry_price == 100.0
    assert adjusted.exit_price == 110.0


def test_multiple_trades_each_adjusted_independently() -> None:
    trades = [
        Trade(entry_bar_index=1, exit_bar_index=2, entry_price=100.0, exit_price=110.0, kind="long"),
        Trade(entry_bar_index=5, exit_bar_index=8, entry_price=200.0, exit_price=180.0, kind="long"),
    ]
    [t1, t2] = _apply_costs(trades, commission_bps=10.0, slippage_bps=5.0)
    assert isclose(t1.entry_price, 100.15, abs_tol=1e-9)
    assert t1.exit_price is not None
    assert isclose(t1.exit_price, 109.835, abs_tol=1e-9)
    assert isclose(t2.entry_price, 200.3, abs_tol=1e-9)
    assert t2.exit_price is not None
    assert isclose(t2.exit_price, 180.0 * (1.0 - 15.0 / 10_000.0), abs_tol=1e-9)
