"""Support/resistance primitives: confirmed swing pivots (Plan 0051, ADR-0023).

`swing_pivots` is the public, reusable extraction of the swing-pivot logic that
previously lived module-private inside `snapshot.py::_support_resistance`. It is
the shared foundation classical-pattern detection (Plan 0052) consumes, so its
signature and semantics are deliberately small and stable:

    swing_pivots(bars, left=3, right=3) -> list[Pivot]

A bar `j` is a `high` pivot when its high strictly exceeds the highs of the
`left` bars before it AND the `right` bars after it; a `low` pivot is the mirror
on lows. Only *confirmed* pivots are returned: bar `j` needs all `right` bars of
right-context inside the input series, so a pivot forming at bar `j` is first
knowable at bar `j + right` and `swing_pivots(bars[: k + 1])` returns exactly
the full-series pivots with `bar_index <= k - right`. Appending future bars
never changes or removes an already-confirmed pivot — the anti-lookahead
property pinned by the truncation-invariance test in
`tests/analysis/test_levels.py`.

Pure, trailing, deterministic, no pandas/numpy (ADR-0023): output order is by
`bar_index` ascending, with a same-bar `high` pivot before the `low` one.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.analysis.types import Pivot
from market_analyser.data.types import Bar

# Default pivot wings: a 3-left/3-right window matches the 3-bar centred window
# the snapshot's private helper has always used (SR_PIVOT_WINDOW).
DEFAULT_PIVOT_LEFT = 3
DEFAULT_PIVOT_RIGHT = 3


def swing_pivots(
    bars: Sequence[Bar],
    left: int = DEFAULT_PIVOT_LEFT,
    right: int = DEFAULT_PIVOT_RIGHT,
) -> list[Pivot]:
    """Confirmed swing pivots over `bars`, ordered by `bar_index` ascending.

    A `high` pivot at `j` has `bars[j].high` strictly above every high in the
    `left` bars before and the `right` bars after it; a `low` pivot mirrors on
    lows. A bar can be both (a `high` pivot is emitted before the same bar's
    `low` pivot). Only pivots with a full `right`-bar window inside the series
    are confirmed — no future bar beyond the series end is read.
    """

    if left < 1:
        raise ValueError(f"left must be >= 1, got {left}")
    if right < 1:
        raise ValueError(f"right must be >= 1, got {right}")
    n = len(bars)
    pivots: list[Pivot] = []
    for j in range(left, n - right):
        neighbours = [*bars[j - left : j], *bars[j + 1 : j + right + 1]]
        if bars[j].high > max(b.high for b in neighbours):  # strict local max
            pivots.append(Pivot(bar_index=j, ts=bars[j].event_ts, price=bars[j].high, kind="high"))
        if bars[j].low < min(b.low for b in neighbours):  # strict local min
            pivots.append(Pivot(bar_index=j, ts=bars[j].event_ts, price=bars[j].low, kind="low"))
    return pivots


__all__ = [
    "DEFAULT_PIVOT_LEFT",
    "DEFAULT_PIVOT_RIGHT",
    "swing_pivots",
]
