"""Plan 0051 phase 2 done-when: `analysis/volume_profile.py`.

Covers:
- The profile bins volume into the expected price buckets on a constructed
  fixture (whole-bucket bars, plus proportional split for a bar spanning two
  buckets).
- `volume_at_price(price, band)` returns the summed volume within a band around
  a level, with proportional partial-bucket attribution.
- Trailing window: bars older than the window are excluded.
- Truncation invariance (anti-lookahead): the profile at the as-of bar reads no
  future bar — its price range and binned totals account for exactly the
  trailing-window bars up to and including the as-of bar.
- Degenerate inputs (empty series, zero-range window, single-price bar) and
  parameter validation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis.volume_profile import (
    VolumeProfile,
    volume_profile,
)
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bar(i: int, *, low: float, high: float, volume: float) -> Bar:
    mid = (low + high) / 2.0
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=volume,
        source="synthetic",
    )


def _bucket_volume(profile: VolumeProfile, low: float, high: float) -> float:
    """The volume of the single bin whose bounds match (low, high)."""

    matches = [b for b in profile.bins if abs(b.low - low) < 1e-6 and abs(b.high - high) < 1e-6]
    assert len(matches) == 1, f"expected one bin ({low}, {high}), got {profile.bins}"
    return matches[0].volume


# --------------------------------------------------------------------------- #
# Binning                                                                      #
# --------------------------------------------------------------------------- #


def test_profile_bins_volume_into_expected_buckets() -> None:
    """Two bars wholly inside [100,110], one wholly inside [110,120]: with two
    buckets over the [100,120] range, the lower bucket holds 1000+500 and the
    upper holds 800."""

    bars = [
        _bar(0, low=100.0, high=110.0, volume=1000.0),
        _bar(1, low=100.0, high=110.0, volume=500.0),
        _bar(2, low=110.0, high=120.0, volume=800.0),
    ]
    profile = volume_profile(bars, window=10, bins=2)

    assert profile.price_low == 100.0
    assert profile.price_high == 120.0
    assert abs(_bucket_volume(profile, 100.0, 110.0) - 1500.0) < _TOL
    assert abs(_bucket_volume(profile, 110.0, 120.0) - 800.0) < _TOL


def test_bar_spanning_two_buckets_splits_proportionally() -> None:
    """A bar trading [105,115] across the 110 bucket edge contributes half its
    volume to each bucket."""

    bars = [
        _bar(0, low=100.0, high=110.0, volume=0.0),  # pins the lower range edge
        _bar(1, low=110.0, high=120.0, volume=0.0),  # pins the upper range edge
        _bar(2, low=105.0, high=115.0, volume=1000.0),
    ]
    profile = volume_profile(bars, window=10, bins=2)

    assert abs(_bucket_volume(profile, 100.0, 110.0) - 500.0) < _TOL
    assert abs(_bucket_volume(profile, 110.0, 120.0) - 500.0) < _TOL


def test_total_binned_volume_equals_window_volume() -> None:
    """Proportional attribution conserves volume: the bins sum to exactly the
    window's total traded volume."""

    bars = [
        _bar(i, low=100.0 + 3 * i, high=112.0 + 3 * i, volume=100.0 * (i + 1)) for i in range(8)
    ]
    profile = volume_profile(bars, window=8, bins=5)
    assert abs(sum(b.volume for b in profile.bins) - sum(b.volume for b in bars)) < 1e-6


def test_single_price_bar_lands_whole_volume_in_its_bucket() -> None:
    bars = [
        _bar(0, low=100.0, high=120.0, volume=0.0),  # pins the range
        _bar(1, low=115.0, high=115.0, volume=700.0),  # zero-range bar
    ]
    profile = volume_profile(bars, window=10, bins=2)
    assert abs(_bucket_volume(profile, 110.0, 120.0) - 700.0) < _TOL
    assert abs(_bucket_volume(profile, 100.0, 110.0) - 0.0) < _TOL


# --------------------------------------------------------------------------- #
# volume_at_price                                                              #
# --------------------------------------------------------------------------- #


def test_volume_at_price_sums_band_with_proportional_edges() -> None:
    """Band [105,115] covers the top half of the lower bucket (750 of 1500) and
    the bottom half of the upper bucket (400 of 800) -> 1150."""

    bars = [
        _bar(0, low=100.0, high=110.0, volume=1000.0),
        _bar(1, low=100.0, high=110.0, volume=500.0),
        _bar(2, low=110.0, high=120.0, volume=800.0),
    ]
    profile = volume_profile(bars, window=10, bins=2)

    assert abs(profile.volume_at_price(110.0, 5.0) - 1150.0) < _TOL
    # A band swallowing the whole range reads the full traded volume.
    assert abs(profile.volume_at_price(110.0, 50.0) - 2300.0) < _TOL
    # A band entirely inside one bucket reads that bucket's covered fraction.
    assert abs(profile.volume_at_price(105.0, 2.5) - 1500.0 * 0.5) < _TOL


