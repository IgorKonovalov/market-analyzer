"""EMA fast/slow crossover strategy.

Computes two exponential moving averages of `close` and emits `ENTER_LONG`
when the fast EMA crosses above the slow EMA while flat, `EXIT_LONG` when it
crosses below while long. EMAs are seeded with the simple average of the first
`period` closes and advanced via the standard `alpha = 2 / (period + 1)`
recurrence. A decision at `bar_index = i` reads only `bars[0..=i]` — no lookahead.

Cross detection requires two consecutive bars where both EMAs are defined, so
no signal fires on the very first bar where both series have values (their
relative order at that bar is a state, not a transition).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field, model_validator

from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)

META = StrategyMeta(
    id="ema_cross",
    name="EMA Fast/Slow Crossover",
    description=(
        "Enter long when the fast EMA crosses above the slow EMA; exit when it crosses below."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    fast: Annotated[int, Field(ge=2, le=200)] = 12
    slow: Annotated[int, Field(ge=3, le=400)] = 26

    @model_validator(mode="after")
    def _fast_strictly_less_than_slow(self) -> Params:
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be strictly less than slow ({self.slow})")
        return self


def _ema(closes: Sequence[float], period: int) -> list[float | None]:
    """Return the EMA series, with `None` for bars where the EMA is undefined.

    Seeded by SMA of `closes[0..period-1]` at index `period - 1`; subsequent
    bars use `alpha * close + (1 - alpha) * prev` with `alpha = 2 / (period + 1)`.
    """

    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(period, n):
        curr = alpha * closes[i] + (1 - alpha) * prev
        out[i] = curr
        prev = curr
    return out


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    closes = [bar.close for bar in bars]
    fast_series = _ema(closes, params.fast)
    slow_series = _ema(closes, params.slow)

    signals: list[Signal] = []
    position: int = 0
    prev_fast: float | None = None
    prev_slow: float | None = None
    for i in range(len(bars)):
        f = fast_series[i]
        s = slow_series[i]
        if f is None or s is None:
            continue
        if prev_fast is not None and prev_slow is not None:
            crossed_up = prev_fast <= prev_slow and f > s
            crossed_down = prev_fast >= prev_slow and f < s
            if position == 0 and crossed_up:
                signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
                position = 1
            elif position == 1 and crossed_down:
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
                position = 0
        prev_fast = f
        prev_slow = s

    return signals
