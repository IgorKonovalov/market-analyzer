"""Phase-3 done-when for Plan 0018: `analysis/snapshot.py::condition_snapshot`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import ConditionSnapshot, MomentumStance, Trend, VolumeStance
from market_analyser.analysis.volume import volume_summary
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bar(i: int, *, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000.0,
        source="synthetic",
    )


def _rising(n: int = 60) -> list[Bar]:
    return [_bar(i, o=100.0 + i, h=100.6 + i, low=99.6 + i, c=100.5 + i) for i in range(n)]


def _falling(n: int = 60) -> list[Bar]:
    return [_bar(i, o=200.0 - i, h=200.4 - i, low=199.4 - i, c=199.5 - i) for i in range(n)]


def _choppy(n: int = 60) -> list[Bar]:
    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + math.sin(i / 2.0)  # small oscillation, no net drift
        bars.append(_bar(i, o=base, h=base + 0.6, low=base - 0.6, c=base + 0.1 * math.cos(i)))
    return bars


# --------------------------------------------------------------------------- #
# Trend classification                                                        #
# --------------------------------------------------------------------------- #


def test_trend_up_on_rising() -> None:
    assert condition_snapshot(_rising(), "1d").trend == Trend.UP


def test_trend_down_on_falling() -> None:
    assert condition_snapshot(_falling(), "1d").trend == Trend.DOWN


def test_trend_sideways_on_choppy() -> None:
    assert condition_snapshot(_choppy(), "1d").trend == Trend.SIDEWAYS


# --------------------------------------------------------------------------- #
# Momentum stance + latest indicator values                                   #
# --------------------------------------------------------------------------- #


def test_latest_indicator_values_match_series() -> None:
    bars = _rising()
    closes = [b.close for b in bars]
    snap = condition_snapshot(bars, "1d")

    rsi_last = next(v for v in reversed(ind.rsi(closes, 14)) if v is not None)
    macd_last = next(v for v in reversed(ind.macd(closes)) if v is not None)
    boll_last = next(v for v in reversed(ind.bollinger(closes, 20)) if v is not None)

    assert snap.indicators["rsi"] is not None
    assert abs(snap.indicators["rsi"] - rsi_last) < _TOL
    assert abs(snap.indicators["macd"] - macd_last.macd) < _TOL  # type: ignore[operator]
    assert abs(snap.indicators["macd_signal"] - macd_last.signal) < _TOL  # type: ignore[operator]
    assert abs(snap.indicators["bb_upper"] - boll_last.upper) < _TOL  # type: ignore[operator]
    assert abs(snap.indicators["bb_middle"] - boll_last.middle) < _TOL  # type: ignore[operator]
    assert abs(snap.indicators["bb_lower"] - boll_last.lower) < _TOL  # type: ignore[operator]


def test_momentum_overbought_on_strong_rise() -> None:
    # A monotonic rise drives RSI to 100 -> overbought.
    assert condition_snapshot(_rising(), "1d").momentum == MomentumStance.OVERBOUGHT


def test_momentum_oversold_on_strong_fall() -> None:
    assert condition_snapshot(_falling(), "1d").momentum == MomentumStance.OVERSOLD


# --------------------------------------------------------------------------- #
# Support / resistance                                                         #
# --------------------------------------------------------------------------- #


def _swing_bars() -> list[Bar]:
    """24 bars around 100 with one clear swing low (50 at bar 6) and one clear
    swing high (150 at bar 14); the last close sits between them."""

    bars: list[Bar] = []
    for i in range(24):
        base = 100.0
        h, low = base + 1.0, base - 1.0
        if i == 6:  # a clear swing low well below its neighbours
            h, low = 91.0, 50.0
        if i == 14:  # a clear swing high well above its neighbours
            h, low = 150.0, 109.0
        o = c = base
        if i == 6:
            o = c = 60.0
        if i == 14:
            o = c = 140.0
        bars.append(_bar(i, o=o, h=h, low=low, c=c))
    return bars


def test_support_resistance_includes_known_swings() -> None:
    sr = condition_snapshot(_swing_bars(), "1d").support_resistance
    assert any(abs(p - 50.0) < 1e-6 for p in sr["support"])
    assert any(abs(p - 150.0) < 1e-6 for p in sr["resistance"])


def test_nearest_levels_frame_the_last_close_with_strength() -> None:
    """Plan 0051 phase 4: the snapshot carries the nearest clustered support
    below and resistance above the last close (100), as structured Levels with
    their strength — not bare floats."""

    snap = condition_snapshot(_swing_bars(), "1d")
    assert snap.nearest_support is not None
    assert snap.nearest_resistance is not None
    assert snap.nearest_support.role == "support"
    assert abs(snap.nearest_support.price - 50.0) < 1e-6
    assert snap.nearest_support.price < 100.0  # below the last close
    assert snap.nearest_resistance.role == "resistance"
    assert abs(snap.nearest_resistance.price - 150.0) < 1e-6
    assert snap.nearest_resistance.price > 100.0  # above the last close
    for level in (snap.nearest_support, snap.nearest_resistance):
        assert level.touches == 1
        assert 0.0 < level.strength <= 1.0
        assert level.volume_at_level >= 0.0


def test_nearest_levels_exclude_wrong_side_of_close() -> None:
    """After a breakdown the only support level sits ABOVE the close: it must
    not be reported as the nearest support — both nearest fields are None when
    no level frames the close on its own side."""

    bars: list[Bar] = []
    for i in range(24):
        if i < 12:
            h, low = 110.0, 108.0
            if i == 5:  # a swing low at 100, later broken
                h, low = 102.0, 100.0
            bars.append(_bar(i, o=(h + low) / 2, h=h, low=low, c=(h + low) / 2))
        else:  # the breakdown: flat trade far below the old support
            bars.append(_bar(i, o=79.0, h=80.0, low=78.0, c=79.0))
    snap = condition_snapshot(bars, "1d")
    # The broken support is still listed among the trailing swing levels...
    assert any(abs(p - 100.0) < 1e-6 for p in snap.support_resistance["support"])
    # ...but it is above the 79 close, so it is not the nearest support.
    assert snap.nearest_support is None
    assert snap.nearest_resistance is None


# --------------------------------------------------------------------------- #
# Pattern surfacing                                                            #
# --------------------------------------------------------------------------- #


def test_recent_patterns_surface_bullish_engulfing_on_last_bar() -> None:
    bars = [
        _bar(i, o=101 + i * 0.1, h=103 + i * 0.1, low=100 + i * 0.1, c=102 + i * 0.1)
        for i in range(8)
    ]
    # Append a bearish bar then a bullish bar that engulfs it (the last bar).
    bars.append(_bar(8, o=105.0, h=105.5, low=101.5, c=102.0))  # bearish
    bars.append(_bar(9, o=101.5, h=106.5, low=101.0, c=106.0))  # bullish engulfing
    snap = condition_snapshot(bars, "1d")
    assert any(h.pattern == "bullish_engulfing" and h.bar_index == 9 for h in snap.recent_patterns)


# --------------------------------------------------------------------------- #
# Active classical chart patterns (Plan 0052 phase 3)                          #
# --------------------------------------------------------------------------- #


def _hs_bars() -> list[Bar]:
    """The Plan 0052 head & shoulders fixture: a piecewise-linear path with
    shoulders 111 @6 / 111.5 @22, head 121 @14, neckline troughs 99 @10 /
    100 @18, then a decline through the neckline (confirms at bar 27)."""

    anchors = [
        (0, 100.0), (6, 110.0), (10, 100.0), (14, 120.0), (18, 101.0), (22, 110.5), (35, 78.0),
    ]
    bases: list[float] = []
    for i in range(anchors[-1][0] + 1):
        for (x1, p1), (x2, p2) in pairwise(anchors):
            if x1 <= i <= x2:
                bases.append(p1 + (p2 - p1) * (i - x1) / (x2 - x1))
                break
    return [_bar(i, o=base, h=base + 1.0, low=base - 1.0, c=base) for i, base in enumerate(bases)]


def test_active_patterns_list_head_shoulders_on_hs_fixture() -> None:
    """The snapshot lists the active head_shoulders hit with its pattern, state,
    direction, and strength — one entry per formation, latest state (the
    confirmed hit supersedes the earlier forming one)."""

    snap = condition_snapshot(_hs_bars(), "1d")
    assert [h.pattern for h in snap.active_patterns] == ["head_shoulders"]
    hit = snap.active_patterns[0]
    assert hit.state == "confirmed"  # broke the neckline at bar 27
    assert hit.direction == "bearish"
    assert 0.0 < hit.strength <= 1.0
    assert hit.lines[0].role == "neckline"


def test_active_patterns_forming_before_the_break() -> None:
    """Truncated just before the confirming close, the same fixture reports the
    formation as forming — the snapshot read is trailing, no future bar leaks."""

    snap = condition_snapshot(_hs_bars()[:27], "1d")
    assert [(h.pattern, h.state) for h in snap.active_patterns] == [
        ("head_shoulders", "forming")
    ]


def test_active_patterns_empty_on_flat_fixture() -> None:
    """A flat series has no swing structure: the list is empty, not fabricated."""

    flat = [_bar(i, o=100.0, h=101.0, low=99.0, c=100.0) for i in range(40)]
    assert condition_snapshot(flat, "1d").active_patterns == []


# --------------------------------------------------------------------------- #
# No-recommendation-leak (analyst non-negotiable at the type level)           #
# --------------------------------------------------------------------------- #


def test_no_recommendation_field() -> None:
    fields = set(ConditionSnapshot.model_fields)
    assert fields == {
        "symbol",
        "timeframe",
        "as_of",
        "trend",
        "momentum",
        "volume_stance",  # Plan 0027: additive condition field
        "indicators",
        "support_resistance",
        "nearest_support",  # Plan 0051: structured nearest levels (price + strength)
        "nearest_resistance",
        "recent_patterns",
        "active_patterns",  # Plan 0052: classical chart patterns in play
    }
    # The analyst non-negotiable, pinned: no action/buy/sell field ever appears.
    assert not (fields & {"action", "signal", "recommendation", "buy", "sell"})


# --------------------------------------------------------------------------- #
# Anti-lookahead                                                               #
# --------------------------------------------------------------------------- #


def test_anti_lookahead_truncation_matches_full_series_at_k() -> None:
    """A snapshot computed on bars[0..=k] carries indicator latest values equal to
    the full-series indicator values at index k — no future bar leaks in."""

    bars: Sequence[Bar] = _rising(80)
    closes = [b.close for b in bars]
    full_rsi = ind.rsi(closes, 14)
    full_atr = ind.atr(bars, 14)
    full_adx = ind.adx(bars, 14)
    for k in (40, 60, 79):
        snap = condition_snapshot(bars[: k + 1], "1d")
        assert snap.as_of == bars[k].event_ts
        assert abs(snap.indicators["rsi"] - full_rsi[k]) < _TOL  # type: ignore[operator]
        assert abs(snap.indicators["atr"] - full_atr[k]) < _TOL  # type: ignore[operator]
        adx_k = full_adx[k]
        assert adx_k is not None
        assert abs(snap.indicators["adx"] - adx_k.adx) < _TOL  # type: ignore[operator]


# --------------------------------------------------------------------------- #
# Volume integration (Plan 0027 phase 2)                                       #
# --------------------------------------------------------------------------- #


def _vbar(i: int, *, c: float, v: float) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=c,
        high=c + 0.6,
        low=c - 0.6,
        close=c,
        volume=v,
        source="synthetic",
    )


def _rising_heavy_last(n: int = 60) -> list[Bar]:
    """Rising closes (OBV accumulates) with flat 1000-volume bars except a heavy
    final bar — last volume well above its trailing MA."""

    bars = [_vbar(i, c=100.0 + i, v=1000.0) for i in range(n - 1)]
    bars.append(_vbar(n - 1, c=100.0 + (n - 1), v=4000.0))
    return bars


def test_snapshot_carries_volume_on_heavy_fixture() -> None:
    snap = condition_snapshot(_rising_heavy_last(), "1d")
    assert snap.volume_stance == VolumeStance.HEAVY
    for key in ("rel_volume", "obv", "vwap", "volume", "vol_sma20", "vol_pct90", "obv_slope"):
        assert key in snap.indicators
    assert snap.indicators["rel_volume"] is not None
    assert snap.indicators["obv"] is not None
    assert snap.indicators["vwap"] is not None
    # Rising closes -> OBV accumulating -> positive slope.
    assert snap.indicators["obv_slope"] is not None and snap.indicators["obv_slope"] > 0


def test_snapshot_short_series_volume_measures_none_no_crash() -> None:
    bars = [_vbar(i, c=100.0 + i, v=1000.0) for i in range(3)]  # too short for the windows
    snap = condition_snapshot(bars, "1d")  # must not raise
    assert snap.volume_stance == VolumeStance.NORMAL
    assert snap.indicators["vol_sma20"] is None
    assert snap.indicators["rel_volume"] is None
    assert snap.indicators["vwap"] is None


def test_snapshot_volume_anti_lookahead_replay() -> None:
    """With bars truncated at k, the snapshot's volume measures equal a direct
    volume_summary on the truncated series, and differ from the full-series read
    where the heavy final bar makes them differ — no future volume leaks back."""

    bars = _rising_heavy_last(60)
    volume_keys = ("volume", "vol_sma20", "rel_volume", "vol_pct90", "obv", "obv_slope", "vwap")
    for k in (30, 58):
        snap = condition_snapshot(bars[: k + 1], "1d")
        truncated = volume_summary(bars[: k + 1])
        assert snap.indicators["rel_volume"] == truncated.relative_volume
        assert snap.indicators["obv"] == truncated.obv
        assert snap.indicators["vwap"] == truncated.vwap
        assert snap.volume_stance == truncated.stance

    full = condition_snapshot(bars, "1d")
    mid = condition_snapshot(bars[:59], "1d")  # excludes the heavy last bar
    # The heavy last bar lifts relative volume and stance vs the truncated read.
    assert full.indicators["rel_volume"] != mid.indicators["rel_volume"]
    assert full.volume_stance == VolumeStance.HEAVY
    assert mid.volume_stance != VolumeStance.HEAVY
    assert set(volume_keys) <= set(full.indicators)
