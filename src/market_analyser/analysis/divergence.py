"""Price↔oscillator divergence detection (Plan 0091 phase 4, ADR-0023).

`detect_divergences(bars, oscillator, ...)` pairs the two most recent confirmed
price swing pivots of a kind against the oscillator's own pivots and classifies
regular / hidden bullish / bearish divergence. Every input is `bars[0..=last]` and
every pivot is *confirmed* (a full `pivot_window` of right-context inside the
series), so a divergence reported at bar `i` is byte-identical on `bars[0..=i]` —
no future pivot leaks in (the load-bearing anti-lookahead property tested in
`tests/analysis/test_divergence.py`).

The pivot-pairing heuristic and its constants (`DIVERGENCE_LOOKBACK`,
`PIVOT_WINDOW`, `MIN_PIVOT_SEPARATION`) are owned like the candlestick thresholds
ADR-0023 already covers — named here, pinned by fixtures, ours to re-tune. No
pandas/numpy. Conditions only: a divergence is chart geometry, never a buy/sell
call (the `Divergence` model has no action field).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from market_analyser.analysis import indicators as ind
from market_analyser.analysis import volume as vol
from market_analyser.analysis.levels import swing_pivots
from market_analyser.analysis.types import Divergence, DivergenceKind, Pivot, PivotPoint
from market_analyser.data.types import Bar

Oscillator = Literal["rsi", "macd_hist", "obv", "mfi"]

# --- Divergence heuristic tunables (named constants, ADR-0023-owned) --------- #
DIVERGENCE_LOOKBACK = 60  # only pivots within the trailing this-many bars are paired
PIVOT_WINDOW = 3  # left/right wings for both price and oscillator pivots (SR_PIVOT_WINDOW)
MIN_PIVOT_SEPARATION = 3  # the two paired pivots must be at least this many bars apart
_EPS = 1e-12


@dataclass(frozen=True)
class _SeriesPivot:
    """A confirmed local extreme of a scalar oscillator series (the oscillator's
    analogue of `Pivot`, carrying the oscillator *value* rather than a price)."""

    bar_index: int
    value: float
    kind: Literal["high", "low"]


def _oscillator_series(bars: Sequence[Bar], oscillator: Oscillator) -> list[float | None]:
    """The scalar series the divergence pairs price against, aligned to `bars`."""

    if oscillator == "rsi":
        return ind.rsi([b.close for b in bars], 14)
    if oscillator == "macd_hist":
        return [v.histogram if v is not None else None for v in ind.macd([b.close for b in bars])]
    if oscillator == "obv":
        return vol.obv(bars)
    return vol.mfi(bars)


def _series_pivots(series: Sequence[float | None], window: int) -> list[_SeriesPivot]:
    """Confirmed strict local extremes of a scalar series over a `window`-wide
    two-sided neighbourhood — the oscillator counterpart of `swing_pivots`, sharing
    its confirmed-only (trailing) discipline. Undefined (`None`) neighbourhoods are
    skipped."""

    n = len(series)
    pivots: list[_SeriesPivot] = []
    for j in range(window, n - window):
        cur = series[j]
        if cur is None:
            continue
        neighbours = [series[k] for k in (*range(j - window, j), *range(j + 1, j + window + 1))]
        if any(v is None for v in neighbours):
            continue
        defined = [v for v in neighbours if v is not None]
        if cur > max(defined):
            pivots.append(_SeriesPivot(bar_index=j, value=cur, kind="high"))
        if cur < min(defined):
            pivots.append(_SeriesPivot(bar_index=j, value=cur, kind="low"))
    return pivots


def _nearest(pivots: Sequence[_SeriesPivot], bar_index: int) -> _SeriesPivot | None:
    """The oscillator pivot whose bar is closest to `bar_index` (deterministic tie-
    break toward the earlier bar), or `None` when there are none."""

    if not pivots:
        return None
    return min(pivots, key=lambda p: (abs(p.bar_index - bar_index), p.bar_index))


def _sign(delta: float) -> int:
    if delta > _EPS:
        return 1
    if delta < -_EPS:
        return -1
    return 0


def _classify(kind: Literal["high", "low"], price_dir: int, osc_dir: int) -> DivergenceKind | None:
    """Map a (pivot kind, price slope, oscillator slope) triple to a divergence
    class, or `None` when the two slopes agree (no divergence)."""

    if kind == "high":  # bearish family — compare swing highs
        if price_dir > 0 and osc_dir < 0:
            return "regular_bearish"  # higher high, lower oscillator high
        if price_dir < 0 and osc_dir > 0:
            return "hidden_bearish"  # lower high, higher oscillator high
        return None
    if price_dir < 0 and osc_dir > 0:
        return "regular_bullish"  # lower low, higher oscillator low
    if price_dir > 0 and osc_dir < 0:
        return "hidden_bullish"  # higher low, lower oscillator low
    return None


def _strength(p1: Pivot, p2: Pivot, o1: _SeriesPivot, o2: _SeriesPivot) -> float:
    """A detector-defined 0..1 magnitude blending the price and oscillator slope
    fractions — larger swings on both legs read as a stronger divergence. Not a
    probability. Bounded and divide-by-zero-guarded."""

    price_frac = abs(p2.price - p1.price) / (max(abs(p1.price), abs(p2.price)) + _EPS)
    osc_frac = abs(o2.value - o1.value) / (abs(o1.value) + abs(o2.value) + _EPS)
    return min(1.0, (price_frac + osc_frac) / 2.0)


def _detect_for_kind(
    bars: Sequence[Bar],
    oscillator: Oscillator,
    price_pivots: Sequence[Pivot],
    osc_pivots: Sequence[_SeriesPivot],
    kind: Literal["high", "low"],
    window_start: int,
    window: int,
) -> Divergence | None:
    """Detect at most one divergence of `kind` from the two most recent in-window
    price pivots and their nearest oscillator pivots."""

    price_k = [p for p in price_pivots if p.kind == kind and p.bar_index >= window_start]
    osc_k = [p for p in osc_pivots if p.kind == kind and p.bar_index >= window_start]
    if len(price_k) < 2 or len(osc_k) < 2:
        return None
    p1, p2 = price_k[-2], price_k[-1]  # older, newer
    if p2.bar_index - p1.bar_index < MIN_PIVOT_SEPARATION:
        return None

    o1 = _nearest(osc_k, p1.bar_index)
    o2 = _nearest(osc_k, p2.bar_index)
    if o1 is None or o2 is None or o1.bar_index == o2.bar_index:
        return None
    # Each oscillator pivot must actually line up with its price pivot.
    if abs(o1.bar_index - p1.bar_index) > window or abs(o2.bar_index - p2.bar_index) > window:
        return None

    price_dir = _sign(p2.price - p1.price)
    osc_dir = _sign(o2.value - o1.value)
    if price_dir == 0 or osc_dir == 0:
        return None
    label = _classify(kind, price_dir, osc_dir)
    if label is None:
        return None

    # Knowable once the later of the four pivots is confirmed (a full right-window).
    confirm_bar = max(p2.bar_index, o2.bar_index) + window
    return Divergence(
        oscillator=oscillator,
        kind=label,
        price_pivots=[
            PivotPoint(ts=p1.ts, price=p1.price),
            PivotPoint(ts=p2.ts, price=p2.price),
        ],
        oscillator_pivots=[
            PivotPoint(ts=bars[o1.bar_index].event_ts, price=o1.value),
            PivotPoint(ts=bars[o2.bar_index].event_ts, price=o2.value),
        ],
        bar_index=confirm_bar,
        strength=_strength(p1, p2, o1, o2),
    )


def detect_divergences(
    bars: Sequence[Bar],
    oscillator: Oscillator = "rsi",
    lookback: int = DIVERGENCE_LOOKBACK,
    pivot_window: int = PIVOT_WINDOW,
) -> list[Divergence]:
    """Regular / hidden bullish / bearish divergences between price and `oscillator`.

    Pairs the two most recent confirmed price `swing_pivots` of each kind (highs →
    bearish family, lows → bullish family) against the oscillator's own confirmed
    pivots (nearest by bar), within the trailing `lookback` window. Returns at most
    one divergence per pivot kind (so at most two), ordered by `bar_index` then
    `kind`. Empty when nothing qualifies — never a fabricated hit.

    Trailing by construction: every pivot needs a full `pivot_window` of
    right-context inside `bars`, so no bar beyond the series end is read.
    """

    if oscillator not in get_args(Oscillator):
        raise ValueError(f"oscillator must be one of {get_args(Oscillator)}, got {oscillator!r}")
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    if pivot_window < 1:
        raise ValueError(f"pivot_window must be >= 1, got {pivot_window}")
    if not bars:
        return []

    osc_series = _oscillator_series(bars, oscillator)
    price_pivots = swing_pivots(bars, left=pivot_window, right=pivot_window)
    osc_pivots = _series_pivots(osc_series, pivot_window)
    window_start = len(bars) - lookback

    results: list[Divergence] = []
    for kind in ("high", "low"):
        div = _detect_for_kind(
            bars, oscillator, price_pivots, osc_pivots, kind, window_start, pivot_window
        )
        if div is not None:
            results.append(div)
    results.sort(key=lambda d: (d.bar_index, d.kind))
    return results


__all__ = [
    "DIVERGENCE_LOOKBACK",
    "MIN_PIVOT_SEPARATION",
    "PIVOT_WINDOW",
    "Oscillator",
    "detect_divergences",
]
