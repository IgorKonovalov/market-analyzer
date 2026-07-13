"""Price-action market-structure read (Plan 0092 phase 2, ADR-0084, ADR-0023).

`market_structure(bars)` labels the confirmed `swing_pivots` sequence as
HH/HL/LH/LL, derives a `structural_trend` from the labels, and detects the
break-of-structure (BOS) and change-of-character (CHoCH) events where a close
takes out a prior confirmed swing extreme. This is a *second, distinct* trend
lens (ADR-0084): it is reported alongside the composed indicator `trend`, never
folded into it, and the two may legitimately disagree.

Everything is trailing. Labels compare a pivot to the previous *confirmed*
same-kind pivot (never a future one). Events walk the bars left-to-right: a level
is only referenceable once its pivot has confirmed (a full pivot-window of
right-context inside `bars`), and a break is detected from that bar's own close —
so an event at bar ``i`` needs nothing beyond ``bars[0..=i]``. A read computed on
a truncated series therefore matches the full-series read as of the truncation bar
(the anti-lookahead property pinned in `tests/analysis/test_structure.py`).

The pivot window, the ATR-scaled break margin, and the ATR period are named
constants, ours to re-tune like the candlestick thresholds ADR-0023 covers. Pure,
deterministic, no pandas/numpy. Conditions only — a structural read is chart
geometry, never a buy/sell call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.levels import swing_pivots
from market_analyser.analysis.types import (
    MarketStructure,
    Pivot,
    PivotPoint,
    StructureEvent,
    StructureLabel,
)
from market_analyser.data.types import Bar

# --- Structure tunables (named constants, ADR-0084-owned) ------------------- #
SR_PIVOT_WINDOW = 3  # left/right wings for confirmed swing pivots (matches the snapshot)
ATR_PERIOD = 14  # trailing ATR window for the break margin
BOS_MARGIN_ATR = 0.25  # a close must clear a level by this * ATR to count as a break

_Bias = Literal["up", "down"]


def _label_pivots(pivots: Sequence[Pivot]) -> list[tuple[PivotPoint, StructureLabel]]:
    """Label each pivot that has a same-kind predecessor: a high is ``HH`` when it
    exceeds the previous high (else ``LH``); a low is ``HL`` when it exceeds the
    previous low (else ``LL``). The first high and first low have no predecessor and
    are unlabeled (excluded). Ordered by `bar_index` (the input order)."""

    labeled: list[tuple[PivotPoint, StructureLabel]] = []
    prev_high: float | None = None
    prev_low: float | None = None
    for p in pivots:
        anchor = PivotPoint(ts=p.ts, price=p.price)
        if p.kind == "high":
            if prev_high is not None:
                label: StructureLabel = "HH" if p.price > prev_high else "LH"
                labeled.append((anchor, label))
            prev_high = p.price
        else:
            if prev_low is not None:
                label = "HL" if p.price > prev_low else "LL"
                labeled.append((anchor, label))
            prev_low = p.price
    return labeled


def _structural_trend(
    labeled: Sequence[tuple[PivotPoint, StructureLabel]],
) -> Literal["up", "down", "range"]:
    """Derive the structural trend from the most recent high and low labels:
    ``up`` = latest high is HH **and** latest low is HL; ``down`` = latest high is
    LH **and** latest low is LL; ``range`` otherwise (mixed, or a side missing)."""

    last_high = next((lbl for _, lbl in reversed(labeled) if lbl in ("HH", "LH")), None)
    last_low = next((lbl for _, lbl in reversed(labeled) if lbl in ("HL", "LL")), None)
    if last_high == "HH" and last_low == "HL":
        return "up"
    if last_high == "LH" and last_low == "LL":
        return "down"
    return "range"


def market_structure(
    bars: Sequence[Bar],
    pivot_window: int = SR_PIVOT_WINDOW,
    bos_margin_atr: float = BOS_MARGIN_ATR,
    atr_period: int = ATR_PERIOD,
) -> MarketStructure:
    """The HH/HL/LH/LL labeling, `structural_trend`, and BOS/CHoCH events over
    `bars` (ADR-0084).

    Events: walking the bars, the most recently confirmed swing high and swing low
    are the active reference levels. When a bar's close clears the active high by
    `bos_margin_atr * ATR` it is an upside break; clearing below the active low is a
    downside break. A break *with* the current bias is a ``BOS`` (continuation); a
    break *against* it is a ``CHoCH`` (change of character). The very first break
    (no bias yet) establishes the bias and is a ``BOS``. A broken level is consumed
    (the next confirmed pivot of that kind becomes the new reference).
    """

    if pivot_window < 1:
        raise ValueError(f"pivot_window must be >= 1, got {pivot_window}")
    if bos_margin_atr < 0.0:
        raise ValueError(f"bos_margin_atr must be >= 0, got {bos_margin_atr}")
    if not bars:
        return MarketStructure(structural_trend="range", labeled_pivots=[], events=[])

    pivots = swing_pivots(bars, left=pivot_window, right=pivot_window)
    labeled = _label_pivots(pivots)

    # A pivot at bar j is first usable as a reference level once its right-context
    # exists (bar j + pivot_window) — the same bar it becomes confirmed.
    confirmed_at: dict[int, list[Pivot]] = {}
    for p in pivots:
        confirmed_at.setdefault(p.bar_index + pivot_window, []).append(p)

    atr_series = ind.atr(bars, atr_period)
    events: list[StructureEvent] = []
    bias: _Bias | None = None
    ref_high: Pivot | None = None
    ref_low: Pivot | None = None

    for i in range(len(bars)):
        for p in confirmed_at.get(i, []):
            if p.kind == "high":
                ref_high = p
            else:
                ref_low = p
        close = bars[i].close
        atr = atr_series[i]
        margin = (atr if atr is not None else 0.0) * bos_margin_atr

        if ref_high is not None and close > ref_high.price + margin:
            # With bias, an upside break is a BOS (continuation); against a `down`
            # bias it is the first counter-trend break — a CHoCH. No bias yet -> BOS.
            events.append(
                StructureEvent(
                    kind="CHoCH" if bias == "down" else "BOS",
                    direction="bullish",
                    bar_index=i,
                    price=ref_high.price,
                )
            )
            bias = "up"
            ref_high = None  # consumed — await the next confirmed high
        elif ref_low is not None and close < ref_low.price - margin:
            events.append(
                StructureEvent(
                    kind="CHoCH" if bias == "up" else "BOS",
                    direction="bearish",
                    bar_index=i,
                    price=ref_low.price,
                )
            )
            bias = "down"
            ref_low = None  # consumed — await the next confirmed low

    return MarketStructure(
        structural_trend=_structural_trend(labeled),
        labeled_pivots=labeled,
        events=events,
    )


__all__ = [
    "ATR_PERIOD",
    "BOS_MARGIN_ATR",
    "SR_PIVOT_WINDOW",
    "market_structure",
]
