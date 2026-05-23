"""MACD line / signal-line crossover strategy.

Computes `MACD = EMA(close, fast) - EMA(close, slow)`, then a signal line as
`EMA(MACD, signal)`. Enters long when the MACD line crosses above the signal
line while flat, exits when it crosses below while long.

All EMAs are seeded with the simple average of the first `period` defined
values and advanced via `alpha = 2 / (period + 1)`. The signal line therefore
becomes defined only after `slow + signal - 1` bars (slow EMA needs `slow`
closes; the signal line then needs `signal` MACD values). A decision at
`bar_index = i` reads only `bars[0..=i]` — no lookahead.
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
    id="macd",
    name="MACD Line/Signal Crossover",
    description=("Enter long when MACD crosses above its signal line; exit when it crosses below."),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    fast: Annotated[int, Field(ge=2, le=200)] = 12
    slow: Annotated[int, Field(ge=3, le=400)] = 26
    signal: Annotated[int, Field(ge=2, le=200)] = 9

    @model_validator(mode="after")
    def _fast_strictly_less_than_slow(self) -> Params:
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be strictly less than slow ({self.slow})")
        return self


def _ema(values: Sequence[float | None], period: int) -> list[float | None]:
    """EMA of a value series, seeded by SMA of the first `period` consecutive defined values.

    Tolerates a leading run of `None` (the case for MACD/signal in this
    strategy: both have a defined prefix shorter than the full series). Assumes
    the value stream is dense once it starts; collapses back to `None` on any
    interior gap.
    """

    n = len(values)
    out: list[float | None] = [None] * n
    first_defined = next((j for j, v in enumerate(values) if v is not None), None)
    if first_defined is None:
        return out
    seed_end = first_defined + period - 1
    if seed_end >= n:
        return out
    seed_window = values[first_defined : seed_end + 1]
    if any(v is None for v in seed_window):
        return out
    seed = sum(v for v in seed_window if v is not None) / period
    out[seed_end] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(seed_end + 1, n):
        v = values[i]
        if v is None:
            return out
        curr = alpha * v + (1 - alpha) * prev
        out[i] = curr
        prev = curr
    return out


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    closes: list[float | None] = [bar.close for bar in bars]
    fast_ema = _ema(closes, params.fast)
    slow_ema = _ema(closes, params.slow)
    macd: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema, strict=True)
    ]
    signal_line = _ema(macd, params.signal)

    signals: list[Signal] = []
    position: int = 0
    prev_macd: float | None = None
    prev_signal: float | None = None
    for i in range(len(bars)):
        m = macd[i]
        sg = signal_line[i]
        if m is None or sg is None:
            continue
        if prev_macd is not None and prev_signal is not None:
            crossed_up = prev_macd <= prev_signal and m > sg
            crossed_down = prev_macd >= prev_signal and m < sg
            if position == 0 and crossed_up:
                signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
                position = 1
            elif position == 1 and crossed_down:
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
                position = 0
        prev_macd = m
        prev_signal = sg

    return signals
