"""Classical chart-pattern breakout strategy (Plan 0054).

Turns the *confirmed* classical-pattern breakouts from
`analysis.chart_patterns.detect_chart_patterns` into a tradeable signal stream.
For each `confirmed` `ChartPatternHit` the strategy opens a position in the
pattern's natural direction:

- **long** on a bullish confirmed breakout (inverse head & shoulders, double
  bottom, ascending triangle, falling wedge, and a symmetrical triangle that
  breaks *up*);
- **short** on a bearish confirmed breakout (head & shoulders, double top,
  descending triangle, rising wedge, and a symmetrical triangle that breaks
  *down*) — using the `ENTER_SHORT` / `EXIT_SHORT` kinds from
  [ADR-0050](../../../docs/architecture/adrs/0050-short-selling-strategy-backtest.md).

Direction is read off the **confirmed hit's `direction`** field, not a static
pattern-name table — so a symmetrical triangle (whose direction is only known at
the confirming break) is mapped correctly, and the bullish/bearish split stays
in lockstep with the detector.

The strategy acts only on `confirmed` hits, never on `forming` ones. The
detector guarantees a `confirmed` hit at bar `i` is a fact about `bars[0..=i]`
only (ADR-0048's trailing two-state lifecycle), so the no-lookahead invariant
carries straight through to the signals: a signal at `bar_index == i` depends
only on `bars[0..=i]`, pinned by the truncation test.

**Position model.** Single direction at a time (flat → long *or* short → flat;
the engine enforces this, the strategy emits a conforming sequence). A confirmed
breakout opposite to the open position closes it; whether a flip immediately
opens the new position is governed by `exit_only_on_opposite` (default: yes — the
opposing breakout both exits and re-enters, the textbook "stop and reverse").

**Exit policy** (a `Params` choice, default documented and pinned by the smoke
test):

- *opposing breakout* — always closes the current position (the default exit).
- *measured-move target* — when `use_target=True`, a close reaching the hit's
  measured-move `target` closes the position (a take-profit).
- *stop* — when `stop_loss_pct` is set below 1.0, a close `stop_loss_pct` beyond
  the entry close (against the position) closes it.

All exit checks read only `bars[0..=i]`. Pure, deterministic, trailing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from market_analyser.analysis.chart_patterns import detect_chart_patterns
from market_analyser.analysis.types import ChartPatternHit
from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
)

META = StrategyMeta(
    id="chart_pattern_breakout",
    name="Classical Chart-Pattern Breakout",
    description=(
        "Enter in the natural direction of each confirmed classical chart-pattern "
        "breakout (head & shoulders, double top/bottom, triangles, wedges) — long "
        "on bullish breaks, short on bearish ones — and exit on the opposing "
        "confirmed breakout and/or a measured-move target / stop."
    ),
    version="1.0.0",
    timeframes=("1h", "4h", "1d"),
)

# Pattern families, mapped to the detector's `pattern` strings. The trendline
# family (triangles + wedges) can be disabled wholesale via
# `enable_triangles_wedges` while detection of those shapes is validated on real
# bars (Plan 0054 open question). The pivot-matched family (H&S + doubles) is
# always eligible.
_TRENDLINE_FAMILY: frozenset[str] = frozenset(
    {
        "ascending_triangle",
        "descending_triangle",
        "symmetrical_triangle",
        "rising_wedge",
        "falling_wedge",
    }
)


class Params(BaseParams):
    # Restrict to long-only entries (bullish breakouts). Default: trade both
    # directions, opening shorts on bearish breakouts.
    long_only: bool = False
    # Include the trendline family (triangles + wedges). Off restricts the
    # strategy to the pivot-matched H&S / double patterns.
    enable_triangles_wedges: bool = True
    # When True, the *only* exit is the opposing confirmed breakout (stop-and-
    # reverse). When False, the target / stop below may close a position before
    # an opposing breakout arrives.
    exit_only_on_opposite: bool = True
    # Close on a measured-move target touch (a take-profit). Has no effect when
    # `exit_only_on_opposite` is True.
    use_target: bool = False
    # Fractional adverse move from the entry close that stops the position out.
    # `1.0` disables the stop (a close can never move 100% against an entry in a
    # normal series). Has no effect when `exit_only_on_opposite` is True.
    stop_loss_pct: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0


def _eligible(hit: ChartPatternHit, params: Params) -> bool:
    """A confirmed hit the strategy is allowed to act on under the params."""

    if hit.state != "confirmed":
        return False
    if not params.enable_triangles_wedges and hit.pattern in _TRENDLINE_FAMILY:
        return False
    if hit.direction == "bullish":
        return True
    # A bearish breakout is only tradeable when shorts are enabled.
    return hit.direction == "bearish" and not params.long_only


def _target_hit(target: float | None, close: float, direction: str) -> bool:
    """Whether `close` has reached the measured-move `target` for `direction`."""

    if target is None:
        return False
    if direction == "bullish":
        return close >= target
    return close <= target


def _stopped_out(entry_close: float, close: float, direction: str, stop_pct: float) -> bool:
    """Whether `close` has moved `stop_pct` against a `direction` entry."""

    if stop_pct >= 1.0:
        return False
    if direction == "bullish":  # long: a drop below the entry stops out
        return close <= entry_close * (1.0 - stop_pct)
    return close >= entry_close * (1.0 + stop_pct)  # short: a rise stops out


def generate_signals(bars: Sequence[Bar], params: Params) -> Sequence[Signal]:
    # Confirmed hits keyed by their confirming bar. `detect_chart_patterns` is
    # ordered by (bar_index, pattern, state), so iterating bars in order and
    # picking the hits at each bar preserves a deterministic, trailing sweep.
    hits_by_bar: dict[int, list[ChartPatternHit]] = {}
    for hit in detect_chart_patterns(bars):
        if _eligible(hit, params):
            hits_by_bar.setdefault(hit.bar_index, []).append(hit)

    signals: list[Signal] = []
    # Open position: None (flat) or a "bullish" / "bearish" direction, with its
    # entry close and measured-move target carried alongside.
    direction: str | None = None
    entry_close: float = 0.0
    target: float | None = None

    for i, bar in enumerate(bars):
        close = bar.close

        # 1) Non-breakout exits (target / stop) on the open position — only when
        #    the policy allows exits other than the opposing breakout.
        if direction is not None and not params.exit_only_on_opposite:
            target_reached = params.use_target and _target_hit(target, close, direction)
            stopped = _stopped_out(entry_close, close, direction, params.stop_loss_pct)
            if target_reached or stopped:
                kind = SignalKind.EXIT_LONG if direction == "bullish" else SignalKind.EXIT_SHORT
                signals.append(Signal(bar_index=i, kind=kind))
                direction = None

        # 2) Confirmed breakouts at this bar drive entries / flips.
        for hit in hits_by_bar.get(i, []):
            hit_dir = hit.direction
            if direction == hit_dir:
                continue  # already in the breakout's direction — nothing to do
            if direction is not None:
                # Opposing breakout: close the current position first.
                kind = SignalKind.EXIT_LONG if direction == "bullish" else SignalKind.EXIT_SHORT
                signals.append(Signal(bar_index=i, kind=kind))
                direction = None
            # Open in the breakout's direction (stop-and-reverse, or a fresh entry).
            enter = SignalKind.ENTER_LONG if hit_dir == "bullish" else SignalKind.ENTER_SHORT
            signals.append(Signal(bar_index=i, kind=enter))
            direction = hit_dir
            entry_close = close
            target = hit.target

    return signals
