"""Plan 0051 phases 1+3 done-when: `analysis/levels.py`.

Phase 1 — `swing_pivots`:
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

Phase 3 — `support_resistance_levels`:
- Three pivots within the cluster tolerance collapse into one `Level` with
  `touches == 3` (mean price, first/last ts bounding the cluster).
- Two pivots outside the tolerance stay separate levels.
- A high-volume-at-level zone ranks above an equal-touch low-volume one.
- The per-role `max_levels` cap keeps only the strongest zones.
- Trailing semantics at the levels surface: an unconfirmed pivot does not leak
  into the levels early, and a future explosive bar leaves the as-of levels
  untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis.levels import support_resistance_levels, swing_pivots
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


# --------------------------------------------------------------------------- #
# Phase 3 — support_resistance_levels: clustering                              #
# --------------------------------------------------------------------------- #


def _vbar(i: int, *, h: float, low: float, v: float = 1000.0) -> Bar:
    mid = (h + low) / 2.0
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=mid,
        high=h,
        low=low,
        close=mid,
        volume=v,
        source="synthetic",
    )


def _support_zone_fixture(dips: dict[int, tuple[float, float]], n: int = 30) -> list[Bar]:
    """Flat 108..110 band with swing-low dips at the given bar indices.

    `dips` maps bar index -> (low, volume). Each dip's low sits far enough below
    the 108 baseline to be a strict local min; dip highs stay below the 110
    baseline so no high pivots form."""

    bars: list[Bar] = []
    for i in range(n):
        if i in dips:
            low, volume = dips[i]
            bars.append(_vbar(i, h=low + 2.0, low=low, v=volume))
        else:
            bars.append(_vbar(i, h=110.0, low=108.0))
    return bars


def test_levels_cluster_three_pivots_within_tolerance_into_one() -> None:
    """Three swing lows at 100.0 / 100.2 / 100.4 sit within 0.5% of the cluster
    anchor (100.0 * 1.005 = 100.5) — one support Level with touches == 3, the
    mean price, and first/last ts bounding the clustered pivots."""

    bars = _support_zone_fixture({5: (100.0, 1000.0), 11: (100.2, 1000.0), 17: (100.4, 1000.0)})
    levels = support_resistance_levels(bars)

    supports = [lv for lv in levels if lv.role == "support"]
    assert len(supports) == 1
    level = supports[0]
    assert level.touches == 3
    assert abs(level.price - 100.2) < _TOL  # mean of the three pivot prices
    assert level.first_ts == bars[5].event_ts
    assert level.last_ts == bars[17].event_ts


def test_levels_keep_pivots_outside_tolerance_separate() -> None:
    """Lows at 100.0 and 104.0 are far beyond 0.5% of each other — two distinct
    support Levels, one touch each."""

    bars = _support_zone_fixture({5: (100.0, 1000.0), 11: (104.0, 1000.0)})
    supports = [lv for lv in support_resistance_levels(bars) if lv.role == "support"]
    assert sorted(lv.price for lv in supports) == [100.0, 104.0]
    assert [lv.touches for lv in supports] == [1, 1]


def test_levels_roles_cluster_independently() -> None:
    """A support dip and a resistance spike at nearby prices never merge — the
    clustering is per role."""

    bars: list[Bar] = []
    for i in range(30):
        if i == 6:
            bars.append(_vbar(i, h=92.0, low=90.0))  # swing low at 90
        elif i == 14:
            bars.append(_vbar(i, h=130.0, low=128.0))  # swing high at 130
        else:
            bars.append(_vbar(i, h=110.0, low=108.0))
    levels = support_resistance_levels(bars)
    assert {(lv.role, lv.price) for lv in levels} == {("support", 90.0), ("resistance", 130.0)}


# --------------------------------------------------------------------------- #
# Phase 3 — support_resistance_levels: strength ranking                        #
# --------------------------------------------------------------------------- #


def test_levels_volume_at_level_breaks_equal_touch_ties() -> None:
    """Two single-touch support zones; the pivot bar at 100 traded 100k volume,
    the one at 90 traded 1k. Equal touch terms, so the heavy zone must carry the
    larger volume_at_level and the strictly higher strength — and lead the
    ranked output."""

    bars = _support_zone_fixture({5: (100.0, 100_000.0), 12: (90.0, 1000.0)})
    levels = support_resistance_levels(bars)
    supports = [lv for lv in levels if lv.role == "support"]
    assert len(supports) == 2

    heavy = next(lv for lv in supports if abs(lv.price - 100.0) < _TOL)
    thin = next(lv for lv in supports if abs(lv.price - 90.0) < _TOL)
    assert heavy.touches == thin.touches == 1
    assert heavy.volume_at_level > thin.volume_at_level
    assert heavy.strength > thin.strength
    # Output ordering is strength-descending: the heavy zone comes first.
    assert levels[0] == heavy


def test_levels_strength_normalised_into_unit_interval() -> None:
    bars = _support_zone_fixture({5: (100.0, 100_000.0), 12: (90.0, 1000.0)})
    levels = support_resistance_levels(bars)
    assert levels  # sanity
    for level in levels:
        assert 0.0 < level.strength <= 1.0
    # The maximal-touches + maximal-volume zone scores exactly 1.0.
    assert max(lv.strength for lv in levels) == 1.0


def test_levels_max_levels_caps_per_role_keeping_strongest() -> None:
    """Three separated support zones with distinct volume mass; max_levels=2
    keeps the two strongest (the heavier-volume ones) and drops the thinnest."""

    bars = _support_zone_fixture(
        {5: (100.0, 50_000.0), 12: (90.0, 20_000.0), 19: (80.0, 100.0)},
        n=32,
    )
    capped = support_resistance_levels(bars, max_levels=2)
    supports = [lv for lv in capped if lv.role == "support"]
    assert len(supports) == 2
    assert sorted(lv.price for lv in supports) == [90.0, 100.0]  # the 80.0 zone dropped


def test_levels_deterministic_across_repeat_calls() -> None:
    bars = _support_zone_fixture({5: (100.0, 100_000.0), 12: (90.0, 1000.0)})
    assert support_resistance_levels(bars) == support_resistance_levels(bars)


# --------------------------------------------------------------------------- #
# Phase 3 — support_resistance_levels: trailing semantics + validation         #
# --------------------------------------------------------------------------- #


def test_levels_unconfirmed_pivot_does_not_leak_early() -> None:
    """The swing low at bar 6 (right=3) is confirmed at bar 9: levels on
    bars[0..=8] are empty, levels on bars[0..=9] carry the zone."""

    bars = _support_zone_fixture({6: (100.0, 1000.0)})
    assert support_resistance_levels(bars[:9]) == []  # last bar 8 — unconfirmed
    confirmed = support_resistance_levels(bars[:10])  # last bar 9 = 6 + right
    assert [(lv.role, lv.price) for lv in confirmed] == [("support", 100.0)]


def test_levels_truncation_reads_no_future_bar() -> None:
    """A future explosive bar (huge volume, far-away price) leaves the as-of
    levels untouched: levels computed on bars[0..=k] are identical whether or
    not that bar exists later in the series."""

    quiet = _support_zone_fixture({6: (100.0, 1000.0)})
    explosive = _vbar(len(quiet), h=210.0, low=200.0, v=1_000_000.0)
    extended = [*quiet, explosive]

    k = len(quiet) - 1
    as_of_k_quiet = support_resistance_levels(quiet[: k + 1])
    as_of_k_extended = support_resistance_levels(extended[: k + 1])
    assert as_of_k_quiet == as_of_k_extended
    assert all(lv.price < 200.0 for lv in as_of_k_quiet)  # no trace of the future bar


def test_levels_empty_and_pivotless_inputs_return_empty() -> None:
    assert support_resistance_levels([]) == []
    flat = [_vbar(i, h=110.0, low=108.0) for i in range(20)]  # no strict extremes
    assert support_resistance_levels(flat) == []


def test_levels_rejects_degenerate_parameters() -> None:
    bars = _support_zone_fixture({6: (100.0, 1000.0)})
    with pytest.raises(ValueError):
        support_resistance_levels(bars, cluster_tolerance_pct=0.0)
    with pytest.raises(ValueError):
        support_resistance_levels(bars, max_levels=0)
