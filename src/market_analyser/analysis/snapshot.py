"""Composed technical-condition snapshot (Plan 0018 phase 3, ADR-0023).

`condition_snapshot(bars, timeframe)` composes the phase-1 indicators and phase-2
patterns into a single trailing read: latest indicator values, a trend
classification (EMA stack + ADX), a momentum stance (RSI zone refined by MACD),
trailing support/resistance pivots, and the candlestick hits on the most recent
bars. Pure and trailing — every input is `bars[0..=last]`, so a snapshot computed
on a truncated series matches the full-series state as of the truncation bar.

Reports **conditions only** — never buy/sell (the analyst non-negotiable); the
`ConditionSnapshot` model has no action field.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.patterns import detect_patterns
from market_analyser.analysis.types import (
    ConditionSnapshot,
    MomentumStance,
    Trend,
)
from market_analyser.data.types import Bar

# --- Tunable classification thresholds -------------------------------------- #
EMA_SHORT = 20
EMA_LONG = 50
ADX_TREND_MIN = 20.0  # ADX below this -> no decisive trend (sideways)
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RECENT_PATTERN_BARS = 5  # patterns on the last N bars are "recent"
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
    """Trailing swing pivots: a bar `j` is a resistance pivot when its high is the
    max of the `2*window+1` bars centered on it, and a support pivot when its low
    is the min. Only pivots with a full window on each side (all bars <= last) are
    confirmed, so the levels read only `bars[0..=last]` — no lookahead relative to
    the snapshot's as-of bar."""

    w = SR_PIVOT_WINDOW
    n = len(bars)
    resistances: list[tuple[int, float]] = []
    supports: list[tuple[int, float]] = []
    for j in range(w, n - w):
        neighbours = [*bars[j - w : j], *bars[j + 1 : j + w + 1]]
        if bars[j].high > max(b.high for b in neighbours):  # strict local max
            resistances.append((j, bars[j].high))
        if bars[j].low < min(b.low for b in neighbours):  # strict local min
            supports.append((j, bars[j].low))
    recent_res = sorted({p for _, p in resistances[-SR_MAX_LEVELS:]})
    recent_sup = sorted({p for _, p in supports[-SR_MAX_LEVELS:]})
    return {"support": recent_sup, "resistance": recent_res}


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

    trend = _classify_trend(closes, indicator_values["adx"])
    momentum = _classify_momentum(rsi_val, indicator_values["macd_hist"])

    recent_cutoff = len(bars) - RECENT_PATTERN_BARS
    recent_patterns = [h for h in detect_patterns(bars) if h.bar_index >= recent_cutoff]

    return ConditionSnapshot(
        symbol=bars[-1].symbol,
        timeframe=timeframe,
        as_of=bars[-1].event_ts,
        trend=trend,
        momentum=momentum,
        indicators=indicator_values,
        support_resistance=_support_resistance(bars),
        recent_patterns=recent_patterns,
    )


__all__ = ["condition_snapshot"]
