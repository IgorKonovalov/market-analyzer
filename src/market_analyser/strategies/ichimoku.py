"""Ichimoku Tenkan/Kijun-cross strategy, gated by the cloud (Plan 0075).

Turns the Ichimoku Kinkō Hyō reading into a tradeable signal stream. The
canonical entry is a **Tenkan/Kijun (TK) cross confirmed by the cloud**:

- **long** when Tenkan crosses *above* Kijun and (by default) the close is
  above the cloud sitting under the current bar;
- **short** on the mirror — Tenkan crosses *below* Kijun and the close is below
  the cloud — using the `ENTER_SHORT` / `EXIT_SHORT` kinds from
  [ADR-0050](../../../docs/architecture/adrs/0050-short-selling-strategy-backtest.md)
  (suppressed when `long_only=True`).

All Ichimoku math is imported from `analysis.indicators.ichimoku` — no inline
re-implementation (the ADR-0023 single-source discipline `chart_pattern_breakout`
follows by importing `analysis.chart_patterns`). That series is **trailing**: the
value at bar `i` is computed from `bars[0..=i]`. Displacement is a *consumption*
concern applied here, not baked into the series — the cloud sitting under bar `i`
is `values[i - displacement]` (`senkou_a`/`senkou_b` there), and the Chikou read
compares `close[i]` to `close[i - displacement]`. Both are trailing look-backs, so
a signal at `bar_index == i` depends only on `bars[0..=i]` — the no-lookahead
invariant, pinned by the truncation test.

**Position model.** Single direction at a time (flat → long *or* short → flat;
the engine enforces this, the strategy emits a conforming sequence). A *confirmed*
opposing TK cross closes the current position and — unless suppressed by
`long_only` — reverses into the new one (the textbook "stop and reverse").

**Exit policy.**

- *opposing confirmed cross* — always the default exit (stop-and-reverse).
- *cloud re-entry* — when `exit_on_cloud_cross=True`, a close that falls back
  *inside* the cloud under the current bar flattens the position before any
  opposing cross arrives.

Pure, deterministic, trailing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from market_analyser.analysis.indicators import IchimokuValue, ichimoku
from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)

META = StrategyMeta(
    id="ichimoku",
    name="Ichimoku TK Cross",
    description=(
        "Enter on a Tenkan/Kijun cross confirmed by the cloud — long when Tenkan "
        "crosses above Kijun with price above the cloud, short on the mirror — with "
        "optional Chikou confirmation, and exit by stop-and-reverse or a cloud "
        "re-entry."
    ),
    version="1.0.0",
    timeframes=("1h", "4h", "1d"),
)


class Params(BaseParams):
    # Ichimoku periods (classic 9/26/52/26). `displacement` shifts the cloud/Chikou
    # reads only — the imported series stays trailing.
    conversion: Annotated[int, Field(ge=1, le=200)] = 9
    base: Annotated[int, Field(ge=1, le=400)] = 26
    span_b: Annotated[int, Field(ge=1, le=520)] = 52
    displacement: Annotated[int, Field(ge=1, le=200)] = 26
    # Require the close to be above (long) / below (short) the cloud under the
    # current bar for the cross to be tradeable. The classic reading.
    require_cloud_confirmation: bool = True
    # Also require the current close to be above/below the close `displacement`
    # bars ago, in the trade's direction (a trailing Chikou check).
    require_chikou_confirmation: bool = False
    # Restrict to long-only entries. Off (default) trades both directions, opening
    # shorts on bearish confirmed crosses (ADR-0050).
    long_only: bool = False
    # Flatten a position when the close re-enters the cloud, rather than waiting for
    # the opposing confirmed cross.
    exit_on_cloud_cross: bool = False


def _cloud_bounds(cloud: IchimokuValue) -> tuple[float, float]:
    """(bottom, top) of the cloud — the min/max of its two Senkou spans."""

    return min(cloud.senkou_a, cloud.senkou_b), max(cloud.senkou_a, cloud.senkou_b)


def _confirmed(
    direction: str,
    close: float,
    cloud: IchimokuValue | None,
    past_close: float | None,
    params: Params,
) -> bool:
    """Whether a candidate `direction` entry clears the cloud/Chikou gates.

    `cloud` is the Ichimoku value under the current bar (`values[i-displacement]`)
    or `None` when that index is undefined; `past_close` is `close[i-displacement]`
    or `None` when that index is out of range. A required gate whose data is not
    yet available withholds the entry.
    """

    if params.require_cloud_confirmation:
        if cloud is None:
            return False
        bottom, top = _cloud_bounds(cloud)
        if direction == "long" and not close > top:
            return False
        if direction == "short" and not close < bottom:
            return False
    if params.require_chikou_confirmation:
        if past_close is None:
            return False
        if direction == "long" and not close > past_close:
            return False
        if direction == "short" and not close < past_close:
            return False
    return True


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    n = len(bars)
    values = ichimoku(
        bars,
        conversion=params.conversion,
        base=params.base,
        span_b=params.span_b,
        displacement=params.displacement,
    )
    disp = params.displacement

    signals: list[Signal] = []
    direction: str | None = None  # "long" | "short" | None (flat)

    for i in range(1, n):
        curr = values[i]
        prev = values[i - 1]
        if curr is None or prev is None:
            continue  # Ichimoku not yet defined for a TK-cross read

        close = bars[i].close
        cloud_idx = i - disp
        cloud = values[cloud_idx] if cloud_idx >= 0 else None
        past_close = bars[cloud_idx].close if cloud_idx >= 0 else None

        # 1) Cloud-re-entry exit (only when enabled and a cloud sits under bar i).
        if direction is not None and params.exit_on_cloud_cross and cloud is not None:
            bottom, top = _cloud_bounds(cloud)
            if bottom <= close <= top:
                kind = SignalKind.EXIT_LONG if direction == "long" else SignalKind.EXIT_SHORT
                signals.append(Signal(bar_index=i, kind=kind))
                direction = None

        # 2) TK cross → confirmed entries / stop-and-reverse.
        bull_cross = prev.tenkan <= prev.kijun and curr.tenkan > curr.kijun
        bear_cross = prev.tenkan >= prev.kijun and curr.tenkan < curr.kijun

        long_signal = bull_cross and _confirmed("long", close, cloud, past_close, params)
        short_signal = (
            bear_cross
            and not params.long_only
            and _confirmed("short", close, cloud, past_close, params)
        )

        if long_signal and direction != "long":
            if direction == "short":
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_SHORT))
            signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_LONG))
            direction = "long"
        elif short_signal and direction != "short":
            if direction == "long":
                signals.append(Signal(bar_index=i, kind=SignalKind.EXIT_LONG))
            signals.append(Signal(bar_index=i, kind=SignalKind.ENTER_SHORT))
            direction = "short"

    return signals
