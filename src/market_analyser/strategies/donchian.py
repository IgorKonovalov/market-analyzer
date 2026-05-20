"""Donchian channel breakout strategy.

Enters long when the close breaks above the upper Donchian channel — the
highest high of the previous `period` bars, strictly excluding the current bar
— while flat. Exits when the close breaks below the lower channel — the
lowest low of the previous `period` bars — while long.

Excluding the current bar from the window keeps the breakout defined against
data strictly in the past: at bar `i` we compare `bars[i].close` against
`max(high)` over `bars[i-period..i-1]`, so no future data leaks in.
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
    id="donchian",
    name="Donchian Channel Breakout",
    description=(
        "Enter long when close breaks above the N-bar prior high; exit when it breaks below the N-bar prior low."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    period: Annotated[int, Field(ge=2, le=400)] = 20


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    n = len(bars)
    period = params.period
    signals: list[Signal] = []
    position: int = 0
    for i in range(period, n):
        window = bars[i - period : i]
        upper = max(b.high for b in window)
        lower = min(b.low for b in window)
        close = bars[i].close
        if position == 0 and close > upper:
            signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
            position = 1
        elif position == 1 and close < lower:
            signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
            position = 0

    return signals
