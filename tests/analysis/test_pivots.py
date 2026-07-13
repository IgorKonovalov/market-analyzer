"""Plan 0092 phase 3 done-when: classic pivot points (`analysis/levels.py`).

- Each method (floor / camarilla / woodie) matches a hand-computed set within 1e-9.
- Pivots are computed from the last completed bar only (trailing).
- `PivotPoints` rejects an extra field (`extra="forbid"`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.levels import pivot_points
from market_analyser.analysis.types import PivotPoints
from market_analyser.data.types import Bar

_TOL = 1e-9
_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _bar(i: int, *, h: float, low: float, close: float) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=_T0 + timedelta(days=i),
        open=close,
        high=h,
        low=low,
        close=close,
        volume=1000.0,
        source="synthetic",
    )


def _bars() -> list[Bar]:
    # An earlier bar the pivots must ignore, then the source bar H=140 L=90 C=100.
    return [_bar(0, h=200.0, low=10.0, close=50.0), _bar(1, h=140.0, low=90.0, close=100.0)]


def test_floor_pivots_match_hand_grid() -> None:
    pp = pivot_points(_bars(), method="floor")
    assert pp.method == "floor"
    assert pp.pivot == pytest.approx(110.0, abs=_TOL)  # (140+90+100)/3
    assert pp.resistances == pytest.approx([130.0, 160.0, 180.0], abs=_TOL)
    assert pp.supports == pytest.approx([80.0, 60.0, 30.0], abs=_TOL)


def test_woodie_pivots_match_hand_grid() -> None:
    pp = pivot_points(_bars(), method="woodie")
    assert pp.pivot == pytest.approx(107.5, abs=_TOL)  # (140+90+2*100)/4
    assert pp.resistances == pytest.approx([125.0, 157.5, 175.0], abs=_TOL)
    assert pp.supports == pytest.approx([75.0, 57.5, 25.0], abs=_TOL)


def test_camarilla_pivots_match_hand_grid() -> None:
    pp = pivot_points(_bars(), method="camarilla")
    assert pp.pivot == pytest.approx(110.0, abs=_TOL)  # (140+90+100)/3
    # Rn = C + (H-L)*1.1/{12,6,4}; Sn mirror below C. C=100, H-L=50.
    assert pp.resistances == pytest.approx(
        [100.0 + 55.0 / 12.0, 100.0 + 55.0 / 6.0, 100.0 + 55.0 / 4.0], abs=_TOL
    )
    assert pp.supports == pytest.approx(
        [100.0 - 55.0 / 12.0, 100.0 - 55.0 / 6.0, 100.0 - 55.0 / 4.0], abs=_TOL
    )


def test_pivots_use_last_completed_bar_only() -> None:
    # Appending a wild future bar re-bases the pivots on it; dropping back to the
    # prior series reproduces the earlier grid — the read is a pure function of the
    # last bar, never the history before it.
    base = _bars()
    from_base = pivot_points(base)
    extended = pivot_points([*base, _bar(2, h=500.0, low=400.0, close=450.0)])
    assert extended.pivot != from_base.pivot
    assert pivot_points(base).pivot == from_base.pivot


def test_pivot_points_requires_bars() -> None:
    with pytest.raises(ValueError, match="at least one bar"):
        pivot_points([])


def test_pivot_points_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        PivotPoints(
            method="floor",
            pivot=1.0,
            resistances=[1.0, 2.0, 3.0],
            supports=[0.5, 0.4, 0.3],
            bogus=1,  # type: ignore[call-arg]
        )
