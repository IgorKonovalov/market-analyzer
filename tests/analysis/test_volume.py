"""Phase-1 done-when for Plan 0027: `analysis/volume.py` measure layer.

Correctness is pinned against *independently hand-computed* expected numbers on
small hand-built fixtures (not self-referential recomputation). The load-bearing
checks are anti-lookahead (truncation invariance), determinism, the
undefined-leading convention, and the `VolumeStance` thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis import volume as vol
from market_analyser.analysis.types import (
    CounterTrendBar,
    CounterTrendVolume,
    Trend,
    VolumeStance,
)
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


# --------------------------------------------------------------------------- #
# Plan 0021 phase 2 — scanner-condition functions                              #
# --------------------------------------------------------------------------- #


def _range_then(*, breakout: bool) -> list[Bar]:
    """20 tight-range bars (close 100, high 101, low 99, flat volume 100), then a
    21st bar that is either a clear volume+price breakout or a quiet drift."""

    bars = [_bar(i, c=100.0, v=100.0, h=101.0, low=99.0) for i in range(20)]
    if breakout:
        # close 110 clears the trailing high (101) on a ~2.7x volume surge.
        bars.append(_bar(20, c=110.0, v=300.0, h=111.0, low=109.0))
    else:
        # inside the range, no volume surge.
        bars.append(_bar(20, c=100.5, v=100.0, h=101.0, low=99.0))
    return bars


def test_volume_breakout_positive_reports_level_and_multiple() -> None:
    res = vol.volume_breakout(_range_then(breakout=True))
    assert res.is_breakout is True
    assert res.direction == "bullish"
    assert res.broken_level == 101.0  # the trailing high it cleared
    assert res.volume_multiple is not None and res.volume_multiple >= vol.BREAKOUT_VOL_MULTIPLE
    assert res.symbol == "TEST"


def test_volume_breakout_negative_on_drift() -> None:
    res = vol.volume_breakout(_range_then(breakout=False))
    assert res.is_breakout is False
    assert res.direction == "neutral"
    assert res.broken_level is None


def test_volume_breakout_negative_when_surge_without_price_break() -> None:
    # A volume surge that does not clear the range is not a breakout.
    bars = [_bar(i, c=100.0, v=100.0, h=101.0, low=99.0) for i in range(20)]
    bars.append(_bar(20, c=100.5, v=400.0, h=101.0, low=99.0))
    res = vol.volume_breakout(bars)
    assert res.is_breakout is False
    assert res.volume_multiple is not None and res.volume_multiple >= vol.BREAKOUT_VOL_MULTIPLE


def test_volume_breakout_too_few_bars_is_negative() -> None:
    res = vol.volume_breakout([_bar(i, c=100.0, v=100.0) for i in range(5)])
    assert res.is_breakout is False
    assert res.volume_multiple is None


def _confirmation_series(*, up_volume: float, down_volume: float) -> list[Bar]:
    """21 bars netting upward (+2 on up bars, -1 on the down bars at i=5/10/15/20).
    Up bars carry `up_volume`, down bars `down_volume` — so the same price path
    can have volume backing the move or fighting it, depending on the weights."""

    bars = [_bar(0, c=100.0, v=100.0)]
    close = 100.0
    for i in range(1, 21):
        if i % 5 == 0:
            close -= 1.0
            bars.append(_bar(i, c=close, v=down_volume))
        else:
            close += 2.0
            bars.append(_bar(i, c=close, v=up_volume))
    return bars


def test_volume_confirmation_high_when_volume_backs_the_move() -> None:
    res = vol.volume_confirmation(_confirmation_series(up_volume=300.0, down_volume=50.0))
    assert res.direction == "bullish"
    assert res.score > 0.9  # volume concentrated on the trend bars
    assert res.confirmed is True


def test_volume_confirmation_low_on_counter_trend_volume() -> None:
    res = vol.volume_confirmation(_confirmation_series(up_volume=50.0, down_volume=600.0))
    assert res.direction == "bullish"  # price still nets up...
    assert res.score < vol.CONFIRMATION_MIN  # ...but the volume sits on the down bars
    assert res.confirmed is False


def test_volume_confirmation_flat_and_too_few_are_zero() -> None:
    flat = [_bar(i, c=100.0, v=100.0) for i in range(21)]
    res = vol.volume_confirmation(flat)
    assert res.direction == "neutral"
    assert res.score == 0.0
    assert res.confirmed is False
    short = vol.volume_confirmation([_bar(i, c=100.0 + i, v=100.0) for i in range(5)])
    assert short.score == 0.0


def _oscillating(n: int = 30, *, last_volume: float) -> list[Bar]:
    """Alternating +1/-1 closes → average gain ≈ average loss → RSI ≈ 50 (inside
    the smart-volume band); flat volume except a surge on the last bar."""

    bars: list[Bar] = []
    close = 100.0
    for i in range(n):
        close += 1.0 if i % 2 == 0 else -1.0
        bars.append(_bar(i, c=close, v=last_volume if i == n - 1 else 100.0))
    return bars


def _uptrend_surge(n: int = 30, *, last_volume: float) -> list[Bar]:
    """Monotonic rise → all gains → RSI ≈ 100 (above the band); flat volume except
    a surge on the last bar — the *same* surge as `_oscillating`, only the RSI
    differs."""

    return [_bar(i, c=100.0 + i, v=last_volume if i == n - 1 else 100.0) for i in range(n)]


def test_smart_volume_qualifies_with_surge_and_rsi_in_band() -> None:
    res = vol.smart_volume(_oscillating(last_volume=200.0))
    assert res.volume_multiple is not None and res.volume_multiple >= vol.SMART_VOL_MULTIPLE
    assert res.rsi is not None and vol.SMART_RSI_LOW <= res.rsi <= vol.SMART_RSI_HIGH
    assert res.qualifies is True


def test_smart_volume_rejects_same_surge_when_rsi_out_of_band() -> None:
    res = vol.smart_volume(_uptrend_surge(last_volume=200.0))
    # Same volume surge as the in-band fixture...
    assert res.volume_multiple is not None and res.volume_multiple >= vol.SMART_VOL_MULTIPLE
    # ...but RSI is above the band, so it does not qualify.
    assert res.rsi is not None and res.rsi > vol.SMART_RSI_HIGH
    assert res.qualifies is False


def _older_prefix(n: int = 25) -> list[Bar]:
    """Unrelated, *older* bars (high prices, heavy volume) to prepend ahead of a
    fixture. They sit outside every scanner's trailing window, so a window-local
    verdict must ignore them — and a function that (buggily) reached past its
    trailing window would have its verdict flipped by the higher highs/volume."""

    return [_bar(-n + j, c=150.0, v=1000.0, h=200.0, low=100.0) for j in range(n)]


def test_scanner_verdicts_are_trailing_window_local() -> None:
    # Anti-lookahead's testable face for a latest-bar verdict: each scanner reads
    # only a bounded trailing window ending at the last bar, so prepending older
    # history cannot change the verdict (and, by the same construction, no bar
    # *after* the last is ever read — there is no future to leak). The earlier
    # "append future bars then slice them back off" check tested nothing but
    # determinism (the slice reproduced the input verbatim); this prepends
    # genuinely different bars sitting outside the window.
    prefix = _older_prefix()

    # volume_breakout / volume_confirmation depend only on fixed trailing windows
    # (rel-vol period 20, price_lookback 20, confirmation lookback 20), so the
    # whole verdict is prefix-invariant.
    breakout = _range_then(breakout=True)
    assert vol.volume_breakout(prefix + breakout) == vol.volume_breakout(breakout)

    conf = _confirmation_series(up_volume=300.0, down_volume=50.0)
    assert vol.volume_confirmation(prefix + conf) == vol.volume_confirmation(conf)

    # smart_volume's surge leg (relative volume) is likewise window-local, so its
    # volume_multiple is prefix-invariant. Its RSI leg is Wilder's recursive
    # smoothing seeded from the series start, so a longer history shifts RSI by
    # design (a deeper past, not a future leak) — assert the windowed leg only.
    osc = _oscillating(last_volume=200.0)
    assert vol.smart_volume(osc).volume_multiple == vol.smart_volume(prefix + osc).volume_multiple


def test_scanner_functions_determinism() -> None:
    breakout = _range_then(breakout=True)
    assert vol.volume_breakout(breakout) == vol.volume_breakout(breakout)
    conf = _confirmation_series(up_volume=300.0, down_volume=50.0)
    assert vol.volume_confirmation(conf) == vol.volume_confirmation(conf)
    osc = _oscillating(last_volume=200.0)
    assert vol.smart_volume(osc) == vol.smart_volume(osc)


# --------------------------------------------------------------------------- #
# counter_trend_volume — decomposition anchored to the snapshot trend          #
# (Plan 0090 phase 3, ADR-0083)                                                #
# --------------------------------------------------------------------------- #


def _dir_bar(i: int, *, o: float, c: float, v: float) -> Bar:
    """A bar with an explicit open != close so its direction (close-vs-open) is
    exercised, unlike the module `_bar` which pins open == close (always neutral)."""

    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=o,
        high=max(o, c) + 0.5,
        low=min(o, c) - 0.5,
        close=c,
        volume=v,
        source="synthetic",
    )


# A hand-built 5-bar window: up(50), down(100), up(50), down(200), doji(999).
# Anchored UP: supportive = 50 + 50 = 100; opposing = 100 + 200 = 300; the doji is
# excluded -> share = 300 / 400 = 0.75.
def _decomp_window() -> list[Bar]:
    return [
        _dir_bar(0, o=100.0, c=101.0, v=50.0),  # up-bar
        _dir_bar(1, o=101.0, c=100.0, v=100.0),  # down-bar
        _dir_bar(2, o=100.0, c=101.0, v=50.0),  # up-bar
        _dir_bar(3, o=101.0, c=100.0, v=200.0),  # down-bar
        _dir_bar(4, o=100.0, c=100.0, v=999.0),  # doji (neutral)
    ]


def test_counter_trend_up_flags_down_bars_and_share() -> None:
    result = vol.counter_trend_volume(_decomp_window(), Trend.UP, lookback=5)
    assert result.trend is Trend.UP
    assert result.lookback == 5
    assert result.anchored_to_sideways is False
    assert len(result.bars) == 5
    # Down-bars oppose an up-trend; up-bars align; the doji is neutral, unflagged.
    assert [b.direction for b in result.bars] == [
        "bullish",
        "bearish",
        "bullish",
        "bearish",
        "neutral",
    ]
    assert [b.is_counter_trend for b in result.bars] == [False, True, False, True, False]
    assert result.counter_trend_volume_share is not None
    assert abs(result.counter_trend_volume_share - 0.75) < _TOL


def test_counter_trend_down_is_the_mirror() -> None:
    result = vol.counter_trend_volume(_decomp_window(), Trend.DOWN, lookback=5)
    # Under a down-trend the up-bars are the counter-trend ones.
    assert [b.is_counter_trend for b in result.bars] == [True, False, True, False, False]
    assert result.counter_trend_volume_share is not None
    # opposing = up-bar volume 50 + 50 = 100; supportive = down-bar 100 + 200 = 300.
    assert abs(result.counter_trend_volume_share - 0.25) < _TOL


def test_counter_trend_sideways_is_undefined() -> None:
    result = vol.counter_trend_volume(_decomp_window(), Trend.SIDEWAYS, lookback=5)
    assert result.anchored_to_sideways is True
    assert result.counter_trend_volume_share is None
    assert all(not b.is_counter_trend for b in result.bars)
    # Directions are still reported (a fact of each bar) even with no anchor.
    assert [b.direction for b in result.bars][:2] == ["bullish", "bearish"]


def _varied_series(n: int = 41) -> list[Bar]:
    """Alternating up/down bars with volumes that vary bar-to-bar, long enough for
    the trailing 20-bar volume MA (so relative_volume is defined mid-series)."""

    bars: list[Bar] = []
    for i in range(n):
        up = i % 2 == 0
        o, c = (100.0, 101.0) if up else (101.0, 100.0)
        bars.append(_dir_bar(i, o=o, c=c, v=100.0 + (i % 7) * 30.0))
    return bars


def test_counter_trend_truncation_invariance() -> None:
    """A bar already inside the trailing window is read identically whether or not
    later bars are appended — no future leak into direction, relative volume, or the
    counter-trend flag (the ADR-0023 anti-lookahead guarantee)."""

    bars = _varied_series(41)
    short = vol.counter_trend_volume(bars[:31], Trend.UP, lookback=20)  # window: idx 11..30
    long = vol.counter_trend_volume(bars[:41], Trend.UP, lookback=20)  # window: idx 21..40
    short_by_ts = {b.ts: b for b in short.bars}
    long_by_ts = {b.ts: b for b in long.bars}
    overlap = short_by_ts.keys() & long_by_ts.keys()
    assert overlap  # bars 21..30 sit in both windows
    for ts in overlap:
        assert short_by_ts[ts] == long_by_ts[ts]


def test_counter_trend_determinism() -> None:
    bars = _varied_series(41)
    assert vol.counter_trend_volume(bars, Trend.UP) == vol.counter_trend_volume(bars, Trend.UP)


def test_counter_trend_models_forbid_extra_fields() -> None:
    ts = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        CounterTrendBar(
            ts=ts,
            direction="bullish",
            relative_volume=1.0,
            is_counter_trend=False,
            bogus=1,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        CounterTrendVolume(
            symbol="X",
            trend=Trend.UP,
            lookback=20,
            anchored_to_sideways=False,
            bars=[],
            counter_trend_volume_share=0.0,
            bogus=1,  # type: ignore[call-arg]
        )


def test_counter_trend_reproduces_btc_probe_shape() -> None:
    """The BTC-probe divergence: recent down-bars carry heavy (>= 1.0 rel-vol)
    counter-trend volume while the rallies are thin (< 1.0), given trend=UP."""

    bars: list[Bar] = []
    # 20 flat baseline up-bars at volume 100 to seed the trailing MA near 100.
    for i in range(20):
        bars.append(_dir_bar(i, o=100.0, c=101.0, v=100.0))
    # Two heavy down-bars (counter-trend), then thin rallies.
    bars.append(_dir_bar(20, o=101.0, c=100.0, v=130.0))  # heavy down
    bars.append(_dir_bar(21, o=100.0, c=99.0, v=140.0))  # heavy down
    bars.append(_dir_bar(22, o=99.0, c=100.0, v=40.0))  # thin rally
    bars.append(_dir_bar(23, o=100.0, c=101.0, v=45.0))  # thin rally
    bars.append(_dir_bar(24, o=101.0, c=102.0, v=50.0))  # thin rally

    result = vol.counter_trend_volume(bars, Trend.UP, lookback=5)  # the 5 recent bars
    by_ts = {b.ts: b for b in result.bars}
    heavy_downs = [by_ts[bars[i].event_ts] for i in (20, 21)]
    thin_ups = [by_ts[bars[i].event_ts] for i in (22, 23, 24)]
    for b in heavy_downs:
        assert b.direction == "bearish" and b.is_counter_trend
        assert b.relative_volume is not None and b.relative_volume >= 1.0
    for b in thin_ups:
        assert b.direction == "bullish" and not b.is_counter_trend
        assert b.relative_volume is not None and b.relative_volume < 1.0
    # Over the recent divergence window the heavy down-bars dominate the split —
    # the shape the single aggregate score (volume_confirmation) would have hidden.
    assert result.counter_trend_volume_share is not None
    assert result.counter_trend_volume_share > 0.5


# --------------------------------------------------------------------------- #
# Money-flow indicators (Plan 0091 phase 2) — mfi / A-D line / CMF             #
# --------------------------------------------------------------------------- #


def test_mfi_matches_hand_computed() -> None:
    """h == low == c so typical price == close and raw money flow == close*volume;
    the positive/negative split and the 100*pos/(pos+neg) form are hand-verified."""

    closes = [10.0, 11.0, 12.0, 11.0, 13.0]
    volumes = [100.0, 200.0, 300.0, 400.0, 500.0]
    bars = [_bar(i, c=c, v=v) for i, (c, v) in enumerate(zip(closes, volumes, strict=True))]
    series = vol.mfi(bars, period=2)
    assert series[0] is None and series[1] is None  # first defined at index `period` = 2
    # i=2: deltas j=1 (11>10, +2200), j=2 (12>11, +3600) -> all positive -> 100.
    assert series[2] is not None and abs(series[2] - 100.0) < _TOL
    # i=3: j=2 (+3600), j=3 (11<12, -4400) -> 100*3600/(3600+4400) = 45.0.
    assert series[3] is not None and abs(series[3] - 45.0) < _TOL
    assert len(series) == len(bars)


def test_mfi_saturates_on_monotonic_rise() -> None:
    bars = [_bar(i, c=100.0 + i, v=100.0) for i in range(30)]  # tp strictly rising
    series = vol.mfi(bars, period=14)
    for i in (20, 29):
        assert series[i] is not None and abs(series[i] - 100.0) < _TOL


def test_accumulation_distribution_matches_hand_computed() -> None:
    """Money-flow multiplier `(2c - h - l)/(h - l)` chosen to be exactly +1 / -1 / 0;
    the cumulative line is hand-verified and dense from bar 0 (like OBV)."""

    bars = [
        _bar(0, c=10.0, v=100.0, h=10.0, low=8.0),  # MFM = (20-18)/2 = +1 -> MFV +100
        _bar(1, c=10.0, v=200.0, h=12.0, low=10.0),  # MFM = (20-22)/2 = -1 -> MFV -200
        _bar(2, c=13.0, v=300.0, h=14.0, low=12.0),  # MFM = (26-26)/2 = 0  -> MFV 0
    ]
    series = vol.accumulation_distribution(bars)
    assert series == [100.0, -100.0, -100.0]  # 100; 100-200; -100+0


def test_accumulation_distribution_empty_is_none_list() -> None:
    assert vol.accumulation_distribution([]) == []


def test_chaikin_money_flow_matches_hand_computed() -> None:
    bars = [
        _bar(0, c=10.0, v=100.0, h=10.0, low=8.0),  # MFV +100
        _bar(1, c=10.0, v=200.0, h=12.0, low=10.0),  # MFV -200
        _bar(2, c=13.0, v=300.0, h=14.0, low=12.0),  # MFV 0
    ]
    series = vol.chaikin_money_flow(bars, period=2)
    assert series[0] is None  # first defined at index period-1 = 1
    # i=1: (100 - 200) / (100 + 200) = -1/3.
    assert series[1] is not None and abs(series[1] - (-1.0 / 3.0)) < _TOL
    # i=2: (-200 + 0) / (200 + 300) = -0.4.
    assert series[2] is not None and abs(series[2] - (-0.4)) < _TOL


def test_chaikin_money_flow_saturates_when_closing_at_high() -> None:
    """Closing at the high with a fixed range makes every money-flow multiplier +1,
    so CMF (flow ÷ volume) saturates to +1."""

    bars = [_bar(i, c=100.0 + i, v=100.0, h=100.0 + i, low=98.0 + i) for i in range(30)]
    series = vol.chaikin_money_flow(bars, period=20)
    for i in (25, 29):
        assert series[i] is not None and abs(series[i] - 1.0) < _TOL


def test_money_flow_degenerate_guards() -> None:
    """Flat typical price -> MFI None; zero-range bars -> A/D contributes 0 (stays
    dense); zero-volume window -> CMF None (never a divide-by-zero)."""

    flat = [_bar(i, c=10.0, v=100.0) for i in range(20)]  # h == low == c -> tp flat
    assert vol.mfi(flat, period=14)[-1] is None
    ad = vol.accumulation_distribution(flat)
    assert all(x == 0.0 for x in ad)  # zero-range MFM -> flat, dense
    zero_vol = [_bar(i, c=10.0, v=0.0, h=11.0, low=9.0) for i in range(20)]
    assert vol.chaikin_money_flow(zero_vol, period=20)[-1] is None


def test_money_flow_truncation_invariance() -> None:
    bars = _varied_series(41)
    full_mfi = vol.mfi(bars, 14)
    full_ad = vol.accumulation_distribution(bars)
    full_cmf = vol.chaikin_money_flow(bars, 20)
    for k in (20, 30, 40):
        head = bars[: k + 1]
        assert vol.mfi(head, 14)[k] == full_mfi[k]
        assert vol.accumulation_distribution(head)[k] == full_ad[k]
        assert vol.chaikin_money_flow(head, 20)[k] == full_cmf[k]


def test_money_flow_none_prefix_and_length() -> None:
    bars = _varied_series(41)
    m = vol.mfi(bars, 14)
    assert len(m) == 41
    assert all(x is None for x in m[:14]) and m[14] is not None
    assert all(x is not None for x in m[14:])  # dense once started
    ad = vol.accumulation_distribution(bars)
    assert len(ad) == 41 and all(x is not None for x in ad)  # dense from bar 0
    cmf = vol.chaikin_money_flow(bars, 20)
    assert len(cmf) == 41
    assert all(x is None for x in cmf[:19]) and cmf[19] is not None
    assert all(x is not None for x in cmf[19:])


def test_money_flow_determinism() -> None:
    bars = _varied_series(41)
    assert vol.mfi(bars) == vol.mfi(bars)
    assert vol.accumulation_distribution(bars) == vol.accumulation_distribution(bars)
    assert vol.chaikin_money_flow(bars) == vol.chaikin_money_flow(bars)


def test_money_flow_reject_bad_periods() -> None:
    bars = _varied_series(41)
    with pytest.raises(ValueError, match="period must be >= 1"):
        vol.mfi(bars, 0)
    with pytest.raises(ValueError, match="period must be >= 1"):
        vol.chaikin_money_flow(bars, 0)
