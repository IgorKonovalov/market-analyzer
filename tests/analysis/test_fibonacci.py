"""Plan 0092 phase 1 done-when: `analysis/fibonacci.py`.

- The retracement / extension level prices match a hand-computed grid for a known
  high/low within 1e-9.
- The `direction` is correct for an up-swing (low before high -> bullish) vs a
  down-swing (high before low -> bearish) anchor, and the grid orientation flips
  with it.
- `dominant_swing` auto-anchors to the intended (largest) confirmed leg on a
  fixture, and is trailing: an unconfirmed swing is not picked early, and
  appending future bars leaves an already-reported grid unchanged.
- `FibonacciLevels` rejects an extra field (`extra="forbid"`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.fibonacci import (
    EXTENSION_RATIOS,
    RETRACEMENT_RATIOS,
    dominant_swing,
    fibonacci_extension,
    fibonacci_retracement,
)
from market_analyser.analysis.types import FibonacciLevels, PivotPoint
from market_analyser.data.types import Bar

_TOL = 1e-9
_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _anchor(day: int, price: float) -> PivotPoint:
    return PivotPoint(ts=_T0 + timedelta(days=day), price=price)


def _bar(i: int, *, h: float, low: float) -> Bar:
    mid = (h + low) / 2.0
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=_T0 + timedelta(days=i),
        open=mid,
        high=h,
        low=low,
        close=mid,
        volume=1000.0,
        source="synthetic",
    )


def _swing_fixture(n: int = 30) -> list[Bar]:
    """Flat 99..101 band (no strict pivots) with one clear swing low (50.0 at bar 6)
    and one clear swing high (150.0 at bar 14) — a single dominant 50<->150 leg."""

    bars: list[Bar] = []
    for i in range(n):
        h, low = 101.0, 99.0
        if i == 6:
            h, low = 91.0, 50.0
        if i == 14:
            h, low = 150.0, 109.0
        bars.append(_bar(i, h=h, low=low))
    return bars


# --------------------------------------------------------------------------- #
# Hand-computed grids                                                          #
# --------------------------------------------------------------------------- #


def test_retracement_bullish_matches_hand_grid() -> None:
    fib = fibonacci_retracement(_anchor(14, 150.0), _anchor(6, 50.0))  # low before high
    assert fib.kind == "retracement"
    assert fib.direction == "bullish"
    # up-swing: level = high - r*(high-low), span=100
    assert fib.levels["0.236"] == pytest.approx(126.4, abs=_TOL)
    assert fib.levels["0.382"] == pytest.approx(111.8, abs=_TOL)
    assert fib.levels["0.5"] == pytest.approx(100.0, abs=_TOL)
    assert fib.levels["0.618"] == pytest.approx(88.2, abs=_TOL)
    assert fib.levels["0.786"] == pytest.approx(71.4, abs=_TOL)
    assert set(fib.levels) == {str(r) for r in RETRACEMENT_RATIOS}


def test_retracement_bearish_flips_orientation() -> None:
    # high before low -> down-swing; level = low + r*(high-low)
    fib = fibonacci_retracement(_anchor(6, 150.0), _anchor(14, 50.0))
    assert fib.direction == "bearish"
    assert fib.levels["0.382"] == pytest.approx(88.2, abs=_TOL)
    assert fib.levels["0.5"] == pytest.approx(100.0, abs=_TOL)
    assert fib.levels["0.618"] == pytest.approx(111.8, abs=_TOL)


def test_extension_bullish_matches_hand_grid() -> None:
    fib = fibonacci_extension(_anchor(14, 150.0), _anchor(6, 50.0), _anchor(20, 100.0))
    assert fib.kind == "extension"
    assert fib.direction == "bullish"
    # up-swing: level = pullback + r*(high-low), span=100, pullback=100
    assert fib.levels["1.272"] == pytest.approx(227.2, abs=_TOL)
    assert fib.levels["1.618"] == pytest.approx(261.8, abs=_TOL)
    assert fib.levels["2.0"] == pytest.approx(300.0, abs=_TOL)
    assert fib.levels["2.618"] == pytest.approx(361.8, abs=_TOL)
    assert set(fib.levels) == {str(r) for r in EXTENSION_RATIOS}


def test_extension_bearish_projects_downward() -> None:
    fib = fibonacci_extension(_anchor(6, 150.0), _anchor(14, 50.0), _anchor(20, 100.0))
    assert fib.direction == "bearish"
    # down-swing: level = pullback - r*(high-low)
    assert fib.levels["1.272"] == pytest.approx(-27.2, abs=_TOL)
    assert fib.levels["2.0"] == pytest.approx(-100.0, abs=_TOL)


# --------------------------------------------------------------------------- #
# Auto-anchor + trailing                                                       #
# --------------------------------------------------------------------------- #


def test_dominant_swing_picks_intended_leg() -> None:
    swing = dominant_swing(_swing_fixture())
    assert swing is not None
    high_anchor, low_anchor = swing
    assert high_anchor.price == pytest.approx(150.0, abs=_TOL)
    assert low_anchor.price == pytest.approx(50.0, abs=_TOL)
    # low (bar 6) printed before high (bar 14) -> the derived grid is an up-swing.
    fib = fibonacci_retracement(high_anchor, low_anchor)
    assert fib.direction == "bullish"


def test_dominant_swing_is_trailing_unconfirmed_not_picked_early() -> None:
    bars = _swing_fixture()
    # The high at bar 14 confirms only once its 3 right bars exist (bar 17). A
    # truncation ending at bar 15 (indices 0..15) cannot yet see it.
    early = dominant_swing(bars[:16])
    # Only the confirmed low@6 exists as a pivot then -> no opposite-kind leg.
    assert early is None


def test_dominant_swing_grid_unchanged_by_future_bars() -> None:
    bars = _swing_fixture()
    # Both pivots confirmed by bar 17; truncate at 20 vs the full 30-bar series.
    at_20 = dominant_swing(bars[:20])
    at_full = dominant_swing(bars)
    assert at_20 is not None and at_full is not None
    assert at_20 == at_full  # appending flat future bars re-anchors nothing
    # ...and therefore the reported grid is byte-identical.
    assert fibonacci_retracement(*at_20).levels == fibonacci_retracement(*at_full).levels


def test_fibonacci_levels_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        FibonacciLevels(
            kind="retracement",
            high_anchor=_anchor(1, 150.0),
            low_anchor=_anchor(0, 50.0),
            direction="bullish",
            levels={"0.5": 100.0},
            bogus=1,  # type: ignore[call-arg]
        )
