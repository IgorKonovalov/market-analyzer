"""Bollinger band mean-reversion strategy.

Enters long when the close crosses below the lower band; exits when it crosses
above the upper band (the "buy lower, sell upper" mean-reversion variant).
Bands are computed over the most recent `period` closes inclusive of the
current bar — a window that depends only on `bars[0..=i]` and therefore
introduces no lookahead.

The standard deviation here is the population stdev (denominator `N`), which
is the most common Bollinger convention. Switching to the sample stdev would
widen the bands by a factor of `sqrt(N / (N - 1))` without changing the
contract.
"""

from __future__ import annotations

import math
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
    id="bollinger",
    name="Bollinger Band Mean Reversion",
    description=(
        "Enter long when close crosses below the lower band; exit when it crosses above the upper band."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    period: Annotated[int, Field(ge=2, le=400)] = 20
    num_std: Annotated[float, Field(gt=0.0, le=10.0)] = 2.0


def _bands(
    closes: Sequence[float], period: int, num_std: float
) -> tuple[list[float | None], list[float | None]]:
    n = len(closes)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper[i] = mean + num_std * sd
        lower[i] = mean - num_std * sd
    return upper, lower


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    closes = [bar.close for bar in bars]
    upper_series, lower_series = _bands(closes, params.period, params.num_std)

    signals: list[Signal] = []
    position: int = 0
    prev_close: float | None = None
    prev_upper: float | None = None
    prev_lower: float | None = None
    for i in range(len(bars)):
        u = upper_series[i]
        l = lower_series[i]
        if u is None or l is None:
            continue
        c = closes[i]
        if prev_close is not None and prev_upper is not None and prev_lower is not None:
            crossed_down_through_lower = prev_close >= prev_lower and c < l
            crossed_up_through_upper = prev_close <= prev_upper and c > u
            if position == 0 and crossed_down_through_lower:
                signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
                position = 1
            elif position == 1 and crossed_up_through_upper:
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
                position = 0
        prev_close = c
        prev_upper = u
        prev_lower = l

    return signals
