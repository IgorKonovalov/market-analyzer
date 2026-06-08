"""RSI oversold/overbought strategy with a stop-loss exit (Plan 0050 phase 6).

A stop-loss-bearing variant of the `rsi` reference strategy. Entries are
identical to `rsi`: `ENTER_LONG` when RSI crosses down through `oversold` while
flat. The exit is the earlier of two conditions while long:

- **stop breach** — the close falls to or below `entry_close * (1 - stop_loss_pct)`,
  where `entry_close` is the close of the bar the `ENTER_LONG` was emitted on; or
- **RSI cross-up** — RSI crosses up through `overbought` (the plain `rsi` exit).

The stop is checked first (it is the protective exit). With `stop_loss_pct` set
wide enough never to breach, the signals are byte-identical to `rsi` — the stop
only ever *adds* an earlier exit, it never changes entries.

**Fill model (Plan 0050 phase 6 decision):** the stop is evaluated on *closes*
and emits `EXIT_LONG` at the breaching bar; the engine fills it per its standard
signal-at-close / next-open model. There is no intrabar stop-*price* fill — that
would require engine support and is out of scope (a separate backtester change).

RSI uses the shared in-house implementation (`analysis.indicators.rsi`), which is
byte-for-byte `rsi._wilder_rsi`. A signal at `bar_index = i` depends only on
`bars[0..=i]` — no lookahead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from market_analyser.analysis.indicators import rsi as wilder_rsi
from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)

META = StrategyMeta(
    id="rsi_stop",
    name="RSI Oversold/Overbought with Stop-Loss",
    description=(
        "Enter long when RSI crosses below oversold; exit on the earlier of an RSI "
        "cross above overbought or a stop-loss breach below the entry close."
    ),
    version="1.0.0",
    timeframes=("1h", "1d"),
)


class Params(BaseParams):
    period: Annotated[int, Field(ge=2, le=200)] = 14
    oversold: Annotated[float, Field(ge=0.0, le=100.0)] = 40.0
    overbought: Annotated[float, Field(ge=0.0, le=100.0)] = 60.0
    # Fractional drop from the entry close that triggers the stop. 0.05 = a 5%
    # stop; `le=1.0` allows a (never-triggering) 100% stop to disable it.
    stop_loss_pct: Annotated[float, Field(gt=0.0, le=1.0)] = 0.05


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    closes = [bar.close for bar in bars]
    rsi_series = wilder_rsi(closes, params.period)

    signals: list[Signal] = []
    position: int = 0  # 0 flat, 1 long
    entry_close: float = 0.0  # close of the bar the current long was entered on
    prev_rsi: float | None = None
    for i, current in enumerate(rsi_series):
        if current is None:
            continue
        # Cross-down/-up detection mirrors `rsi` exactly: the previous reading was
        # at/above (oversold) or at/below (overbought) the threshold — or undefined,
        # meaning this is the first computable bar and it landed in the zone — and
        # the current reading is on the other side.
        crossed_down = (prev_rsi is None or prev_rsi >= params.oversold) and (
            current < params.oversold
        )
        crossed_up = (prev_rsi is None or prev_rsi <= params.overbought) and (
            current > params.overbought
        )
        if position == 0 and crossed_down:
            signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
            position = 1
            entry_close = closes[i]
        elif position == 1:
            # Exit on the earlier of a stop breach or the RSI cross-up. Both emit
            # the same EXIT_LONG at this bar, so they collapse to one branch; the
            # stop level is fixed at the entry close and a close at or below it
            # breaches.
            stop_breached = closes[i] <= entry_close * (1.0 - params.stop_loss_pct)
            if stop_breached or crossed_up:
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
                position = 0
        prev_rsi = current

    return signals
