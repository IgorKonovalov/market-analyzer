"""Pure watch evaluation core (Plan 0060 phase 2, ADR-0055).

`evaluate_watch(watch, bars, *, now) -> bool` answers: *is this watch's
condition true on the latest closed bar?* Three kinds, each wrapping an
existing read-only primitive:

- ``indicator_threshold`` — the indicator's latest value on the closed bars
  compared against the level (`analysis/indicators.py` / `analysis/volume.py`,
  the ADR-0023 surface);
- ``pattern`` — the named candlestick pattern completes on the latest closed
  bar (`analysis/patterns.py`);
- ``strategy_signal`` — the strategy reports `fresh_signal`
  (`backtest/live_signal.py`).

No wall-clock, no I/O: the scheduler injects `bars` and `now`. `now` exists
*only* to exclude a still-forming latest bar — the same no-lookahead rule the
live-signal core carries (a forming-bar value crossing a threshold must not
fire; the bar can still close on the other side). Same
`(watch, bars, now)` in → same result out.

`should_fire(last_state, current)` is the edge-transition reducer (ADR-0055):
a watch fires only on the false→true transition between consecutive
evaluations. A condition staying true across N polls yields exactly one alert;
`last_state=None` (a fresh watch, or one whose memory was reset) *arms* the
detector without firing — there is no known previous `False` to transition
from, so a watch created while its condition is already true waits for the
condition to go false and come back.

`evaluate_watch_detail` is the same evaluation returning the condition *fact*
alongside the bool — the human-readable string and the numbers behind it that
the scheduler puts in the `alert.triggered v1` payload (phase 3). Facts only,
never a directive (ADR-0029).
"""

from __future__ import annotations

import operator as op
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.alerts.types import (
    IndicatorThresholdParams,
    PatternParams,
    StrategySignalParams,
    Watch,
)
from market_analyser.analysis import indicators as ind
from market_analyser.analysis.patterns import detect_patterns
from market_analyser.analysis.volume import volume_summary
from market_analyser.backtest.live_signal import evaluate_signals
from market_analyser.contracts.strategy import BaseParams, discover
from market_analyser.data.timeframes import timeframe_spec
from market_analyser.data.types import Bar

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": op.lt,
    "<=": op.le,
    ">": op.gt,
    ">=": op.ge,
}


class EvaluationDetail(BaseModel):
    """One evaluation's outcome plus the condition fact behind it.

    `condition` is the human-readable statement (e.g. ``rsi 28.44 < 30``) and
    `values` the numbers it was computed from — exactly what the
    `alert.triggered v1` payload carries. Deliberately no direction/action/
    conviction field (ADR-0029 boundary).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: bool
    condition: str
    values: dict[str, float]


def evaluate_watch(watch: Watch, bars: Sequence[Bar], *, now: datetime) -> bool:
    """Evaluate `watch` against the latest closed bar of `bars`. Pure."""
    return evaluate_watch_detail(watch, bars, now=now).result


def evaluate_watch_detail(watch: Watch, bars: Sequence[Bar], *, now: datetime) -> EvaluationDetail:
    """Evaluate `watch` and return the outcome with its condition fact.

    `bars` is the full fetched series (chronological, single timeframe); a
    still-forming latest bar is excluded via the watch's timeframe bar
    duration relative to `now`. An empty series — or one with no closed bar
    yet — is an honest "condition not true" (`False` with an explanatory
    condition string), not an error: the scheduler controls how much history
    it supplies.

    Raises `ValueError` for a naive `now`, and for a ``strategy_signal``
    watch naming an unknown strategy or carrying params its `Params` model
    rejects — a watch that can *never* evaluate should fail loudly (the
    scheduler contains the exception and surfaces it in the heartbeat) rather
    than sit silently false forever.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")

    duration = timeframe_spec(watch.timeframe).bar_duration
    closed_bars = [bar for bar in bars if bar.event_ts + duration <= now]
    if not closed_bars:
        return EvaluationDetail(
            result=False,
            condition="no closed bars to evaluate",
            values={},
        )

    params = watch.params
    if isinstance(params, IndicatorThresholdParams):
        return _evaluate_indicator_threshold(params, closed_bars)
    if isinstance(params, PatternParams):
        return _evaluate_pattern(params, closed_bars)
    return _evaluate_strategy_signal(params, closed_bars, now=now)


def should_fire(last_state: bool | None, current: bool) -> bool:
    """The edge-transition reducer: fire only on false→true.

    `None` (no previous evaluation) arms without firing; `True→True` holds
    silent; `True→False` re-arms so the next `True` fires again.
    """
    return current and last_state is False


def _evaluate_indicator_threshold(
    params: IndicatorThresholdParams, closed_bars: Sequence[Bar]
) -> EvaluationDetail:
    value = _indicator_value(params.indicator, closed_bars)
    if value is None:
        return EvaluationDetail(
            result=False,
            condition=(
                f"{params.indicator} undefined over {len(closed_bars)} closed bar(s) "
                f"(not enough history)"
            ),
            values={"level": params.level},
        )
    result = _OPERATORS[params.operator](value, params.level)
    return EvaluationDetail(
        result=result,
        condition=f"{params.indicator} {value:.6g} {params.operator} {params.level:g}",
        values={params.indicator: value, "level": params.level},
    )


