"""Phase-3 done-when for Plan 0018: `analysis/snapshot.py::condition_snapshot`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import ConditionSnapshot, MomentumStance, Trend
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


def test_support_resistance_includes_known_swings() -> None:
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
    sr = condition_snapshot(bars, "1d").support_resistance
    assert any(abs(p - 50.0) < 1e-6 for p in sr["support"])
    assert any(abs(p - 150.0) < 1e-6 for p in sr["resistance"])


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
# No-recommendation-leak (analyst non-negotiable at the type level)           #
# --------------------------------------------------------------------------- #


def test_no_recommendation_field() -> None:
    assert set(ConditionSnapshot.model_fields) == {
        "symbol",
        "timeframe",
        "as_of",
        "trend",
        "momentum",
        "indicators",
        "support_resistance",
        "recent_patterns",
    }


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