def test_volume_at_price_outside_range_is_zero() -> None:
    bars = [_bar(0, low=100.0, high=110.0, volume=1000.0)]
    profile = volume_profile(bars, window=10, bins=4)
    assert profile.volume_at_price(200.0, 5.0) == 0.0


def test_volume_at_price_rejects_negative_band() -> None:
    profile = volume_profile([_bar(0, low=100.0, high=110.0, volume=1.0)], window=5, bins=2)
    with pytest.raises(ValueError):
        profile.volume_at_price(105.0, -1.0)


# --------------------------------------------------------------------------- #
# Trailing window                                                              #
# --------------------------------------------------------------------------- #


def test_bars_older_than_window_are_excluded() -> None:
    """A huge-volume bar that falls outside the trailing window contributes
    nothing: the profile covers only the last `window` bars' range + volume."""

    old_heavy = _bar(0, low=200.0, high=210.0, volume=99_999.0)
    recent = [_bar(1 + i, low=100.0, high=110.0, volume=1000.0) for i in range(3)]
    profile = volume_profile([old_heavy, *recent], window=3, bins=2)

    assert profile.price_low == 100.0
    assert profile.price_high == 110.0  # the old bar's 200..210 range is gone
    assert profile.start_ts == recent[0].event_ts
    assert abs(sum(b.volume for b in profile.bins) - 3000.0) < _TOL


# --------------------------------------------------------------------------- #
# Anti-lookahead: the as-of profile reads no future bar                        #
# --------------------------------------------------------------------------- #


def test_truncation_profile_reads_no_future_bar() -> None:
    """Bars 0..49 trade [100,110]; bar 50 explodes to [200,210] on huge volume.
    The profile as of bar 49 (truncated series) shows no trace of the future
    bar — range capped at 110, totals equal to the first 50 bars' volume —
    while the profile as of bar 50 does include it."""

    quiet = [_bar(i, low=100.0, high=110.0, volume=1000.0) for i in range(50)]
    future = _bar(50, low=200.0, high=210.0, volume=50_000.0)
    bars = [*quiet, future]

    as_of_49 = volume_profile(bars[:50], window=90, bins=10)
    assert as_of_49.end_ts == quiet[-1].event_ts
    assert as_of_49.price_high == 110.0  # the future bar's 200..210 never leaks in
    assert abs(sum(b.volume for b in as_of_49.bins) - 50_000.0) < _TOL  # 50 x 1000
    assert as_of_49.volume_at_price(205.0, 5.0) == 0.0

    as_of_50 = volume_profile(bars, window=90, bins=10)
    assert as_of_50.price_high == 210.0
    assert as_of_50.volume_at_price(205.0, 5.0) > 0.0


def test_truncation_totals_account_exactly_for_trailing_window() -> None:
    """For several truncation points k, the binned total equals exactly the sum
    of the trailing `window` bars up to k — by volume conservation, nothing
    outside bars[k-window+1 ..= k] is read."""

    bars = [_bar(i, low=100.0 + i, high=104.0 + i, volume=10.0 * (i + 1)) for i in range(40)]
    window = 12
    for k in (5, 20, 39):
        profile = volume_profile(bars[: k + 1], window=window, bins=6)
        expected_total = sum(b.volume for b in bars[max(0, k - window + 1) : k + 1])
        assert abs(sum(b.volume for b in profile.bins) - expected_total) < 1e-6
        assert profile.end_ts == bars[k].event_ts


# --------------------------------------------------------------------------- #
# Degenerate inputs + validation                                               #
# --------------------------------------------------------------------------- #


def test_zero_range_window_collapses_to_single_degenerate_bin() -> None:
    bars = [_bar(i, low=100.0, high=100.0, volume=500.0) for i in range(4)]
    profile = volume_profile(bars, window=10, bins=8)
    assert profile.price_low == profile.price_high == 100.0
    assert len(profile.bins) == 1
    assert profile.bins[0].volume == 2000.0
    # The degenerate bucket is readable through volume_at_price.
    assert profile.volume_at_price(100.0, 1.0) == 2000.0
    assert profile.volume_at_price(105.0, 1.0) == 0.0


def test_empty_and_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        volume_profile([])
    bars = [_bar(0, low=100.0, high=110.0, volume=1.0)]
    with pytest.raises(ValueError):
        volume_profile(bars, window=0)
    with pytest.raises(ValueError):
        volume_profile(bars, bins=0)