def _evaluate_pattern(params: PatternParams, closed_bars: Sequence[Bar]) -> EvaluationDetail:
    last_index = len(closed_bars) - 1
    hit = next(
        (
            h
            for h in detect_patterns(closed_bars)
            if h.pattern == params.pattern and h.bar_index == last_index
        ),
        None,
    )
    if hit is None:
        return EvaluationDetail(
            result=False,
            condition=f"{params.pattern} did not print on the latest closed bar",
            values={},
        )
    return EvaluationDetail(
        result=True,
        condition=f"{params.pattern} printed on the latest closed bar",
        values={"strength": hit.strength},
    )


def _evaluate_strategy_signal(
    params: StrategySignalParams, closed_bars: Sequence[Bar], *, now: datetime
) -> EvaluationDetail:
    strategies = discover()
    if params.strategy_id not in strategies:
        raise ValueError(
            f"unknown strategy_id {params.strategy_id!r}; known: {sorted(strategies)}",
        )
    strategy_module = strategies[params.strategy_id]
    # Boundary-validate the stored params against the strategy's own model —
    # raises pydantic.ValidationError on violation (contained by the scheduler).
    params_instance: BaseParams = strategy_module.Params(**params.params)

    # `closed_bars` are already closed relative to `now`, so the core's own
    # forming-bar exclusion is a no-op here — passing `now` through keeps one
    # source of truth for the closed-bar rule.
    evaluation = evaluate_signals(strategy_module, closed_bars, params_instance, now=now)
    if not evaluation.fresh_signal or evaluation.last_signal is None:
        return EvaluationDetail(
            result=False,
            condition=f"strategy {params.strategy_id} has no fresh signal",
            values={},
        )
    return EvaluationDetail(
        result=True,
        condition=(
            f"strategy {params.strategy_id} emitted {evaluation.last_signal.kind.value} "
            f"on the latest closed bar"
        ),
        values={},
    )


def _last(series: Sequence[float | None]) -> float | None:
    return next((v for v in reversed(series) if v is not None), None)


def _indicator_value(indicator_id: str, bars: Sequence[Bar]) -> float | None:
    """The latest defined value of one ADR-0023-surface indicator, or `None`
    when the available closed bars are too few for it to warm up.

    Mirrors `analysis/snapshot.py`'s composition (same primitives, same
    periods) for the scalar ids the watch vocabulary admits — kept local so
    a tick computes only the one indicator it needs, not the full snapshot.
    """
    closes = [b.close for b in bars]

    if indicator_id == "close":
        return closes[-1]
    if indicator_id == "rsi":
        return _last(ind.rsi(closes, 14))
    if indicator_id in ("macd", "macd_signal", "macd_hist"):
        last_macd = next((v for v in reversed(ind.macd(closes)) if v is not None), None)
        if last_macd is None:
            return None
        return {
            "macd": last_macd.macd,
            "macd_signal": last_macd.signal,
            "macd_hist": last_macd.histogram,
        }[indicator_id]
    if indicator_id in ("bb_upper", "bb_middle", "bb_lower", "bb_pct_b"):
        last_boll = next((v for v in reversed(ind.bollinger(closes, 20)) if v is not None), None)
        if last_boll is None:
            return None
        if indicator_id == "bb_pct_b":
            if last_boll.upper == last_boll.lower:
                return None
            return (closes[-1] - last_boll.lower) / (last_boll.upper - last_boll.lower)
        return {
            "bb_upper": last_boll.upper,
            "bb_middle": last_boll.middle,
            "bb_lower": last_boll.lower,
        }[indicator_id]
    if indicator_id == "atr":
        return _last(ind.atr(bars, 14))
    if indicator_id in ("adx", "plus_di", "minus_di"):
        last_adx = next((v for v in reversed(ind.adx(bars, 14)) if v is not None), None)
        if last_adx is None:
            return None
        return {
            "adx": last_adx.adx,
            "plus_di": last_adx.plus_di,
            "minus_di": last_adx.minus_di,
        }[indicator_id]
    if indicator_id in ("supertrend", "supertrend_direction"):
        last_st = next((v for v in reversed(ind.supertrend(bars, 10)) if v is not None), None)
        if last_st is None:
            return None
        return last_st.value if indicator_id == "supertrend" else float(last_st.direction)
    volume_ids = ("volume", "vol_sma20", "rel_volume", "vol_pct90", "obv", "obv_slope", "vwap")
    if indicator_id in volume_ids:
        summary = volume_summary(bars)
        volume_value: float | None = {
            "volume": summary.latest_volume,
            "vol_sma20": summary.volume_sma,
            "rel_volume": summary.relative_volume,
            "vol_pct90": summary.volume_percentile,
            "obv": summary.obv,
            "obv_slope": summary.obv_slope,
            "vwap": summary.vwap,
        }[indicator_id]
        return volume_value
    # Unreachable for a validated Watch (the params model's Literal is the
    # gate); loud for a raw string sneaking around the boundary.
    raise ValueError(f"unknown indicator id: {indicator_id!r}")


__all__ = [
    "EvaluationDetail",
    "evaluate_watch",
    "evaluate_watch_detail",
    "should_fire",
]
