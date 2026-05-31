"""Phase-1 done-when for Plan 0027: `analysis/volume.py` measure layer.

Correctness is pinned against *independently hand-computed* expected numbers on
small hand-built fixtures (not self-referential recomputation). The load-bearing
checks are anti-lookahead (truncation invariance), determinism, the
undefined-leading convention, and the `VolumeStance` thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis import volume as vol
from market_analyser.analysis.types import VolumeStance
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bar(i: int, *, c: float, v: float, h: float | None = None, low: float | None = None) -> Bar:
    """A bar at day `i`. `h`/`low` default to a tight band around `c` so OHLC
    invariants hold; volume `v` is what these tests actually exercise."""

    hi = c if h is None else h
    lo = c if low is None else low
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=v,
        source="synthetic",
    )


# --------------------------------------------------------------------------- #
# volume_sma — trailing MA, None-prefixed                                      #
# --------------------------------------------------------------------------- #


def test_volume_sma_matches_hand_computed() -> None:
    volumes = [100.0, 200.0, 300.0, 400.0, 500.0]
    bars = [_bar(i, c=10.0, v=v) for i, v in enumerate(volumes)]
    series = vol.volume_sma(bars, period=3)
    # None for i < period-1; then trailing 3-bar means.
    assert series[0] is None and series[1] is None
    assert series[2] is not None and abs(series[2] - 200.0) < _TOL  # (100+200+300)/3
    assert series[3] is not None and abs(series[3] - 300.0) < _TOL  # (200+300+400)/3
    assert series[4] is not None and abs(series[4] - 400.0) < _TOL  # (300+400+500)/3
    assert len(series) == len(bars)


# --------------------------------------------------------------------------- #
# relative_volume — ratio + trailing percentile, divide-by-zero guard          #
# --------------------------------------------------------------------------- #


def _flat_then_last(last_volume: float, *, n: int = 20, flat: float = 100.0) -> list[Bar]:
    """`n-1` bars at `flat` volume, then one bar at `last_volume`."""

    bars = [_bar(i, c=10.0, v=flat) for i in range(n - 1)]
    bars.append(_bar(n - 1, c=10.0, v=last_volume))
    return bars


def test_relative_volume_ratio_and_percentile() -> None:
    bars = _flat_then_last(300.0)  # 19x100 + 1x300, period 20
    ratio, percentile = vol.relative_volume(bars, period=20)
    # MA = (19*100 + 300) / 20 = 2200/20 = 110; ratio = 300/110.
    assert ratio is not None and abs(ratio - (300.0 / 110.0)) < _TOL
    # 300 is the max of the 20-bar window -> 20/20 at-or-below -> 100th percentile.
    assert percentile is not None and abs(percentile - 100.0) < _TOL


def test_relative_volume_none_when_too_few_bars() -> None:
    bars = [_bar(i, c=10.0, v=100.0) for i in range(5)]
    assert vol.relative_volume(bars, period=20) == (None, None)


def test_relative_volume_ratio_none_on_zero_ma() -> None:
    # An all-zero-volume window: MA is 0 -> ratio is None, never inf.
    bars = [_bar(i, c=10.0, v=0.0) for i in range(20)]
    ratio, _ = vol.relative_volume(bars, period=20)
    assert ratio is None


# --------------------------------------------------------------------------- #
# obv — cumulative, seeded at 0; obv_slope sign                                #
# --------------------------------------------------------------------------- #


def test_obv_matches_hand_computed() -> None:
    closes = [10.0, 11.0, 10.0, 12.0, 12.0, 13.0]
    volumes = [100.0, 200.0, 150.0, 300.0, 50.0, 250.0]
    bars = [_bar(i, c=c, v=v) for i, (c, v) in enumerate(zip(closes, volumes, strict=True))]
    series = vol.obv(bars)
    # seed 0; +200 (up); -150 (down); +300 (up); +0 (flat); +250 (up)
    assert series == [0.0, 200.0, 50.0, 350.0, 350.0, 600.0]


def test_obv_slope_positive_on_accumulation() -> None:
    # Strictly rising closes -> OBV climbs every bar -> positive slope.
    bars = [_bar(i, c=10.0 + i, v=100.0) for i in range(12)]
    slope = vol.obv_slope(bars, lookback=5)
    assert slope[-1] is not None and slope[-1] > 0.0


def test_obv_slope_negative_on_distribution() -> None:
    # Strictly falling closes -> OBV falls every bar -> negative slope.
    bars = [_bar(i, c=100.0 - i, v=100.0) for i in range(12)]
    slope = vol.obv_slope(bars, lookback=5)
    assert slope[-1] is not None and slope[-1] < 0.0


def test_obv_slope_none_prefixed() -> None:
    bars = [_bar(i, c=10.0 + i, v=100.0) for i in range(8)]
    slope = vol.obv_slope(bars, lookback=5)
    assert all(v is None for v in slope[:5])
    assert all(v is not None for v in slope[5:])
    assert len(slope) == len(bars)


# --------------------------------------------------------------------------- #
# vwap — rolling trailing, typical-price weighted                              #
# --------------------------------------------------------------------------- #


def test_vwap_matches_hand_computed() -> None:
    bars = [
        _bar(0, c=9.0, v=100.0, h=10.0, low=8.0),  # tp = 9
        _bar(1, c=11.0, v=300.0, h=12.0, low=10.0),  # tp = 11
        _bar(2, c=13.0, v=100.0, h=14.0, low=12.0),  # tp = 13
    ]
    series = vol.vwap(bars, period=2)
    assert series[0] is None
    # i=1: (9*100 + 11*300) / 400 = 4200/400 = 10.5
    assert series[1] is not None and abs(series[1] - 10.5) < _TOL
    # i=2: (11*300 + 13*100) / 400 = 4600/400 = 11.5
    assert series[2] is not None and abs(series[2] - 11.5) < _TOL


def test_vwap_none_on_zero_volume_window() -> None:
    bars = [_bar(i, c=10.0, v=0.0, h=11.0, low=9.0) for i in range(4)]
    series = vol.vwap(bars, period=2)
    assert all(v is None for v in series)


# --------------------------------------------------------------------------- #
# VolumeStance thresholds                                                       #
# --------------------------------------------------------------------------- #


def test_stance_heavy_normal_light() -> None:
    # heavy: last 300 vs MA 110 -> ratio ~2.73 >= 1.5
    assert vol.volume_summary(_flat_then_last(300.0)).stance == VolumeStance.HEAVY
    # light: last 20 vs MA = (19*100+20)/20 = 96 -> ratio ~0.21 <= 0.5
    assert vol.volume_summary(_flat_then_last(20.0)).stance == VolumeStance.LIGHT
    # normal: all 100 -> ratio 1.0, in band
    flat = [_bar(i, c=10.0, v=100.0) for i in range(20)]
    assert vol.volume_summary(flat).stance == VolumeStance.NORMAL


# --------------------------------------------------------------------------- #
# volume_summary composition + too-few-bars degeneracy                         #
# --------------------------------------------------------------------------- #


def test_volume_summary_carries_latest_measures() -> None:
    bars = _flat_then_last(300.0)
    summary = vol.volume_summary(bars)
    assert summary.latest_volume == 300.0
    assert summary.volume_sma is not None
    assert summary.relative_volume is not None
    assert summary.obv is not None
    assert summary.vwap is not None
    assert summary.stance == VolumeStance.HEAVY


def test_volume_summary_empty_is_all_none_and_normal() -> None:
    summary = vol.volume_summary([])
    assert summary.latest_volume is None
    assert summary.volume_sma is None
    assert summary.relative_volume is None
    assert summary.volume_percentile is None
    assert summary.obv is None
    assert summary.obv_slope is None
    assert summary.vwap is None
    assert summary.stance == VolumeStance.NORMAL


def test_volume_summary_short_series_does_not_raise() -> None:
    bars = [_bar(i, c=10.0 + i, v=100.0) for i in range(3)]
    summary = vol.volume_summary(bars)  # must not raise
    assert summary.volume_sma is None  # needs 20 bars
    assert summary.relative_volume is None
    assert summary.stance == VolumeStance.NORMAL


# --------------------------------------------------------------------------- #
# Anti-lookahead + determinism                                                 #
# --------------------------------------------------------------------------- #


def _mixed_series(n: int = 40) -> list[Bar]:
    bars: list[Bar] = []
    for i in range(n):
        close = 100.0 + (i % 5) - 2.0 + 0.3 * i  # drifts up with wobble
        volume = 100.0 + 50.0 * (i % 7)
        bars.append(_bar(i, c=close, v=volume, h=close + 1.0, low=close - 1.0))
    return bars


def test_anti_lookahead_truncation_invariance() -> None:
    bars: Sequence[Bar] = _mixed_series(40)
    full_sma = vol.volume_sma(bars, period=5)
    full_obv = vol.obv(bars)
    full_slope = vol.obv_slope(bars, lookback=5)
    full_vwap = vol.vwap(bars, period=5)
    for k in (10, 25, 39):
        head = bars[: k + 1]
        assert vol.volume_sma(head, period=5)[k] == full_sma[k]
        assert vol.obv(head)[k] == full_obv[k]
        assert vol.obv_slope(head, lookback=5)[k] == full_slope[k]
        assert vol.vwap(head, period=5)[k] == full_vwap[k]


def test_determinism_two_calls_equal() -> None:
    bars = _mixed_series(30)
    assert vol.volume_sma(bars) == vol.volume_sma(bars)
    assert vol.obv(bars) == vol.obv(bars)
    assert vol.obv_slope(bars) == vol.obv_slope(bars)
    assert vol.vwap(bars) == vol.vwap(bars)
    assert vol.volume_summary(bars) == vol.volume_summary(bars)
