"""Composed technical-condition snapshot (Plan 0018 phase 3, ADR-0023).

`condition_snapshot(bars, timeframe)` composes the phase-1 indicators and phase-2
patterns into a single trailing read: latest indicator values, a trend
classification (EMA stack + ADX), a momentum stance (RSI zone refined by MACD),
trailing support/resistance pivots plus the nearest clustered support/resistance
`Level` framing the last close (Plan 0051 phase 4), the candlestick hits on
the most recent bars, and the classical chart patterns still in play (Plan 0052
phase 3). Pure and trailing — every input is `bars[0..=last]`, so a snapshot
computed on a truncated series matches the full-series state as of the
truncation bar.

Reports **conditions only** — never buy/sell (the analyst non-negotiable); the
`ConditionSnapshot` model has no action field.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.chart_patterns import (
    BREAKOUT_SCAN_MAX_BARS,
    detect_chart_patterns,
)
from market_analyser.analysis.levels import support_resistance_levels, swing_pivots
from market_analyser.analysis.patterns import detect_patterns
from market_analyser.analysis.types import (
    ChartPatternHit,
    ConditionSnapshot,
    Level,
    MomentumStance,
    Trend,
)
from market_analyser.analysis.volume import volume_summary
from market_analyser.data.types import Bar

# --- Tunable classification thresholds -------------------------------------- #
EMA_SHORT = 20
EMA_LONG = 50
ADX_TREND_MIN = 20.0  # ADX below this -> no decisive trend (sideways)
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RECENT_PATTERN_BARS = 5  # patterns on the last N bars are "recent"
# A classical chart pattern is "active" while its completing/confirming bar is
# within the detector's breakout scan horizon of the snapshot bar — the window
# in which a forming pattern can still confirm (Plan 0052, ADR-0048).
ACTIVE_PATTERN_BARS = BREAKOUT_SCAN_MAX_BARS
PERCENTILE_WINDOW = 90  # trailing window for rsi/atr percentile rank
SR_PIVOT_WINDOW = 3  # bars on each side of a confirmed swing pivot
SR_MAX_LEVELS = 5  # keep at most this many of the most recent levels per side


def _last(series: Sequence[float | None]) -> float | None:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _percentile_rank(series: Sequence[float | None], window: int) -> float | None:
    """Trailing percentile rank (0..100) of the latest defined value among the most
    recent `window` defined values. Pure and trailing — no future bars."""

    defined = [v for v in series if v is not None]
    if not defined:
        return None
    sample = defined[-window:]
    latest = sample[-1]
    below_or_equal = sum(1 for v in sample if v <= latest)
    return 100.0 * below_or_equal / len(sample)


def _classify_trend(closes: Sequence[float], adx_val: float | None) -> Trend:
    ema_s = _last(ind.ema(closes, EMA_SHORT))
    ema_l = _last(ind.ema(closes, EMA_LONG))
    close = closes[-1]
    strong = adx_val is not None and adx_val >= ADX_TREND_MIN
    if ema_s is not None and ema_l is not None:
        if strong and ema_s > ema_l and close >= ema_s:
            return Trend.UP
        if strong and ema_s < ema_l and close <= ema_s:
            return Trend.DOWN
        return Trend.SIDEWAYS
    if ema_s is not None and strong:
        if close > ema_s:
            return Trend.UP
        if close < ema_s:
            return Trend.DOWN
    return Trend.SIDEWAYS


def _classify_momentum(rsi_val: float | None, macd_hist: float | None) -> MomentumStance:
    if rsi_val is None:
        return MomentumStance.NEUTRAL
    if rsi_val >= RSI_OVERBOUGHT:
        return MomentumStance.OVERBOUGHT
    if rsi_val <= RSI_OVERSOLD:
        return MomentumStance.OVERSOLD
    if macd_hist is not None and macd_hist > 0:
        return MomentumStance.BULLISH
    if macd_hist is not None and macd_hist < 0:
        return MomentumStance.BEARISH
    return MomentumStance.NEUTRAL


def _support_resistance(bars: Sequence[Bar]) -> dict[str, list[float]]:
    """Trailing swing-pivot levels, delegating to the public `swing_pivots`
    primitive (`analysis/levels.py`, Plan 0051). Only pivots with a full window
    on each side (all bars <= last) are confirmed, so the levels read only
    `bars[0..=last]` — no lookahead relative to the snapshot's as-of bar. Output
    is unchanged by the extraction: the most recent `SR_MAX_LEVELS` pivot prices
    per side, deduplicated and sorted ascending."""

    pivots = swing_pivots(bars, left=SR_PIVOT_WINDOW, right=SR_PIVOT_WINDOW)
    resistances = [p.price for p in pivots if p.kind == "high"]
    supports = [p.price for p in pivots if p.kind == "low"]
    recent_res = sorted(set(resistances[-SR_MAX_LEVELS:]))
    recent_sup = sorted(set(supports[-SR_MAX_LEVELS:]))
    return {"support": recent_sup, "resistance": recent_res}


def _active_patterns(bars: Sequence[Bar]) -> list[ChartPatternHit]:
    """Classical chart patterns still in play at the snapshot bar (Plan 0052):
    hits whose completing/confirming `bar_index` falls within the trailing
    `ACTIVE_PATTERN_BARS` window, reduced to the latest-state hit per formation
    (a formation's `confirmed` hit supersedes its earlier `forming` one). A
    `forming` hit can later vanish when the window slides past or a pivot
    invalidates it — correct trailing behavior (ADR-0048), read as provisional.
    Order is the detector's deterministic `(bar_index, pattern, state)`."""

    cutoff = len(bars) - 1 - ACTIVE_PATTERN_BARS
    recent = [h for h in detect_chart_patterns(bars) if h.bar_index >= cutoff]
    latest: dict[tuple[str, tuple[tuple[datetime, float], ...]], ChartPatternHit] = {}
    order: list[tuple[str, tuple[tuple[datetime, float], ...]]] = []
    for hit in recent:
        key = (hit.pattern, tuple((p.ts, p.price) for p in hit.pivots))
        if key not in latest:
            order.append(key)
        # Detector order is bar_index-ascending with forming before confirmed,
        # so the last hit per formation is its latest state.
        latest[key] = hit
    return [latest[key] for key in order]


def _nearest_levels(levels: Sequence[Level], close: float) -> tuple[Level | None, Level | None]:
    """The clustered level framing the last close on each side (Plan 0051 phase 4):
    nearest support at-or-below `close` and nearest resistance at-or-above it.
    A level on the wrong side of the close (a support left above it after a
    breakdown, a resistance left below after a breakout) is not "nearest" — it
    is excluded, yielding ``None`` rather than a misleading frame."""

    supports = [lv for lv in levels if lv.role == "support" and lv.price <= close]
    resistances = [lv for lv in levels if lv.role == "resistance" and lv.price >= close]
    nearest_support = max(supports, key=lambda lv: lv.price, default=None)
    nearest_resistance = min(resistances, key=lambda lv: lv.price, default=None)
    return nearest_support, nearest_resistance


def condition_snapshot(bars: Sequence[Bar], timeframe: str) -> ConditionSnapshot:
    """Compose indicators + patterns into a single trailing condition read.

    Requires at least one bar. `symbol`/`as_of` are taken from the last bar.
    """

    if not bars:
        raise ValueError("condition_snapshot requires at least one bar")
    closes = [b.close for b in bars]

    rsi_series = ind.rsi(closes, 14)
    macd_series = ind.macd(closes)
    boll_series = ind.bollinger(closes, 20)
    atr_series = ind.atr(bars, 14)
    adx_series = ind.adx(bars, 14)
    st_series = ind.supertrend(bars, 10)

    rsi_val = _last(rsi_series)
    atr_val = _last(atr_series)
    last_macd = next((v for v in reversed(macd_series) if v is not None), None)
    last_boll = next((v for v in reversed(boll_series) if v is not None), None)
    last_adx = next((v for v in reversed(adx_series) if v is not None), None)
    last_st = next((v for v in reversed(st_series) if v is not None), None)

    bb_pct_b: float | None = None
    if last_boll is not None and last_boll.upper != last_boll.lower:
        bb_pct_b = (closes[-1] - last_boll.lower) / (last_boll.upper - last_boll.lower)

    indicator_values: dict[str, float | None] = {
        "rsi": rsi_val,
        "rsi_pct90": _percentile_rank(rsi_series, PERCENTILE_WINDOW),
        "macd": last_macd.macd if last_macd else None,
        "macd_signal": last_macd.signal if last_macd else None,
        "macd_hist": last_macd.histogram if last_macd else None,
        "bb_upper": last_boll.upper if last_boll else None,
        "bb_middle": last_boll.middle if last_boll else None,
        "bb_lower": last_boll.lower if last_boll else None,
        "bb_pct_b": bb_pct_b,
        "atr": atr_val,
        "atr_pct90": _percentile_rank(atr_series, PERCENTILE_WINDOW),
        "adx": last_adx.adx if last_adx else None,
        "plus_di": last_adx.plus_di if last_adx else None,
        "minus_di": last_adx.minus_di if last_adx else None,
        "supertrend": last_st.value if last_st else None,
        "supertrend_direction": float(last_st.direction) if last_st else None,
    }

    volume = volume_summary(bars)
    indicator_values.update(
        {
            "volume": volume.latest_volume,
            "vol_sma20": volume.volume_sma,
            "rel_volume": volume.relative_volume,
            "vol_pct90": volume.volume_percentile,
            "obv": volume.obv,
            "obv_slope": volume.obv_slope,
            "vwap": volume.vwap,
        }
    )

    trend = _classify_trend(closes, indicator_values["adx"])
    momentum = _classify_momentum(rsi_val, indicator_values["macd_hist"])

    recent_cutoff = len(bars) - RECENT_PATTERN_BARS
    recent_patterns = [h for h in detect_patterns(bars) if h.bar_index >= recent_cutoff]

    nearest_support, nearest_resistance = _nearest_levels(
        support_resistance_levels(bars), closes[-1]
    )

    return ConditionSnapshot(
        symbol=bars[-1].symbol,
        timeframe=timeframe,
        as_of=bars[-1].event_ts,
        trend=trend,
        momentum=momentum,
        volume_stance=volume.stance,
        indicators=indicator_values,
        support_resistance=_support_resistance(bars),
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        recent_patterns=recent_patterns,
        active_patterns=_active_patterns(bars),
    )


__all__ = ["condition_snapshot"]
