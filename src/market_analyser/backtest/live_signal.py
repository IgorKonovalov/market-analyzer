"""Pure live-signal evaluation core (Plan 0026 phase 1).

`evaluate_signals(strategy_module, bars, params, *, now)` answers the question
the historical backtest path cannot: *what does this strategy say on the
**current** bar?* It runs a strategy's `generate_signals` over fresh bars and
reports the current signal state — implied position, the most-recent signal,
bars-since, and a "fresh signal fired on the last closed bar" flag.

Two behaviours make this a distinct primitive from `signals_to_trades`, not a
thin reuse of it (see Plan 0026 §"Context & problem"):

1. **It does not drop the last-closed-bar signal.** `signals_to_trades` ignores
   any signal with `bar_index > len(bars) - 2` because a historical series has
   no `i+1` open to execute against. In a *live* read the bar after the last
   closed bar is simply the future, so a fresh `enter_long` on the last closed
   bar is the single most important output — the actionable "act at the next
   open when it arrives" signal. We keep it.
2. **It does not apply the `+1` execution offset to position state.** That
   offset is about *price* (you fill at the next open), not about *whether you
   hold*. The implied position is derived by folding the signal stream directly.

The only wall-clock dependence a live read carries is deciding which bars count
as *closed* right now (excluding a still-forming latest bar). That is injected
as `now` so the core itself stays pure and deterministic: same
`(strategy_module, bars, params, now)` in → same `SignalEvaluation` out, with no
clock read inside. Bar duration for the closed-bar test comes from the
`data/timeframes.py` registry, not a private map.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import ModuleType
from typing import Literal

from market_analyser.backtest.adapter import _ordering_key
from market_analyser.backtest.engine import _validate_strategy_module
from market_analyser.backtest.types import EvaluatedSignal, SignalEvaluation
from market_analyser.contracts.strategy import BaseParams, Signal, SignalKind
from market_analyser.data.timeframes import timeframe_spec
from market_analyser.data.types import Bar


def evaluate_signals(
    strategy_module: ModuleType,
    bars: Sequence[Bar],
    params: BaseParams | None = None,
    *,
    now: datetime,
) -> SignalEvaluation:
    """Evaluate `strategy_module` against the current bar of `bars`.

    `bars` is the full fetched series (chronological, single timeframe); the
    timeframe and symbol are read from the bars themselves. `params` is an
    already-validated `Params` instance, or `None` to use the strategy's
    defaults. `now` is the current wall-clock instant (UTC, timezone-aware),
    injected so the core stays deterministic — it is used *only* to exclude a
    not-yet-closed latest bar.

    Returns a `SignalEvaluation`. An empty signal stream (e.g. too few bars for
    the strategy's indicator to warm up) is a valid `flat` / `last_signal=None`
    result, not an error — the caller controls warm-up via how much history it
    supplies.

    Raises `ValueError` if `bars` is empty, if `now` is not timezone-aware, or
    if every bar is still forming relative to `now` (no closed bar to evaluate).
    """

    _validate_strategy_module(strategy_module)

    if not bars:
        raise ValueError("bars must not be empty")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")

    timeframe = bars[0].timeframe
    duration = timeframe_spec(timeframe).bar_duration

    # A bar is closed once a full duration has elapsed since it opened. Bars are
    # chronological, so the closed bars are always a prefix: once one bar is
    # still forming every later bar is too. The latest bar is the only one that
    # can realistically be forming on a contiguous series.
    closed_bars = [bar for bar in bars if bar.event_ts + duration <= now]
    if not closed_bars:
        raise ValueError(
            f"no closed bars: all {len(bars)} bar(s) are still forming relative to now={now!r}"
        )

    latest_bar_excluded_as_forming = len(closed_bars) < len(bars)
    last_closed_index = len(closed_bars) - 1

    params_instance: BaseParams = params if params is not None else strategy_module.Params()
    raw_signals: Sequence[Signal] = strategy_module.generate_signals(closed_bars, params_instance)

    # Same ordering the engine's adapter applies (ADR-0050): ascending bar,
    # exits before entries within a bar. This keeps the implied position — and
    # the reported `last_signal` — consistent with how a backtest would execute
    # a same-bar exit+enter flip, regardless of the strategy's emission order.
    signals = sorted(raw_signals, key=_ordering_key)

    current_position = _fold_position(signals)
    last_signal = signals[-1] if signals else None

    evaluated: EvaluatedSignal | None
    bars_since_last_signal: int | None
    fresh_signal: bool
    if last_signal is None:
        evaluated = None
        bars_since_last_signal = None
        fresh_signal = False
    else:
        evaluated = EvaluatedSignal(
            kind=last_signal.kind,
            bar_index=last_signal.bar_index,
            event_ts=closed_bars[last_signal.bar_index].event_ts,
            reason=last_signal.reason,
        )
        bars_since_last_signal = last_closed_index - last_signal.bar_index
        fresh_signal = last_signal.bar_index == last_closed_index

    return SignalEvaluation(
        strategy_id=strategy_module.META.id,
        symbol=closed_bars[0].symbol,
        timeframe=timeframe,
        evaluated_through_ts=closed_bars[-1].event_ts,
        closed_bar_count=len(closed_bars),
        latest_bar_excluded_as_forming=latest_bar_excluded_as_forming,
        current_position=current_position,
        last_signal=evaluated,
        bars_since_last_signal=bars_since_last_signal,
        fresh_signal=fresh_signal,
    )


def _fold_position(signals: Sequence[Signal]) -> Literal["flat", "long", "short"]:
    """Replay the (ordered) signal stream into the implied flat/long/short position.

    Mirrors the `signals_to_trades` state machine *minus* the execution offset
    and the last-bar drop: `enter_long`/`enter_short` while flat opens the
    matching direction; `exit_long` while long and `exit_short` while short
    return to flat; everything else is a no-op (no pyramiding, no simultaneous
    long+short, no closing a position of the other direction — ADR-0050).
    Callers pass the adapter-ordered stream so same-bar exit+enter flips fold
    exit-first, exactly as the engine executes them.
    """

    position: Literal["flat", "long", "short"] = "flat"
    for signal in signals:
        if position == "flat":
            if signal.kind is SignalKind.ENTER_LONG:
                position = "long"
            elif signal.kind is SignalKind.ENTER_SHORT:
                position = "short"
        elif (position == "long" and signal.kind is SignalKind.EXIT_LONG) or (
            position == "short" and signal.kind is SignalKind.EXIT_SHORT
        ):
            position = "flat"
    return position


__all__ = ["evaluate_signals"]
