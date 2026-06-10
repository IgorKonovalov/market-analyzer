"""Classical chart-pattern detection (Plan 0052, ADR-0048).

`detect_chart_patterns(bars)` recognises multi-pivot formations over the shared
`swing_pivots` primitive (`analysis/levels.py`, Plan 0051), with two recognition
models and a strictly-trailing two-state lifecycle:

- *Pivot-matched* — head & shoulders, inverse head & shoulders, double top,
  double bottom: geometric relations among an ordered run of consecutive
  confirmed pivots (relative extremum heights, a symmetry tolerance, a neckline
  through the intervening troughs/peaks).
- *Trendline-fit* — ascending / descending / symmetrical triangle, rising /
  falling wedge: connect-the-extremes — an upper line through the two highest
  recent swing highs and a lower line through the two lowest recent swing lows,
  classified by the two line slopes and their convergence. The lines always sit
  on prices the market truly touched (ADR-0048 rejected regression fits).

Lifecycle (the event semantics that make the no-lookahead invariant exact):
the detector emits a `forming` hit at the bar where a formation's geometry
first completes — the bar at which its last defining pivot confirms, i.e.
`pivot.bar_index + PIVOT_RIGHT` — and a `confirmed` hit at the first later bar
whose close breaks the neckline / breakout trendline by `BREAKOUT_ATR_MULT *
ATR`. Both events are facts about `bars[0..=bar_index]` only, so a hit
reported at bar `i` is byte-identical when the series is truncated to
`bars[0..=i]` — the truncation-invariance test pins this per pattern. A
formation whose close never breaks (or that invalidates first) simply never
yields a `confirmed` hit.

Every threshold is a named module constant (the candlestick-detector stance:
tests assert internal consistency against these constants, not agreement with
any external library). Pure, trailing, deterministic, no pandas/numpy
(ADR-0023). Hits are derived and never persisted (ADR-0048).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from market_analyser.analysis.indicators import atr
from market_analyser.analysis.levels import swing_pivots
from market_analyser.analysis.types import (
    ChartPatternHit,
    Direction,
    LineSeg,
    PatternState,
    Pivot,
    PivotPoint,
)
from market_analyser.data.types import Bar

# --- Pivot extraction --------------------------------------------------------- #
# Wings of the confirmed-swing-pivot window (the shared Plan 0051 primitive).
# A pivot printing at bar j is first knowable at bar j + PIVOT_RIGHT.
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# --- Formation width ---------------------------------------------------------- #
# Bars between the first and last defining pivot. Too narrow is noise, too wide
# is two unrelated swings, not one formation.
PATTERN_MIN_WIDTH_BARS = 10
PATTERN_MAX_WIDTH_BARS = 120

# --- Breakout confirmation (ADR-0048's k * ATR rule) --------------------------- #
ATR_PERIOD = 14
BREAKOUT_ATR_MULT = 0.5  # k: close must clear the line by k * ATR to confirm
# A formation whose break has not come within this many bars of its completion
# bar is stale — stop scanning (it can still be reported `forming`).
BREAKOUT_SCAN_MAX_BARS = 60

# --- Pivot-matched tolerances -------------------------------------------------- #
# Shoulders within this fraction of the head price of each other.
HS_SHOULDER_SYMMETRY_TOL = 0.05
# The two neckline anchors (troughs for H&S, peaks for inverse) within this
# fraction of their mean of each other.
NECKLINE_FLATNESS_TOL = 0.05
# The two tops/bottoms of a double within this fraction of their mean.
DOUBLE_MATCH_TOL = 0.02
# The middle pullback must retrace at least this fraction off the extremes —
# otherwise two nearby pivots with a shallow dip are not a "double" anything.
DOUBLE_PULLBACK_MIN_PCT = 0.01

# --- Trendline-fit tolerances --------------------------------------------------- #
# Pivots older than this many bars before the evaluation bar are out of the
# connect-the-extremes window.
TRENDLINE_WINDOW_BARS = 60
# Slopes are price-relative per bar: (p2 - p1) / (x2 - x1) / mean(p1, p2).
# A line is "flat" below FLAT_SLOPE_TOL_PER_BAR and "trending" above
# MIN_TREND_SLOPE_PER_BAR; the dead zone between them classifies as neither
# (deliberate — it suppresses marginal triangles).
FLAT_SLOPE_TOL_PER_BAR = 0.0005
MIN_TREND_SLOPE_PER_BAR = 0.002
# Lines converge when the relative gap shrinks by at least this much per bar
# (lower slope minus upper slope).
TRENDLINE_CONVERGENCE_MIN = 0.0005
# Convergence rate at (or above) which a converging pattern's strength is 1.0.
TRENDLINE_STRENGTH_CONVERGENCE_REF = 0.005

_PIVOT_MATCHED_PATTERNS = (
    "head_shoulders",
    "inverse_head_shoulders",
    "double_top",
    "double_bottom",
)
_TRENDLINE_PATTERNS = (
    "ascending_triangle",
    "descending_triangle",
    "symmetrical_triangle",
    "rising_wedge",
    "falling_wedge",
)
CHART_PATTERNS: tuple[str, ...] = _PIVOT_MATCHED_PATTERNS + _TRENDLINE_PATTERNS

_STATE_ORDER: dict[str, int] = {"forming": 0, "confirmed": 1}


def _point(pivot: Pivot) -> PivotPoint:
    return PivotPoint(ts=pivot.ts, price=pivot.price)


def _line_value(x1: int, p1: float, x2: int, p2: float, x: int) -> float:
    """Price of the line through `(x1, p1)`-`(x2, p2)` at bar `x` (bar-index
    interpolation/extrapolation — deterministic, no timestamp arithmetic)."""

    return p1 + (p2 - p1) * (x - x1) / (x2 - x1)


def _rel_slope(x1: int, p1: float, x2: int, p2: float) -> float:
    """Price-relative slope per bar of the line through the two anchors."""

    return (p2 - p1) / (x2 - x1) / ((p1 + p2) / 2.0)


@dataclass(frozen=True)
class _Formation:
    """One recognised formation, pre-lifecycle.

    `completion_bar` is the bar at which the last defining pivot confirms (the
    `forming` hit's bar_index). The breakout line is `(break_x1, break_p1)`-
    `(break_x2, break_p2)`; `break_direction` is which way a confirming close
    must clear it (`+1` above, `-1` below; `0` for a symmetrical triangle,
    which confirms on either bounding line). `invalidate_level`/`_direction`
    optionally name a close level beyond which the formation is dead (e.g. a
    close above the head of a head & shoulders).
    """

    pattern: str
    direction: Direction
    completion_bar: int
    pivots: tuple[Pivot, ...]
    lines: tuple[LineSeg, ...]
    strength: float
    # Breakout line in bar-index space.
    break_x1: int
    break_p1: float
    break_x2: int
    break_p2: float
    break_direction: int  # +1 close above, -1 close below, 0 either bounding line
    # Measured-move height (added/subtracted from the broken line at the hit bar).
    measured_height: float
    # Optional hard invalidation: a close beyond this level kills the formation.
    invalidate_level: float | None = None
    invalidate_direction: int = 0  # +1 close above invalidates, -1 close below
    # Symmetrical triangle only: the second bounding line.
    alt_x1: int = 0
    alt_p1: float = 0.0
    alt_x2: int = 0
    alt_p2: float = 0.0


def _width_ok(first: Pivot, last: Pivot) -> bool:
    width = last.bar_index - first.bar_index
    return PATTERN_MIN_WIDTH_BARS <= width <= PATTERN_MAX_WIDTH_BARS


# --------------------------------------------------------------------------- #
# Pivot-matched family                                                          #
# --------------------------------------------------------------------------- #


def _match_head_shoulders(run: Sequence[Pivot], inverse: bool) -> _Formation | None:
    """A consecutive pivot run [extreme, counter, extreme, counter, extreme]:
    shoulders within the symmetry tolerance, head beyond both, neckline through
    the two intervening counter-pivots (within the flatness tolerance)."""

    ls, t1, head, t2, rs = run
    sign = -1.0 if inverse else 1.0  # inverse mirrors every comparison
    if not _width_ok(ls, rs):
        return None
    # Head strictly beyond both shoulders.
    if not (sign * head.price > sign * ls.price and sign * head.price > sign * rs.price):
        return None
    symmetry = abs(ls.price - rs.price) / abs(head.price)
    if symmetry > HS_SHOULDER_SYMMETRY_TOL:
        return None
    neckline_mean = (t1.price + t2.price) / 2.0
    if abs(t1.price - t2.price) / abs(neckline_mean) > NECKLINE_FLATNESS_TOL:
        return None

    head_height = abs(head.price - _line_value(
        t1.bar_index, t1.price, t2.bar_index, t2.price, head.bar_index
    ))
    return _Formation(
        pattern="inverse_head_shoulders" if inverse else "head_shoulders",
        direction="bullish" if inverse else "bearish",
        completion_bar=rs.bar_index + PIVOT_RIGHT,
        pivots=(ls, t1, head, t2, rs),
        lines=(LineSeg(start=_point(t1), end=_point(t2), role="neckline"),),
        strength=1.0 - symmetry / HS_SHOULDER_SYMMETRY_TOL,
        break_x1=t1.bar_index,
        break_p1=t1.price,
        break_x2=t2.bar_index,
        break_p2=t2.price,
        break_direction=1 if inverse else -1,
        measured_height=head_height,
        invalidate_level=head.price,
        invalidate_direction=-1 if inverse else 1,
    )


def _match_double(run: Sequence[Pivot], bottom: bool) -> _Formation | None:
    """A consecutive pivot run [extreme, counter, extreme]: the two extremes
    within the match tolerance, the middle pullback deep enough, the neckline
    horizontal at the pullback pivot's price."""

    e1, mid, e2 = run
    sign = -1.0 if bottom else 1.0
    if not _width_ok(e1, e2):
        return None
    extreme_mean = (e1.price + e2.price) / 2.0
    mismatch = abs(e1.price - e2.price) / abs(extreme_mean)
    if mismatch > DOUBLE_MATCH_TOL:
        return None
    # Pullback depth: the middle pivot must sit beyond the nearer extreme by
    # the minimum retrace fraction.
    nearer = min(e1.price, e2.price) if not bottom else max(e1.price, e2.price)
    pullback = sign * (nearer - mid.price) / abs(nearer)
    if pullback < DOUBLE_PULLBACK_MIN_PCT:
        return None

    height = abs(extreme_mean - mid.price)
    neck_start = PivotPoint(ts=e1.ts, price=mid.price)
    neck_end = PivotPoint(ts=e2.ts, price=mid.price)
    return _Formation(
        pattern="double_bottom" if bottom else "double_top",
        direction="bullish" if bottom else "bearish",
        completion_bar=e2.bar_index + PIVOT_RIGHT,
        pivots=(e1, mid, e2),
        lines=(LineSeg(start=neck_start, end=neck_end, role="neckline"),),
        strength=1.0 - mismatch / DOUBLE_MATCH_TOL,
        break_x1=e1.bar_index,
        break_p1=mid.price,
        break_x2=e2.bar_index,
        break_p2=mid.price,
        break_direction=1 if bottom else -1,
        measured_height=height,
        invalidate_level=min(e1.price, e2.price) if bottom else max(e1.price, e2.price),
        invalidate_direction=-1 if bottom else 1,
    )


def _pivot_matched_formations(pivots: Sequence[Pivot]) -> list[_Formation]:
    formations: list[_Formation] = []
    for i in range(len(pivots) - 2):
        triple = pivots[i : i + 3]
        kinds = tuple(p.kind for p in triple)
        if kinds == ("high", "low", "high"):
            if (m := _match_double(triple, bottom=False)) is not None:
                formations.append(m)
        elif kinds == ("low", "high", "low") and (
            m := _match_double(triple, bottom=True)
        ) is not None:
            formations.append(m)
    for i in range(len(pivots) - 4):
        run = pivots[i : i + 5]
        kinds = tuple(p.kind for p in run)
        if kinds == ("high", "low", "high", "low", "high"):
            if (m := _match_head_shoulders(run, inverse=False)) is not None:
                formations.append(m)
        elif kinds == ("low", "high", "low", "high", "low") and (
            m := _match_head_shoulders(run, inverse=True)
        ) is not None:
            formations.append(m)
    return formations


# --------------------------------------------------------------------------- #
# Trendline-fit family (connect-the-extremes)                                   #
# --------------------------------------------------------------------------- #


def _classify_trendlines(
    upper_rel: float, lower_rel: float
) -> tuple[str, Direction, int] | None:
    """Classify the two bounding lines by slope: returns
    `(pattern, forming_direction, break_direction)` or None when the slope
    combination matches no pattern (incl. the flat/trending dead zone)."""

    def _flat(rel: float) -> bool:
        return abs(rel) <= FLAT_SLOPE_TOL_PER_BAR

    def _rising(rel: float) -> bool:
        return rel >= MIN_TREND_SLOPE_PER_BAR

    def _falling(rel: float) -> bool:
        return rel <= -MIN_TREND_SLOPE_PER_BAR

    converging = (lower_rel - upper_rel) >= TRENDLINE_CONVERGENCE_MIN
    if _flat(upper_rel) and _rising(lower_rel):
        return ("ascending_triangle", "bullish", 1)
    if _flat(lower_rel) and _falling(upper_rel):
        return ("descending_triangle", "bearish", -1)
    if _falling(upper_rel) and _rising(lower_rel):
        return ("symmetrical_triangle", "neutral", 0)
    if _rising(upper_rel) and _rising(lower_rel) and converging:
        return ("rising_wedge", "bearish", -1)
    if _falling(upper_rel) and _falling(lower_rel) and converging:
        return ("falling_wedge", "bullish", 1)
    return None


def _trendline_strength(pattern: str, upper_rel: float, lower_rel: float) -> float:
    """0..1 relative conviction: flat-line quality for the flat-side triangles,
    convergence rate (capped at the reference) for the converging shapes."""

    if pattern == "ascending_triangle":
        return 1.0 - abs(upper_rel) / FLAT_SLOPE_TOL_PER_BAR
    if pattern == "descending_triangle":
        return 1.0 - abs(lower_rel) / FLAT_SLOPE_TOL_PER_BAR
    return min(1.0, (lower_rel - upper_rel) / TRENDLINE_STRENGTH_CONVERGENCE_REF)


def _trendline_formation_at(
    pivots: Sequence[Pivot], eval_bar: int
) -> _Formation | None:
    """Connect-the-extremes at one evaluation bar: take the pivots confirmed by
    `eval_bar` inside the trailing window, anchor the upper line on the two
    highest highs and the lower line on the two lowest lows, classify."""

    window = [
        p
        for p in pivots
        if p.bar_index + PIVOT_RIGHT <= eval_bar
        and p.bar_index >= eval_bar - TRENDLINE_WINDOW_BARS
    ]
    highs = [p for p in window if p.kind == "high"]
    lows = [p for p in window if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    # Two highest highs / two lowest lows; price ties break by bar_index so the
    # anchor choice is deterministic.
    upper = sorted(sorted(highs, key=lambda p: (-p.price, p.bar_index))[:2],
                   key=lambda p: p.bar_index)
    lower = sorted(sorted(lows, key=lambda p: (p.price, p.bar_index))[:2],
                   key=lambda p: p.bar_index)
    if upper[0].bar_index == upper[1].bar_index or lower[0].bar_index == lower[1].bar_index:
        return None
    anchors = sorted([*upper, *lower], key=lambda p: (p.bar_index, p.kind))
    if not _width_ok(anchors[0], anchors[-1]):
        return None

    upper_rel = _rel_slope(upper[0].bar_index, upper[0].price, upper[1].bar_index, upper[1].price)
    lower_rel = _rel_slope(lower[0].bar_index, lower[0].price, lower[1].bar_index, lower[1].price)
    classified = _classify_trendlines(upper_rel, lower_rel)
    if classified is None:
        return None
    pattern, direction, break_direction = classified

    x0 = anchors[0].bar_index
    height = _line_value(
        upper[0].bar_index, upper[0].price, upper[1].bar_index, upper[1].price, x0
    ) - _line_value(lower[0].bar_index, lower[0].price, lower[1].bar_index, lower[1].price, x0)
    lines = (
        LineSeg(start=_point(upper[0]), end=_point(upper[1]), role="upper_trendline"),
        LineSeg(start=_point(lower[0]), end=_point(lower[1]), role="lower_trendline"),
    )
    # The breakout line is the one a confirming close must clear: the upper for
    # bullish shapes, the lower for bearish; symmetrical watches both.
    if break_direction >= 0:
        bx1, bp1, bx2, bp2 = upper[0].bar_index, upper[0].price, upper[1].bar_index, upper[1].price
        ax1, ap1, ax2, ap2 = lower[0].bar_index, lower[0].price, lower[1].bar_index, lower[1].price
    else:
        bx1, bp1, bx2, bp2 = lower[0].bar_index, lower[0].price, lower[1].bar_index, lower[1].price
        ax1, ap1, ax2, ap2 = upper[0].bar_index, upper[0].price, upper[1].bar_index, upper[1].price
    return _Formation(
        pattern=pattern,
        direction=direction,
        completion_bar=eval_bar,
        pivots=tuple(anchors),
        lines=lines,
        strength=_trendline_strength(pattern, upper_rel, lower_rel),
        break_x1=bx1,
        break_p1=bp1,
        break_x2=bx2,
        break_p2=bp2,
        break_direction=break_direction,
        measured_height=abs(height),
        # The opposite bounding line: a close through it by the same margin
        # invalidates a directional shape (and confirms a symmetrical one).
        alt_x1=ax1,
        alt_p1=ap1,
        alt_x2=ax2,
        alt_p2=ap2,
    )


def _trendline_formations(pivots: Sequence[Pivot]) -> list[_Formation]:
    """Evaluate connect-the-extremes at every pivot-confirmation bar; emit each
    distinct anchor set (the formation identity) once, at the first bar where
    it classifies."""

    formations: list[_Formation] = []
    seen: set[tuple[str, tuple[tuple[int, str], ...]]] = set()
    eval_bars = sorted({p.bar_index + PIVOT_RIGHT for p in pivots})
    for eval_bar in eval_bars:
        formation = _trendline_formation_at(pivots, eval_bar)
        if formation is None:
            continue
        identity = (
            formation.pattern,
            tuple((p.bar_index, p.kind) for p in formation.pivots),
        )
        if identity in seen:
            continue
        seen.add(identity)
        formations.append(formation)
    return formations


# --------------------------------------------------------------------------- #
# Lifecycle: forming -> confirmed (trailing, never reading a future bar)        #
# --------------------------------------------------------------------------- #


def _hit(formation: _Formation, state: PatternState, bar_index: int,
         direction: Direction, target: float | None) -> ChartPatternHit:
    return ChartPatternHit(
        pattern=formation.pattern,
        state=state,
        direction=direction,
        bar_index=bar_index,
        pivots=[_point(p) for p in formation.pivots],
        lines=list(formation.lines),
        target=target,
        strength=max(0.0, min(1.0, formation.strength)),
    )


def _target_at(formation: _Formation, bar: int, direction: Direction) -> float | None:
    """Measured-move projection from the breakout line at `bar` (geometry fact,
    never advice). A direction-less forming symmetrical triangle has none."""

    if direction == "neutral":
        return None
    if formation.break_direction == 0 and direction == "bearish":
        # Symmetrical triangle broken downward: project from the lower line.
        line = _line_value(
            formation.alt_x1, formation.alt_p1, formation.alt_x2, formation.alt_p2, bar
        )
    else:
        line = _line_value(
            formation.break_x1, formation.break_p1, formation.break_x2, formation.break_p2, bar
        )
    if direction == "bullish":
        return line + formation.measured_height
    return line - formation.measured_height


def _confirm_or_invalidate(
    formation: _Formation, bars: Sequence[Bar], atr_series: Sequence[float | None]
) -> tuple[int, Direction] | None:
    """Scan forward from the completion bar for the first close that breaks the
    breakout line by `BREAKOUT_ATR_MULT * ATR`, or stop on invalidation / scan
    horizon. Every comparison at bar `b` reads only `bars[0..=b]`."""

    n = len(bars)
    last = min(n - 1, formation.completion_bar + BREAKOUT_SCAN_MAX_BARS)
    for b in range(formation.completion_bar, last + 1):
        close = bars[b].close
        # Hard invalidation first: the formation is dead the bar this prints.
        if formation.invalidate_level is not None:
            if formation.invalidate_direction > 0 and close > formation.invalidate_level:
                return None
            if formation.invalidate_direction < 0 and close < formation.invalidate_level:
                return None
        atr_b = atr_series[b]
        if atr_b is None:
            continue  # margin undefined this early — cannot confirm yet
        margin = BREAKOUT_ATR_MULT * atr_b
        line = _line_value(
            formation.break_x1, formation.break_p1, formation.break_x2, formation.break_p2, b
        )
        if formation.break_direction > 0 and close > line + margin:
            return (b, "bullish")
        if formation.break_direction < 0 and close < line - margin:
            return (b, "bearish")
        if formation.break_direction == 0:
            # Symmetrical triangle: either bounding line confirms; check the
            # upper (bullish) side first, deterministically.
            if close > line + margin:
                return (b, "bullish")
            alt = _line_value(
                formation.alt_x1, formation.alt_p1, formation.alt_x2, formation.alt_p2, b
            )
            if close < alt - margin:
                return (b, "bearish")
        elif formation.pattern in _TRENDLINE_PATTERNS:
            # Directional trendline shape: a close through the opposite
            # bounding line by the same margin invalidates it.
            alt = _line_value(
                formation.alt_x1, formation.alt_p1, formation.alt_x2, formation.alt_p2, b
            )
            if formation.break_direction > 0 and close < alt - margin:
                return None
            if formation.break_direction < 0 and close > alt + margin:
                return None
    return None


def detect_chart_patterns(bars: Sequence[Bar]) -> list[ChartPatternHit]:
    """Detect classical chart patterns over `bars`, ordered by
    `(bar_index, pattern, state)`.

    Emits one `forming` hit per formation at its completion bar and one
    `confirmed` hit at its `k * ATR` breakout bar (when that break happens
    before invalidation / the scan horizon). Strictly trailing: a hit at bar
    `i` depends only on `bars[0..=i]`, so `detect_chart_patterns(bars[: i+1])`
    reproduces it byte-identically (the ADR-0048 anti-lookahead corollary).
    """

    if len(bars) <= PIVOT_LEFT + PIVOT_RIGHT:
        return []
    pivots = swing_pivots(bars, left=PIVOT_LEFT, right=PIVOT_RIGHT)
    if not pivots:
        return []
    atr_series = atr(bars, ATR_PERIOD)

    formations = _pivot_matched_formations(pivots) + _trendline_formations(pivots)
    hits: list[ChartPatternHit] = []
    for formation in formations:
        forming_target = _target_at(formation, formation.completion_bar, formation.direction)
        hits.append(
            _hit(formation, "forming", formation.completion_bar,
                 formation.direction, forming_target)
        )
        confirmed = _confirm_or_invalidate(formation, bars, atr_series)
        if confirmed is not None:
            confirm_bar, confirm_direction = confirmed
            hits.append(
                _hit(formation, "confirmed", confirm_bar, confirm_direction,
                     _target_at(formation, confirm_bar, confirm_direction))
            )
    hits.sort(key=lambda h: (h.bar_index, h.pattern, _STATE_ORDER[h.state]))
    return hits


__all__ = [
    "ATR_PERIOD",
    "BREAKOUT_ATR_MULT",
    "BREAKOUT_SCAN_MAX_BARS",
    "CHART_PATTERNS",
    "DOUBLE_MATCH_TOL",
    "DOUBLE_PULLBACK_MIN_PCT",
    "FLAT_SLOPE_TOL_PER_BAR",
    "HS_SHOULDER_SYMMETRY_TOL",
    "MIN_TREND_SLOPE_PER_BAR",
    "NECKLINE_FLATNESS_TOL",
    "PATTERN_MAX_WIDTH_BARS",
    "PATTERN_MIN_WIDTH_BARS",
    "PIVOT_LEFT",
    "PIVOT_RIGHT",
    "TRENDLINE_CONVERGENCE_MIN",
    "TRENDLINE_STRENGTH_CONVERGENCE_REF",
    "TRENDLINE_WINDOW_BARS",
    "detect_chart_patterns",
]
