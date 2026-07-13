"""Fibonacci retracement / extension grids over confirmed swings (Plan 0092, ADR-0023).

`fibonacci_retracement(high_anchor, low_anchor)` draws the canonical retracement
grid *inside* a swing; `fibonacci_extension(high_anchor, low_anchor, pullback)`
projects the extension grid *beyond* it. Both take `PivotPoint` anchors (a
`(ts, price)` geometry anchor — the same shape the chart's trendline primitive
consumes, ADR-0049) and infer the swing `direction` from the anchors' temporal
order: an up-swing (`bullish`) has the low printing before the high; a down-swing
(`bearish`) the mirror. The grid is oriented by that direction so ratio 0 sits at
the swing's *end* and ratio 1 at its *start* — a retracement measures how far
price pulls back toward the start of the move.

`dominant_swing(bars)` auto-anchors: it picks the largest-magnitude leg between
two consecutive confirmed `swing_pivots` of opposite kind, within a trailing
lookback window. Because it reads only confirmed pivots (a full pivot-window of
right-context inside `bars`), the auto-anchor — and therefore the grid — is
trailing: appending future bars never changes a level already reported (the
anti-lookahead property pinned in `tests/analysis/test_fibonacci.py`).

The ratio sets and the auto-anchor lookback are named constants, ours to re-tune
like the candlestick thresholds ADR-0023 already covers. Pure, deterministic, no
pandas/numpy. Conditions only — a fib grid is chart geometry, never a buy/sell
call (the `FibonacciLevels` model has no action field).
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from market_analyser.analysis.levels import (
    DEFAULT_PIVOT_LEFT,
    DEFAULT_PIVOT_RIGHT,
    swing_pivots,
)
from market_analyser.analysis.types import Direction, FibonacciLevels, PivotPoint
from market_analyser.data.types import Bar

# --- Ratio sets (named constants, ADR-0023-owned) --------------------------- #
# Retracement ratios sit inside the swing (0..1); extension ratios project beyond
# it (>1). The canonical grids most traders mark.
RETRACEMENT_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSION_RATIOS: tuple[float, ...] = (1.272, 1.618, 2.0, 2.618)

# Only legs whose pivots fall within this trailing bar window are candidates for
# the auto-anchor — "the dominant *recent* swing", not the biggest leg ever.
DOMINANT_SWING_LOOKBACK = 120


def _ratio_key(ratio: float) -> str:
    """The dict key for a ratio: its shortest round-trip decimal string (``0.5`` ->
    ``"0.5"``, ``0.618`` -> ``"0.618"``, ``2.0`` -> ``"2.0"``). Deterministic
    (Python's shortest-repr) and hand-readable — the label the UI draws."""

    return str(ratio)


def _direction(high_anchor: PivotPoint, low_anchor: PivotPoint) -> Direction:
    """The swing's direction from the anchors' temporal order: ``bullish`` when the
    low printed at-or-before the high (an up-swing), ``bearish`` when the high
    printed first (a down-swing)."""

    return "bullish" if low_anchor.ts <= high_anchor.ts else "bearish"


def fibonacci_retracement(high_anchor: PivotPoint, low_anchor: PivotPoint) -> FibonacciLevels:
    """The retracement grid inside the swing bounded by `high_anchor`/`low_anchor`.

    For an up-swing (`bullish`, low before high) a level at ratio `r` is
    ``high - r * (high - low)`` — price retraces down from the high toward the low
    (ratio 0 = high, ratio 1 = low). For a down-swing (`bearish`) it is
    ``low + r * (high - low)`` — the mirror, retracing up from the low. Direction
    is inferred from the anchors' timestamps.
    """

    high = high_anchor.price
    low = low_anchor.price
    span = high - low
    direction = _direction(high_anchor, low_anchor)
    levels: dict[str, float] = {}
    for ratio in RETRACEMENT_RATIOS:
        if direction == "bullish":
            levels[_ratio_key(ratio)] = high - ratio * span
        else:
            levels[_ratio_key(ratio)] = low + ratio * span
    return FibonacciLevels(
        kind="retracement",
        high_anchor=high_anchor,
        low_anchor=low_anchor,
        direction=direction,
        levels=levels,
    )


def fibonacci_extension(
    high_anchor: PivotPoint, low_anchor: PivotPoint, pullback: PivotPoint
) -> FibonacciLevels:
    """The extension grid projected beyond the swing from a `pullback` anchor.

    Given the impulse leg (`high_anchor`/`low_anchor`) and the counter-trend
    `pullback` that followed it, a level at ratio `r` (> 1) projects the impulse's
    span off the pullback: for an up-swing (`bullish`) ``pullback + r * (high -
    low)`` (targets above the high), for a down-swing (`bearish`) ``pullback - r *
    (high - low)`` (targets below the low). Direction is inferred from the
    impulse anchors' timestamps.
    """

    high = high_anchor.price
    low = low_anchor.price
    span = high - low
    direction = _direction(high_anchor, low_anchor)
    base = pullback.price
    levels: dict[str, float] = {}
    for ratio in EXTENSION_RATIOS:
        if direction == "bullish":
            levels[_ratio_key(ratio)] = base + ratio * span
        else:
            levels[_ratio_key(ratio)] = base - ratio * span
    return FibonacciLevels(
        kind="extension",
        high_anchor=high_anchor,
        low_anchor=low_anchor,
        direction=direction,
        levels=levels,
    )


def dominant_swing(
    bars: Sequence[Bar],
    left: int = DEFAULT_PIVOT_LEFT,
    right: int = DEFAULT_PIVOT_RIGHT,
    lookback: int = DOMINANT_SWING_LOOKBACK,
) -> tuple[PivotPoint, PivotPoint] | None:
    """The dominant recent swing's `(high_anchor, low_anchor)`, or `None`.

    Reads confirmed `swing_pivots`, keeps those whose bar falls within the trailing
    `lookback` window, and returns the largest-magnitude leg between two
    *consecutive* pivots of opposite kind (a high and an adjacent low). Ties break
    toward the more recent leg. Same-bar high/low pairs (a bar that is both) are
    not a swing leg and are skipped. Returns `None` when no opposite-kind adjacent
    leg exists in the window.

    Trailing by construction: only confirmed pivots feed the choice, so the
    returned anchors are byte-identical on `bars[0..=i]` once the later pivot
    confirms — appending future bars cannot re-anchor a past grid.
    """

    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    pivots = swing_pivots(bars, left=left, right=right)
    if bars:
        cutoff = len(bars) - lookback
        pivots = [p for p in pivots if p.bar_index >= cutoff]

    best_span = -1.0
    best: tuple[PivotPoint, PivotPoint] | None = None
    for a, b in pairwise(pivots):
        if a.kind == b.kind or a.bar_index == b.bar_index:
            continue
        span = abs(a.price - b.price)
        if span >= best_span:  # >= so a later equal-span leg wins the tie
            best_span = span
            high_p, low_p = (a, b) if a.kind == "high" else (b, a)
            best = (
                PivotPoint(ts=high_p.ts, price=high_p.price),
                PivotPoint(ts=low_p.ts, price=low_p.price),
            )
    return best


__all__ = [
    "DOMINANT_SWING_LOOKBACK",
    "EXTENSION_RATIOS",
    "RETRACEMENT_RATIOS",
    "dominant_swing",
    "fibonacci_extension",
    "fibonacci_retracement",
]
