"""Plan 0055 phase 4 — pure BTC cycle math, pinned by fixture values.

Done-when claims pinned here:
(a) a synthetic close series with a known SMA200 yields the exact Mayer value,
    and a date inside a known (completed) cycle yields the exact phase fraction;
(b) `dist_200w_ma` returns `None` (not a number) when fewer than 1400 daily
    closes exist — never a silently-shortened window.

All functions are pure: same inputs, same outputs, no wall-clock reads.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise

import pytest

from market_analyser.analysis.cycles import (
    ESTIMATED_CYCLE_DAYS,
    HALVING_DATES,
    NEXT_HALVING_DATE_EST,
    days_since_halving,
    days_to_next_halving_est,
    dist_200w_ma,
    halving_phase,
    mayer_multiple,
)

# --- halving clock ---------------------------------------------------------------


def test_halving_constants_are_the_protocol_facts() -> None:
    assert (
        date(2012, 11, 28),
        date(2016, 7, 9),
        date(2020, 5, 11),
        date(2024, 4, 19),
    ) == HALVING_DATES
    # Observed inter-halving intervals: 1319, 1402, 1439 days; the estimated
    # cycle is their mean rounded to a whole day, anchoring the estimate date.
    intervals = [(b - a).days for a, b in pairwise(HALVING_DATES)]
    assert intervals == [1319, 1402, 1439]
    assert ESTIMATED_CYCLE_DAYS == 1387
    assert date(2028, 2, 5) == NEXT_HALVING_DATE_EST


def test_days_since_halving_on_the_halving_day_is_zero() -> None:
    assert days_since_halving(date(2024, 4, 19)) == 0
    assert halving_phase(date(2024, 4, 19)) == 0.0


def test_date_inside_a_completed_cycle_yields_the_exact_phase_fraction() -> None:
    # 2018-07-09 is exactly 730 days after the 2016-07-09 halving, inside the
    # 1402-day 2016->2020 cycle: phase is exactly 730/1402, days-to is exact
    # (a known next halving, not an estimate).
    as_of = date(2018, 7, 9)
    assert days_since_halving(as_of) == 730
    assert days_to_next_halving_est(as_of) == 672
    assert halving_phase(as_of) == 730 / 1402


def test_current_open_cycle_uses_the_labeled_estimate() -> None:
    # 781 days after the 2024-04-19 halving; the denominator is the estimated
    # cycle length because the closing halving has not happened.
    as_of = date(2026, 6, 9)
    assert days_since_halving(as_of) == 781
    assert days_to_next_halving_est(as_of) == ESTIMATED_CYCLE_DAYS - 781
    assert halving_phase(as_of) == 781 / ESTIMATED_CYCLE_DAYS


def test_dates_past_the_estimate_saturate_rather_than_go_negative() -> None:
    past_estimate = date(2028, 3, 1)  # after the 2028-02-05 estimate
    assert days_to_next_halving_est(past_estimate) == 0
    assert halving_phase(past_estimate) == 1.0
    assert days_since_halving(past_estimate) == (past_estimate - HALVING_DATES[-1]).days


def test_date_before_the_first_halving_is_rejected() -> None:
    with pytest.raises(ValueError, match="2012-11-28"):
        days_since_halving(date(2012, 1, 1))


# --- Mayer Multiple --------------------------------------------------------------


def test_mayer_multiple_exact_on_known_sma200() -> None:
    # closes 1..200: SMA200 = 100.5, latest close = 200.
    closes = [float(i) for i in range(1, 201)]
    assert mayer_multiple(closes) == 200.0 / 100.5


def test_mayer_multiple_is_trailing_only() -> None:
    # A wild prefix beyond the trailing 200 closes must not move the value.
    closes = [1_000_000.0] * 100 + [float(i) for i in range(1, 201)]
    assert mayer_multiple(closes) == 200.0 / 100.5


def test_mayer_multiple_none_under_200_closes() -> None:
    assert mayer_multiple([100.0] * 199) is None


# --- 200-week MA distance --------------------------------------------------------


def test_dist_200w_ma_exact_on_known_sma1400() -> None:
    closes = [100.0] * 1399 + [130.0]
    sma = (1399 * 100.0 + 130.0) / 1400
    assert dist_200w_ma(closes) == 130.0 / sma - 1.0


def test_dist_200w_ma_zero_when_flat() -> None:
    assert dist_200w_ma([100.0] * 1400) == 0.0


def test_dist_200w_ma_none_under_1400_closes() -> None:
    # `None`, not a number from a shortened window (plan 0055 phase 4 (b)).
    assert dist_200w_ma([100.0] * 1399) is None
    assert dist_200w_ma([]) is None
