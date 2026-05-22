"""Tests for `_buy_and_hold_return`.

Per Plan 0008 phase 1 done-when:

- first close=100, last close=110 → 0.10 exactly.
- first close=100, last close=80  → -0.20.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import isclose

import pytest

from market_analyser.backtest import _buy_and_hold_return
from market_analyser.data.types import Bar


def _bars(closes: Sequence[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=0.0,
            source="fixture",
        )
        for i, c in enumerate(closes)
    ]


def test_positive_buy_and_hold_return() -> None:
    bars = _bars([100.0, 105.0, 108.0, 110.0])
    assert isclose(_buy_and_hold_return(bars, initial_capital=10_000.0), 0.10, abs_tol=1e-12)


def test_negative_buy_and_hold_return() -> None:
    bars = _bars([100.0, 95.0, 90.0, 80.0])
    assert isclose(_buy_and_hold_return(bars, initial_capital=10_000.0), -0.20, abs_tol=1e-12)


def test_flat_buy_and_hold_return_is_zero() -> None:
    bars = _bars([100.0, 100.0, 100.0])
    assert _buy_and_hold_return(bars, initial_capital=10_000.0) == 0.0


def test_initial_capital_does_not_affect_result() -> None:
    bars = _bars([100.0, 110.0])
    a = _buy_and_hold_return(bars, initial_capital=10_000.0)
    b = _buy_and_hold_return(bars, initial_capital=1_000_000.0)
    assert a == b


def test_empty_bars_raises() -> None:
    with pytest.raises(ValueError):
        _buy_and_hold_return([], initial_capital=10_000.0)
