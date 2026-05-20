"""RSI oversold/overbought reference strategy.

Computes Wilder's smoothed RSI inline; emits `ENTER_LONG` when RSI crosses
down through `oversold` while flat, and `EXIT_LONG` when RSI crosses up
through `overbought` while long. Cross detection (not "is below") prevents
duplicate entries while RSI lingers in a zone.

A signal at `bar_index = i` depends only on `bars[0..=i]` — no lookahead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)

META = StrategyMeta(
    id="rsi",
    name="RSI Oversold/Overbought",
    description=(
        "Enter long when RSI crosses below oversold; exit when it crosses above overbought."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    period: Annotated[int, Field(ge=2, le=200)] = 14
    oversold: Annotated[float, Field(ge=0.0, le=100.0)] = 40.0
    overbought: Annotated[float, Field(ge=0.0, le=100.0)] = 60.0


def _wilder_rsi(closes: Sequence[float], period: int) -> list[float | None]:
    """Return the RSI series, with `None` for bars where RSI is undefined.

    Wilder's smoothing: the first `period` changes are simple-averaged for the
    seed; subsequent bars use the recurrence
    `avg = (avg_prev * (period - 1) + value) / period`.
    """

    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi

    gains_sum = 0.0
    losses_sum = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains_sum += change
        else:
            losses_sum += -change

    avg_gain = gains_sum / period
    avg_loss = losses_sum / period
    rsi[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi[i] = _rsi_from(avg_gain, avg_loss)

    return rsi


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    closes = [bar.close for bar in bars]
    rsi_series = _wilder_rsi(closes, params.period)

    signals: list[Signal] = []
    position: int = 0  # 0 flat, 1 long
    prev_rsi: float | None = None
    for i, current in enumerate(rsi_series):
        if current is None:
            continue
        # Cross-down through oversold: previous reading was at or above
        # the threshold (or undefined, meaning this is the first computable
        # bar and it landed in the zone) and the current reading is below.
        crossed_down = (prev_rsi is None or prev_rsi >= params.oversold) and (
            current < params.oversold
        )
        crossed_up = (prev_rsi is None or prev_rsi <= params.overbought) and (
            current > params.overbought
        )
        if position == 0 and crossed_down:
            signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
            position = 1
        elif position == 1 and crossed_up:
            signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
            position = 0
        prev_rsi = current

    return signals
