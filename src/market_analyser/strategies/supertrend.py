"""Supertrend trend-flip strategy.

Builds the standard Supertrend indicator: Wilder-smoothed ATR, basic bands
`hl2 ± multiplier * ATR`, recursive final bands, and a direction state that
flips when close pierces the active band. Emits `ENTER_LONG` on a down→up
flip while flat, `EXIT_LONG` on an up→down flip while long.

The seed convention is "downtrend at the ATR seed bar"; the first flip on or
after `period + 1` is what triggers the first signal. A decision at
`bar_index = i` reads only `bars[0..=i]` — no lookahead.

Recursive band convention (the textbook formulation):

    final_upper[i] = basic_upper[i]
        if basic_upper[i] < final_upper[i-1] or close[i-1] > final_upper[i-1]
        else final_upper[i-1]

    final_lower[i] = basic_lower[i]
        if basic_lower[i] > final_lower[i-1] or close[i-1] < final_lower[i-1]
        else final_lower[i-1]

i.e. the upper band tightens or follows price down; the lower band tightens
or follows price up. Otherwise the prior level persists ("supertrend stays").
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
    id="supertrend",
    name="Supertrend",
    description=(
        "Trend-flip signal off the recursive Supertrend band — enter long on "
        "flip to uptrend, exit on flip to downtrend."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    period: Annotated[int, Field(ge=2, le=200)] = 10
    multiplier: Annotated[float, Field(gt=0.0, le=10.0)] = 3.0


def _true_range(bars: Sequence[Bar]) -> list[float | None]:
    n = len(bars)
    tr: list[float | None] = [None] * n
    for i in range(1, n):
        h, l, prev_c = bars[i].high, bars[i].low, bars[i - 1].close
        tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))
    return tr


def _atr_wilder(tr: Sequence[float | None], period: int) -> list[float | None]:
    """Wilder-smoothed ATR seeded by SMA of `tr[1..period]` (TR is undefined at i=0)."""

    n = len(tr)
    atr: list[float | None] = [None] * n
    if n <= period:
        return atr
    seed_values = tr[1 : period + 1]
    if any(v is None for v in seed_values):
        return atr
    seed = sum(v for v in seed_values if v is not None) / period
    atr[period] = seed
    prev = seed
    for i in range(period + 1, n):
        v = tr[i]
        assert v is not None
        curr = (prev * (period - 1) + v) / period
        atr[i] = curr
        prev = curr
    return atr


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    n = len(bars)
    period = params.period
    multiplier = params.multiplier
    if n <= period + 1:
        return []

    tr = _true_range(bars)
    atr = _atr_wilder(tr, period)

    basic_upper: list[float | None] = [None] * n
    basic_lower: list[float | None] = [None] * n
    for i in range(n):
        a = atr[i]
        if a is None:
            continue
        hl2 = (bars[i].high + bars[i].low) / 2
        basic_upper[i] = hl2 + multiplier * a
        basic_lower[i] = hl2 - multiplier * a

    final_upper: list[float | None] = [None] * n
    final_lower: list[float | None] = [None] * n
    final_upper[period] = basic_upper[period]
    final_lower[period] = basic_lower[period]
    for i in range(period + 1, n):
        bu = basic_upper[i]
        bl = basic_lower[i]
        prev_fu = final_upper[i - 1]
        prev_fl = final_lower[i - 1]
        assert bu is not None and bl is not None
        assert prev_fu is not None and prev_fl is not None
        prev_close = bars[i - 1].close
        final_upper[i] = bu if (bu < prev_fu or prev_close > prev_fu) else prev_fu
        final_lower[i] = bl if (bl > prev_fl or prev_close < prev_fl) else prev_fl

    signals: list[Signal] = []
    position: int = 0
    direction: str = "down"  # "down" => st = final_upper; "up" => st = final_lower
    for i in range(period + 1, n):
        fu = final_upper[i]
        fl = final_lower[i]
        assert fu is not None and fl is not None
        c = bars[i].close
        if direction == "down":
            new_direction = "up" if c > fu else "down"
        else:
            new_direction = "down" if c < fl else "up"
        if new_direction != direction:
            if new_direction == "up" and position == 0:
                signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
                position = 1
            elif new_direction == "down" and position == 1:
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
                position = 0
        direction = new_direction

    return signals
