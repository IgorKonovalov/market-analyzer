"""Plan 0051 phase 1 done-when: `analysis/levels.py::swing_pivots`.

Covers:
- `swing_pivots` returns the confirmed highs/lows of a constructed fixture, with
  the right `bar_index` / `ts` / `price` / `kind`.
- Confirmation semantics: a pivot at bar `j` needs `right` bars of right-context,
  so it first appears when the series reaches `j + right` — never earlier.
- Truncation invariance (anti-lookahead): the pivots reported on `bars[0..=k]`
  are exactly the full-series pivots with `bar_index <= k - right`; appending
  future bars never changes or removes an already-confirmed pivot.
- Asymmetric wings (`left != right`) are honoured.
- The refactored `condition_snapshot` produces the same `support_resistance`
  dict as before the extraction (pinned by the unchanged snapshot tests; the
  equivalence test here re-derives the dict from `swing_pivots` directly).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis.levels import swing_pivots
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bar(i: int, *, h: float, low: float) -> Bar:
    mid = (h + low) / 2.0
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=mid,
        high=h,
        low=low,
        close=mid,
        volume=1000.0,
        source="synthetic",
    )


def _swing_fixture(n: int = 24) -> list[Bar]:
    """Flat 99..101 band with one clear swing low (50.0 at bar 6) and one clear
    swing high (150.0 at bar 14)."""

    bars: list[Bar] = []
    for i in range(n):
        h, low = 101.0, 99.0
        if i == 6:
            h, low = 91.0, 50.0  # swing low: low far below all neighbours
        if i == 14:
            h, low = 150.0, 109.0  # swing high: high far above all neighbours
        bars.append(_bar(i, h=h, low=low))
    return bars


# --------------------------------------------------------------------------- #
# Confirmed pivots on a constructed fixture                                    #
# --------------------------------------------------------------------------- #


def test_swing_pivots_finds_constructed_high_and_low() -> None:
    bars = _swing_fixture()
    pivots = swing_pivots(bars)  # left=3, right=3

    lows = [p for p in pivots if p.kind == "low"]
    highs = [p for p in pivots if p.kind == "high"]
    assert [(p.bar_index, p.price) for p in lows] == [(6, 50.0)]
    assert [(p.bar_index, p.price) for p in highs] == [(14, 150.0)]
    assert lows[0].ts == bars[6].event_ts
    assert highs[0].ts == bars[14].event_ts


def test_swing_pivots_ordered_by_bar_index() -> None:
    pivots = swing_pivots(_swing_fixture())
    indices = [p.bar_index for p in pivots]
    assert indices == sorted(indices)
    assert indices == [6, 14]


def test_swing_pivots_strictness_excludes_equal_neighbours() -> None:
    """An extreme tied with a neighbour is not a strict local max/min — no pivot."""

    bars = _swing_fixture()
    # Duplicate the swing high one bar later: neither bar strictly exceeds the other.
    bars[15] = _bar(15, h=150.0, low=109.0)
    highs = [p for p in swing_pivots(bars) if p.kind == "high"]
    assert highs == []


def test_swing_pivots_rejects_degenerate_wings() -> None:
    bars = _swing_fixture()
    with pytest.raises(ValueError):
        swing_pivots(bars, left=0)
    with pytest.raises(ValueError):
        swing_pivots(bars, right=0)


# --------------------------------------------------------------------------- #
# Asymmetric wings                                                             #
# --------------------------------------------------------------------------- #


def test_swing_pivots_asymmetric_wings() -> None:
    """With right=5 the swing high at bar 14 in a 19-bar series has only 4 bars
    of right-context — unconfirmed; with right=4 it confirms."""

    bars = _swing_fixture(19)  # last index 18; bar 14 has 4 right-context bars
    assert all(p.bar_index != 14 for p in swing_pivots(bars, left=3, right=5))
    confirmed = swing_pivots(bars, left=3, right=4)
    assert any(p.bar_index == 14 and p.kind == "high" for p in confirmed)


def test_swing_pivots_wider_left_wing_filters_lesser_extremes() -> None:
    """A local high that beats 2 bars each side but not a higher bar 3 to its left
    is a pivot at left=2 and not at left=3."""

    bars = _swing_fixture()
    # bar 17 prints a minor high (120) below the major one at 14 (150).
    bars[17] = _bar(17, h=120.0, low=109.0)
    narrow = swing_pivots(bars, left=2, right=2)
    wide = swing_pivots(bars, left=3, right=3)
    assert any(p.bar_index == 17 and p.kind == "high" for p in narrow)
    assert all(p.bar_index != 17 for p in wide)


# --------------------------------------------------------------------------- #
# Anti-lookahead: confirmation + truncation invariance                         #
# --------------------------------------------------------------------------- #


def test_pivot_unknown_until_right_context_complete() -> None:
    """The swing high at bar 14 (right=3) is first reported when bar 17 exists:
    absent on bars[0..=16], present on bars[0..=17]."""

    bars = _swing_fixture()
    assert all(p.bar_index != 14 for p in swing_pivots(bars[:17]))  # last bar 16
    assert any(
        p.bar_index == 14 and p.kind == "high" and abs(p.price - 150.0) < _TOL
        for p in swing_pivots(bars[:18])  # last bar 17 = 14 + right
    )


def test_truncation_invariance_no_lookahead() -> None:
    """For every truncation point k, the pivots on bars[0..=k] are EXACTLY the
    full-series pivots with bar_index <= k - right: once confirmed, a pivot is
    unchanged (same index/ts/price/kind) by any future bar, and nothing
    unconfirmed leaks in early."""

    right = 3
    bars = _swing_fixture()
    full = swing_pivots(bars, left=3, right=right)
    assert len(full) == 2  # the fixture's two pivots, sanity
    for k in range(len(bars)):
        truncated = swing_pivots(bars[: k + 1], left=3, right=right)
        expected = [p for p in full if p.bar_index <= k - right]
        assert truncated == expected, f"pivot set diverged at truncation k={k}"


def test_appending_bars_never_mutates_confirmed_pivots() -> None:
    """Extending the series with new extreme bars leaves every already-confirmed
    pivot byte-identical (prefix property of the pivot list)."""

    bars = _swing_fixture()
    before = swing_pivots(bars)
    extended = bars + [_bar(24 + i, h=200.0 + i, low=10.0 - i) for i in range(6)]
    after = swing_pivots(extended)
    assert after[: len(before)] == before


# --------------------------------------------------------------------------- #
# Snapshot delegation equivalence                                              #
# --------------------------------------------------------------------------- #


def test_snapshot_support_resistance_matches_swing_pivots_derivation() -> None:
    """The refactored `condition_snapshot` derives `support_resistance` from
    `swing_pivots(left=3, right=3)`: most recent 5 pivot prices per side,
    deduplicated, sorted ascending — same dict the private helper produced."""

    bars = _swing_fixture()
    pivots = swing_pivots(bars, left=3, right=3)
    expected = {
        "support": sorted({p.price for p in pivots if p.kind == "low"}),
        "resistance": sorted({p.price for p in pivots if p.kind == "high"}),
    }
    snap = condition_snapshot(bars, "1d")
    assert snap.support_resistance == expected
    assert snap.support_resistance == {"support": [50.0], "resistance": [150.0]}
