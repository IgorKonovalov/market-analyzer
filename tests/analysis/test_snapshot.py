"""Phase-3 done-when for Plan 0018: `analysis/snapshot.py::condition_snapshot`."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.divergence import detect_divergences
from market_analyser.analysis.snapshot import (
    DIVERGENCE_OSCILLATORS,
    ICHIMOKU_DISPLACEMENT,
    RECENT_DIVERGENCE_BARS,
    _classify_trend,
    _ema_adx_trend,
    condition_snapshot,
)
from market_analyser.analysis.types import (
    ConditionSnapshot,
    Divergence,
    MomentumStance,
    Trend,
    VolumeStance,
)
from market_analyser.analysis.volume import (
    accumulation_distribution,
    chaikin_money_flow,
    mfi,
    volume_summary,
)
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
# Ichimoku in trend classification (ADR-0067, Plan 0073 phase 2)              #
# --------------------------------------------------------------------------- #
#
# The conjunctive-veto rule is composed in `_classify_trend`: it runs the real
# EMA/ADX base read on `closes` and the Ichimoku regime on the displaced-cloud
# args, so feeding real rising/falling closes plus explicit cloud args exercises
# the actual composition (not a mock). A fully end-to-end `condition_snapshot`
# UP->SIDEWAYS veto is *structurally* degenerate to build on smooth synthetic
# bars: a cloud sitting above current price needs high prices in bars
# [i-78 .. i-26], which overlaps the EMA50 window [i-49 .. i] and so forces
# EMA50 up, preventing the EMA20 > EMA50 that a base UP requires. The two regime
# lenses cannot diverge that starkly on clean data — hence the veto is pinned at
# the classifier level and the wiring is proven separately below.


def test_classifier_ichimoku_veto_matrix() -> None:
    """ADR-0067: Ichimoku can veto a directional base read but never manufacture
    one. `tenkan`/`kijun` set the TK side; the cloud args set price-vs-cloud."""

    up_closes = [b.close for b in _rising(80)]  # base EMA/ADX read is UP
    down_closes = [b.close for b in _falling(80)]  # base read is DOWN
    strong = 30.0  # ADX above the trend floor
    above = up_closes[-1] + 60.0  # a cloud sitting above the last close
    below = down_closes[-1] - 60.0  # a cloud sitting below the last close

    # Sanity: the bases really are directional before Ichimoku is applied.
    assert _ema_adx_trend(up_closes, strong) is Trend.UP
    assert _ema_adx_trend(down_closes, strong) is Trend.DOWN

    def cls(closes: list[float], tenkan: float, kijun: float, ca: float, cb: float) -> Trend:
        return _classify_trend(
            closes, strong, ichimoku_tenkan=tenkan, ichimoku_kijun=kijun, cloud_a=ca, cloud_b=cb
        )

    # UP base, Ichimoku bearish (below cloud AND tenkan<kijun) -> vetoed to SIDEWAYS.
    assert cls(up_closes, 1.0, 2.0, above, above + 10) is Trend.SIDEWAYS
    # UP base, Ichimoku bullish (above cloud AND tenkan>kijun) -> confirmed UP.
    assert cls(up_closes, 2.0, 1.0, below, below - 10) is Trend.UP
    # UP base, below the cloud but tenkan>kijun -> neutral, NOT bearish -> not vetoed.
    assert cls(up_closes, 2.0, 1.0, above, above + 10) is Trend.UP
    # DOWN base, Ichimoku bullish -> vetoed to SIDEWAYS.
    assert cls(down_closes, 2.0, 1.0, below, below - 10) is Trend.SIDEWAYS
    # DOWN base, Ichimoku bearish -> confirmed DOWN.
    assert cls(down_closes, 1.0, 2.0, above, above + 10) is Trend.DOWN


def test_classifier_cannot_manufacture_a_trend() -> None:
    """A SIDEWAYS base stays SIDEWAYS no matter how bullish/bearish Ichimoku reads
    — the veto is one-directional (ADR-0067)."""

    chop_closes = [b.close for b in _choppy(80)]
    assert _ema_adx_trend(chop_closes, 5.0) is Trend.SIDEWAYS  # weak ADX -> no base trend
    bull = _classify_trend(
        chop_closes, 5.0, ichimoku_tenkan=2.0, ichimoku_kijun=1.0, cloud_a=0.0, cloud_b=1.0
    )
    assert bull is Trend.SIDEWAYS


def test_classifier_falls_back_when_ichimoku_undefined() -> None:
    """With no cloud (short series), the composed read equals the pre-0073
    EMA/ADX read exactly."""

    up_closes = [b.close for b in _rising(80)]
    assert _classify_trend(up_closes, 30.0) == _ema_adx_trend(up_closes, 30.0)


def test_snapshot_surfaces_ichimoku_scalars_and_displaced_cloud() -> None:
    """The four Ichimoku keys ride in `indicators`; the cloud-under-price fields
    read the spans computed `ICHIMOKU_DISPLACEMENT` bars ago (ADR-0067's trailing
    displaced read), not the values computed at the current bar."""

    bars = _rising(90)  # >= span_b + displacement, so the cloud is defined
    snap = condition_snapshot(bars, "1d")
    series = ind.ichimoku(bars)
    last = series[-1]
    cloud = series[len(bars) - 1 - ICHIMOKU_DISPLACEMENT]
    assert last is not None and cloud is not None
    assert snap.indicators["ichimoku_tenkan"] == last.tenkan
    assert snap.indicators["ichimoku_kijun"] == last.kijun
    assert snap.indicators["ichimoku_cloud_a"] == cloud.senkou_a
    assert snap.indicators["ichimoku_cloud_b"] == cloud.senkou_b
    assert cloud.senkou_a != last.senkou_a  # the cloud is displaced, not the current span


def test_snapshot_trend_confirmed_when_price_above_cloud() -> None:
    """A long clean rise reads above its (defined) cloud with tenkan>kijun —
    Ichimoku confirms, so the trend stays UP (done-when: EMA-up + above-cloud)."""

    snap = condition_snapshot(_rising(90), "1d")
    assert snap.indicators["ichimoku_cloud_a"] is not None  # cloud is defined here
    assert snap.trend is Trend.UP


def test_snapshot_short_series_keeps_pre_0073_trend() -> None:
    """Under `span_b + displacement` bars the cloud is undefined, so the trend is
    the identical pre-0073 EMA/ADX read (fallback proven)."""

    bars = _rising(60)
    snap = condition_snapshot(bars, "1d")
    assert snap.indicators["ichimoku_cloud_a"] is None
    assert snap.indicators["ichimoku_cloud_b"] is None
    assert snap.trend == _ema_adx_trend([b.close for b in bars], snap.indicators["adx"])
    assert snap.trend is Trend.UP  # unchanged from before Ichimoku fed the classifier


def test_snapshot_ichimoku_anti_lookahead() -> None:
    """The displaced-cloud read is trailing: on bars[0..=k] the snapshot's Ichimoku
    scalars equal the full-series `ichimoku()` at k (cloud at k - displacement)."""

    bars = _rising(100)
    full = ind.ichimoku(bars)
    for k in (80, 99):
        snap = condition_snapshot(bars[: k + 1], "1d")
        last = full[k]
        cloud = full[k - ICHIMOKU_DISPLACEMENT]
        assert last is not None and cloud is not None
        assert snap.indicators["ichimoku_tenkan"] == last.tenkan
        assert snap.indicators["ichimoku_kijun"] == last.kijun
        assert snap.indicators["ichimoku_cloud_a"] == cloud.senkou_a
        assert snap.indicators["ichimoku_cloud_b"] == cloud.senkou_b


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
        (0, 100.0),
        (6, 110.0),
        (10, 100.0),
        (14, 120.0),
        (18, 101.0),
        (22, 110.5),
        (35, 78.0),
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
    assert [(h.pattern, h.state) for h in snap.active_patterns] == [("head_shoulders", "forming")]


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
        "recent_divergences",  # Plan 0091: price↔oscillator divergences in play
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


# --------------------------------------------------------------------------- #
# Squeeze fields (Plan 0090 phase 2, ADR-0083)                                 #
# --------------------------------------------------------------------------- #

# The exact `indicators` key-set — frozen so a dropped or stray key fails loudly.
_EXPECTED_INDICATOR_KEYS = {
    "rsi",
    "rsi_pct90",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "bb_pct_b",
    "bb_width",
    "bb_width_pct90",
    "squeeze_on",
    "atr",
    "atr_pct90",
    "adx",
    "plus_di",
    "minus_di",
    "supertrend",
    "supertrend_direction",
    "ichimoku_tenkan",
    "ichimoku_kijun",
    "ichimoku_cloud_a",
    "ichimoku_cloud_b",
    "volume",
    "vol_sma20",
    "rel_volume",
    "vol_pct90",
    "obv",
    "obv_slope",
    "vwap",
    # Plan 0091 oscillators + money-flow.
    "stoch_k",
    "stoch_d",
    "stoch_rsi",
    "cci",
    "williams_r",
    "roc",
    "mfi",
    "ad_line",
    "cmf",
}


def test_snapshot_indicator_keys_frozen() -> None:
    """The `indicators` dict carries exactly the pinned key-set — the squeeze trio
    `bb_width` / `bb_width_pct90` / `squeeze_on` is present, and no key is dropped
    or added silently (additive-schema guard, ADR-0083)."""

    snap = condition_snapshot(_rising(60), "1d")
    assert set(snap.indicators) == _EXPECTED_INDICATOR_KEYS
    for key in ("bb_width", "bb_width_pct90", "squeeze_on"):
        assert key in snap.indicators


def _last_defined(series: Sequence[float | None]) -> float | None:
    return next((v for v in reversed(series) if v is not None), None)


def test_snapshot_oscillator_moneyflow_values_match_standalone() -> None:
    """Each Plan-0091 value on the snapshot equals the standalone indicator's latest
    defined value — reported facts, wired straight through (ADR-0023). Uses a choppy
    fixture so all nine are actually defined (a monotonic rise flattens RSI, leaving
    stoch_rsi undefined)."""

    bars = _choppy(60)
    closes = [b.close for b in bars]
    snap = condition_snapshot(bars, "1d")

    last_stoch = next((v for v in reversed(ind.stochastic(bars)) if v is not None), None)
    assert last_stoch is not None
    assert snap.indicators["stoch_k"] == last_stoch.k
    assert snap.indicators["stoch_d"] == last_stoch.d
    assert snap.indicators["stoch_rsi"] == _last_defined(ind.stochastic_rsi(closes))
    assert snap.indicators["cci"] == _last_defined(ind.cci(bars))
    assert snap.indicators["williams_r"] == _last_defined(ind.williams_r(bars))
    assert snap.indicators["roc"] == _last_defined(ind.roc(closes))
    assert snap.indicators["mfi"] == _last_defined(mfi(bars))
    assert snap.indicators["ad_line"] == _last_defined(accumulation_distribution(bars))
    assert snap.indicators["cmf"] == _last_defined(chaikin_money_flow(bars))
    # All nine are genuinely computed on this fixture (not silently None).
    for key in (
        "stoch_k",
        "stoch_d",
        "stoch_rsi",
        "cci",
        "williams_r",
        "roc",
        "mfi",
        "ad_line",
        "cmf",
    ):
        assert snap.indicators[key] is not None


def _accumulation_bars(closes: Sequence[float]) -> list[Bar]:
    """Bars whose volume is heavy on up-closes and light on down-closes, so OBV
    climbs even as price makes a lower high (an OBV hidden-bearish divergence)."""

    bars: list[Bar] = []
    prev: float | None = None
    for i, c in enumerate(closes):
        v = 500.0 if prev is None else (1000.0 if c > prev else 100.0 if c < prev else 300.0)
        bars.append(_bar(i, o=c, h=c + 0.5, low=c - 0.5, c=c))
        bars[-1] = bars[-1].model_copy(update={"volume": v})
        prev = c
    return bars


def test_snapshot_recent_divergences_match_detector() -> None:
    """`recent_divergences` is the divergence detector run across the oscillator set
    and filtered to the recent-activity window — proven by an independent recompute,
    with a known OBV hidden-bearish divergence present (Plan 0091 phase 5)."""

    closes = [
        100.0, 100.0, 100.0, 100.0,
        104.0, 108.0, 112.0, 116.0, 120.0,  # rally1 -> peak1 = 120
        116.0, 112.0, 108.0, 105.0,  # pull1
        108.0, 111.0, 114.0, 115.0,  # rally2 -> peak2 = 115 (lower high)
        112.0, 109.0, 107.0,  # pull2
    ]  # fmt: skip
    bars = _accumulation_bars(closes)
    snap = condition_snapshot(bars, "1d")

    cutoff = len(bars) - RECENT_DIVERGENCE_BARS
    expected: list[Divergence] = []
    for oscillator in DIVERGENCE_OSCILLATORS:
        expected.extend(d for d in detect_divergences(bars, oscillator) if d.bar_index >= cutoff)
    expected.sort(key=lambda d: (d.bar_index, d.oscillator, d.kind))
    assert snap.recent_divergences == expected
    assert any(
        d.oscillator == "obv" and d.kind == "hidden_bearish" for d in snap.recent_divergences
    )


def _flat_wide_range_bars(n: int = 40) -> list[Bar]:
    """Flat closes (band-width -> ~0) inside a wide intrabar range (ATR ~ 10), so
    the Bollinger band collapses well inside the Keltner channel: a squeeze."""

    return [_bar(i, o=100.0, h=105.0, low=95.0, c=100.0) for i in range(n)]


def _dispersed_tight_range_bars(n: int = 40) -> list[Bar]:
    """A strong close-to-close trend (large stdev -> wide Bollinger band) with a
    tight intrabar range (small ATR -> narrow Keltner channel): the band pokes
    outside the channel, so no squeeze."""

    return [
        _bar(i, o=100.0 + 3.0 * i, h=100.1 + 3.0 * i, low=99.9 + 3.0 * i, c=100.0 + 3.0 * i)
        for i in range(n)
    ]


def test_squeeze_on_when_bollinger_inside_keltner() -> None:
    snap = condition_snapshot(_flat_wide_range_bars(), "1d")
    assert snap.indicators["squeeze_on"] == 1.0
    # Sanity: the band really is inside the channel.
    boll = ind.bollinger([b.close for b in _flat_wide_range_bars()], 20)[-1]
    kc = ind.keltner(_flat_wide_range_bars(), 20, 20, 1.5)[-1]
    assert boll is not None and kc is not None
    assert boll.upper <= kc.upper and boll.lower >= kc.lower


def test_squeeze_off_when_band_wide() -> None:
    snap = condition_snapshot(_dispersed_tight_range_bars(), "1d")
    assert snap.indicators["squeeze_on"] == 0.0


def test_squeeze_on_none_when_undefined() -> None:
    """Too few bars for the Keltner channel -> squeeze_on is an honest None, not a
    forced 0.0."""

    snap = condition_snapshot(_rising(10), "1d")  # < 21 bars -> Keltner undefined
    assert snap.indicators["squeeze_on"] is None


def test_bb_width_pct90_matches_independent_percentile() -> None:
    """`bb_width` is the latest band-width and `bb_width_pct90` its trailing
    percentile rank, both matching an independent computation off the same bars."""

    bars = _choppy(120)
    closes = [b.close for b in bars]
    # Independent band-width series: (upper - lower) / middle via mean / pstdev.
    bandwidth: list[float | None] = [None] * len(closes)
    for i in range(19, len(closes)):
        window = closes[i - 19 : i + 1]
        mean = math.fsum(window) / len(window)
        var = math.fsum((x - mean) ** 2 for x in window) / len(window)
        sd = math.sqrt(var)
        if mean != 0.0:
            bandwidth[i] = ((mean + 2.0 * sd) - (mean - 2.0 * sd)) / mean
    defined = [v for v in bandwidth if v is not None]
    sample = defined[-90:]
    latest = sample[-1]
    expected_pct = 100.0 * sum(1 for v in sample if v <= latest) / len(sample)

    snap = condition_snapshot(bars, "1d")
    bb_width = snap.indicators["bb_width"]
    bb_width_pct90 = snap.indicators["bb_width_pct90"]
    assert bb_width is not None and bb_width_pct90 is not None
    assert abs(bb_width - latest) < _TOL
    assert abs(bb_width_pct90 - expected_pct) < _TOL
