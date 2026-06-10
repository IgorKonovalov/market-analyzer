"""Plan 0052 phase 1 done-when: `analysis/chart_patterns.py`.

- H&S fixture geometry: the detector reports `head_shoulders` with three peak
  pivots whose middle (head) exceeds both shoulders within the symmetry
  tolerance, and a neckline through the two intervening troughs.
- **No lookahead** (the cardinal-sin guard, pinned per pattern): a hit
  reported at bar `i` — in either state — is byte-identical when the series is
  truncated to `bars[0..=i]`; stronger, the hits on `bars[0..=k]` are exactly
  the full-series hits with `bar_index <= k`, for every `k`.
- State transition: the H&S fixture reports `forming` at the bar where the
  geometry first completes (last pivot's confirmation bar) and `confirmed`
  only at the bar whose close breaks the neckline by `BREAKOUT_ATR_MULT * ATR`
  — computed here from the module's own constants, internal-consistency style.
  A fixture that never breaks the neckline never reaches `confirmed`.
- A symmetrical-triangle fixture reports converging upper/lower trendlines
  (opposite-sign slopes within tolerance) connecting the two highest highs /
  two lowest lows; an ascending-triangle fixture reports a flat upper line +
  rising lower line.
- Out-of-tolerance formations (asymmetric shoulders, mismatched tops, width
  outside min/max) do NOT fire.

Fixtures are piecewise-linear base paths sampled into bars (high = base + 1,
low = base - 1, open = close = base), so the only swing pivots are the anchor
extremes — every expected pivot price/index is computable by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from market_analyser.analysis.chart_patterns import (
    ATR_PERIOD,
    BREAKOUT_ATR_MULT,
    PIVOT_RIGHT,
    detect_chart_patterns,
)
from market_analyser.analysis.indicators import atr
from market_analyser.analysis.types import ChartPatternHit
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bars_from_path(anchors: list[tuple[int, float]]) -> list[Bar]:
    """Sample a piecewise-linear base path into bars: high/low straddle the
    base by 1.0, open/close sit on it. Between anchors the path is monotone,
    so the swing pivots are exactly the interior anchor bars."""

    n = anchors[-1][0] + 1
    bases: list[float] = []
    for i in range(n):
        for (x1, p1), (x2, p2) in pairwise(anchors):
            if x1 <= i <= x2:
                bases.append(p1 + (p2 - p1) * (i - x1) / (x2 - x1))
                break
    bars = [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
            open=base,
            high=base + 1.0,
            low=base - 1.0,
            close=base,
            volume=1000.0,
            source="synthetic",
        )
        for i, base in enumerate(bases)
    ]
    return bars


# --------------------------------------------------------------------------- #
# Fixtures — one per pattern (the per-pattern no-lookahead pin needs them all)  #
# --------------------------------------------------------------------------- #

# Head & shoulders: peaks 111 @6 / 121 @14 / 111.5 @22 (highs = base + 1),
# troughs 99 @10 / 100 @18; then a decline through the neckline.
_HS_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 78.0),
]
# Same formation, but the tail drifts sideways above the neckline forever.
_HS_NO_BREAK_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 104.0),
]
_INVERSE_HS_ANCHORS = [
    (0, 120.0),
    (6, 110.0),
    (10, 120.0),
    (14, 100.0),
    (18, 119.0),
    (22, 109.5),
    (35, 142.0),
]
_DOUBLE_TOP_ANCHORS = [(0, 100.0), (6, 120.0), (12, 105.0), (18, 119.5), (33, 85.0)]
_DOUBLE_BOTTOM_ANCHORS = [(0, 120.0), (6, 100.0), (12, 115.0), (18, 100.5), (33, 135.0)]
# Symmetrical triangle: falling highs 121 @6 / 117 @14, rising lows 99 @10 /
# 103 @18, then an upside breakout.
_SYM_TRIANGLE_ANCHORS = [
    (0, 110.0),
    (6, 120.0),
    (10, 100.0),
    (14, 116.0),
    (18, 104.0),
    (24, 110.0),
    (29, 126.0),
]
# Ascending triangle: flat top 111 @6 / 111.04 @14, rising lows 99 @10 / 103 @18.
_ASC_TRIANGLE_ANCHORS = [
    (0, 104.0),
    (6, 110.0),
    (10, 100.0),
    (14, 110.04),
    (18, 104.0),
    (24, 109.0),
    (29, 124.0),
]
_DESC_TRIANGLE_ANCHORS = [
    (0, 116.0),
    (6, 110.0),
    (10, 120.0),
    (14, 109.96),
    (18, 116.0),
    (24, 111.0),
    (29, 96.0),
]
# Rising wedge: rising highs 111 @6 / 114 @14, faster-rising lows 103 @10 /
# 110 @18 (converging), then the bearish break.
_RISING_WEDGE_ANCHORS = [
    (0, 106.0),
    (6, 110.0),
    (10, 104.0),
    (14, 113.0),
    (18, 111.0),
    (21, 111.6),
    (29, 95.6),
]
_FALLING_WEDGE_ANCHORS = [
    (0, 114.0),
    (6, 110.0),
    (10, 116.0),
    (14, 107.0),
    (18, 109.0),
    (21, 108.4),
    (29, 124.4),
]

_ALL_FIXTURES: dict[str, list[tuple[int, float]]] = {
    "head_shoulders": _HS_ANCHORS,
    "inverse_head_shoulders": _INVERSE_HS_ANCHORS,
    "double_top": _DOUBLE_TOP_ANCHORS,
    "double_bottom": _DOUBLE_BOTTOM_ANCHORS,
    "symmetrical_triangle": _SYM_TRIANGLE_ANCHORS,
    "ascending_triangle": _ASC_TRIANGLE_ANCHORS,
    "descending_triangle": _DESC_TRIANGLE_ANCHORS,
    "rising_wedge": _RISING_WEDGE_ANCHORS,
    "falling_wedge": _FALLING_WEDGE_ANCHORS,
}


def _hits_for(bars: list[Bar], pattern: str) -> list[ChartPatternHit]:
    return [h for h in detect_chart_patterns(bars) if h.pattern == pattern]


# --------------------------------------------------------------------------- #
# H&S fixture geometry                                                          #
# --------------------------------------------------------------------------- #


def test_head_shoulders_geometry_on_constructed_fixture() -> None:
    bars = _bars_from_path(_HS_ANCHORS)
    hits = _hits_for(bars, "head_shoulders")
    assert hits, "the constructed H&S fixture must fire"
    forming = next(h for h in hits if h.state == "forming")

    assert forming.direction == "bearish"
    # Five ordered pivots: shoulder, trough, head, trough, shoulder.
    assert [p.price for p in forming.pivots] == [111.0, 99.0, 121.0, 100.0, 111.5]
    shoulders = (forming.pivots[0].price, forming.pivots[4].price)
    head = forming.pivots[2].price
    assert head > max(shoulders)  # the head exceeds both shoulders
    assert abs(shoulders[0] - shoulders[1]) / head <= 0.05  # within symmetry tol
    # The neckline runs through the two intervening troughs.
    assert len(forming.lines) == 1
    neckline = forming.lines[0]
    assert neckline.role == "neckline"
    assert neckline.start.ts == bars[10].event_ts
    assert abs(neckline.start.price - 99.0) < _TOL
    assert neckline.end.ts == bars[18].event_ts
    assert abs(neckline.end.price - 100.0) < _TOL
    assert 0.0 < forming.strength <= 1.0
    # Measured-move target: neckline at the hit bar minus the head height.
    neck_at_head = 99.0 + (100.0 - 99.0) * (14 - 10) / (18 - 10)
    neck_at_hit = 99.0 + (100.0 - 99.0) * (forming.bar_index - 10) / (18 - 10)
    assert forming.target is not None
    assert abs(forming.target - (neck_at_hit - (121.0 - neck_at_head))) < _TOL


def test_inverse_head_shoulders_mirrors_bullish() -> None:
    bars = _bars_from_path(_INVERSE_HS_ANCHORS)
    hits = _hits_for(bars, "inverse_head_shoulders")
    assert hits
    forming = next(h for h in hits if h.state == "forming")
    assert forming.direction == "bullish"
    head = forming.pivots[2].price
    assert head < min(forming.pivots[0].price, forming.pivots[4].price)
    assert forming.lines[0].role == "neckline"


def test_double_top_and_bottom_fire_with_horizontal_neckline() -> None:
    top_hits = _hits_for(_bars_from_path(_DOUBLE_TOP_ANCHORS), "double_top")
    assert top_hits
    top = next(h for h in top_hits if h.state == "forming")
    assert top.direction == "bearish"
    assert [p.price for p in top.pivots] == [121.0, 104.0, 120.5]
    neck = top.lines[0]
    assert neck.role == "neckline"
    assert abs(neck.start.price - 104.0) < _TOL  # horizontal at the trough
    assert abs(neck.end.price - 104.0) < _TOL

    bottom_hits = _hits_for(_bars_from_path(_DOUBLE_BOTTOM_ANCHORS), "double_bottom")
    assert bottom_hits
    bottom = next(h for h in bottom_hits if h.state == "forming")
    assert bottom.direction == "bullish"
    assert [p.price for p in bottom.pivots] == [99.0, 116.0, 99.5]
    assert abs(bottom.lines[0].start.price - 116.0) < _TOL


# --------------------------------------------------------------------------- #
# No lookahead — truncation invariance, pinned per pattern                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pattern", sorted(_ALL_FIXTURES))
def test_no_lookahead_truncation_invariance_per_pattern(pattern: str) -> None:
    """The cardinal-sin guard: a hit reported at bar `i` (either state) is
    byte-identical on `bars[0..=i]`; stronger, the hits on every truncation
    `bars[0..=k]` are EXACTLY the full-series hits with `bar_index <= k`."""

    bars = _bars_from_path(_ALL_FIXTURES[pattern])
    full = detect_chart_patterns(bars)
    assert any(h.pattern == pattern for h in full), f"{pattern} fixture must fire"

    for hit in full:
        truncated = detect_chart_patterns(bars[: hit.bar_index + 1])
        assert hit in truncated  # field-equal
        match = next(h for h in truncated if h == hit)
        assert match.model_dump_json() == hit.model_dump_json()  # byte-identical

    for k in range(len(bars)):
        truncated = detect_chart_patterns(bars[: k + 1])
        expected = [h for h in full if h.bar_index <= k]
        assert truncated == expected, f"{pattern}: hit set diverged at truncation k={k}"


# --------------------------------------------------------------------------- #
# State transition: forming at completion, confirmed only at the k*ATR break    #
# --------------------------------------------------------------------------- #


def _neckline_value(b: int) -> float:
    """The H&S fixture's neckline (through (10, 99.0) and (18, 100.0)) at bar b."""

    return 99.0 + (100.0 - 99.0) * (b - 10) / (18 - 10)


def test_hs_forming_at_geometry_completion_bar() -> None:
    """The right shoulder prints at bar 22 and (with PIVOT_RIGHT bars of
    context) confirms at 22 + PIVOT_RIGHT — the first bar at which the full
    geometry is knowable, and exactly where the forming hit sits."""

    bars = _bars_from_path(_HS_ANCHORS)
    forming = [h for h in _hits_for(bars, "head_shoulders") if h.state == "forming"]
    assert len(forming) == 1
    completion = 22 + PIVOT_RIGHT
    assert forming[0].bar_index == completion
    # Not knowable one bar earlier.
    assert _hits_for(bars[:completion], "head_shoulders") == []


def test_hs_confirmed_only_at_k_atr_neckline_break() -> None:
    """The confirmed hit lands exactly at the first bar whose close breaks the
    neckline by BREAKOUT_ATR_MULT * ATR — computed here from the module's own
    constants — and is absent on the series truncated one bar before."""

    bars = _bars_from_path(_HS_ANCHORS)
    atr_series = atr(bars, ATR_PERIOD)
    completion = 22 + PIVOT_RIGHT
    expected_break = next(
        b
        for b in range(completion, len(bars))
        if atr_series[b] is not None
        and bars[b].close < _neckline_value(b) - BREAKOUT_ATR_MULT * atr_series[b]  # type: ignore[operator]
    )
    assert expected_break > completion  # the fixture separates the two states

    confirmed = [h for h in _hits_for(bars, "head_shoulders") if h.state == "confirmed"]
    assert len(confirmed) == 1
    hit = confirmed[0]
    assert hit.bar_index == expected_break
    assert hit.direction == "bearish"
    # The break is not a fact one bar earlier.
    earlier = _hits_for(bars[:expected_break], "head_shoulders")
    assert all(h.state != "confirmed" for h in earlier)
    assert any(h.state == "forming" for h in earlier)


def test_hs_never_breaking_fixture_never_confirms() -> None:
    """Closes drift sideways above the neckline forever: the formation stays
    forming on every truncation and never reaches confirmed."""

    bars = _bars_from_path(_HS_NO_BREAK_ANCHORS)
    hits = _hits_for(bars, "head_shoulders")
    assert any(h.state == "forming" for h in hits)
    assert all(h.state != "confirmed" for h in hits)
    for k in range(len(bars)):
        assert all(h.state != "confirmed" for h in _hits_for(bars[: k + 1], "head_shoulders"))


# --------------------------------------------------------------------------- #
# Trendline-fit family                                                          #
# --------------------------------------------------------------------------- #


def _line_slope(seg_start_price: float, seg_end_price: float, x1: int, x2: int) -> float:
    return (seg_end_price - seg_start_price) / (x2 - x1)


def test_symmetrical_triangle_reports_converging_extreme_lines() -> None:
    """Upper line through the two highest highs (121 @6, 117 @14), lower line
    through the two lowest lows (99 @10, 103 @18): opposite-sign slopes."""

    bars = _bars_from_path(_SYM_TRIANGLE_ANCHORS)
    hits = _hits_for(bars, "symmetrical_triangle")
    assert hits
    forming = next(h for h in hits if h.state == "forming")
    assert forming.direction == "neutral"
    assert forming.target is None  # no direction yet -> no measured move

    upper = next(line for line in forming.lines if line.role == "upper_trendline")
    lower = next(line for line in forming.lines if line.role == "lower_trendline")
    assert (upper.start.ts, upper.start.price) == (bars[6].event_ts, 121.0)
    assert (upper.end.ts, upper.end.price) == (bars[14].event_ts, 117.0)
    assert (lower.start.ts, lower.start.price) == (bars[10].event_ts, 99.0)
    assert (lower.end.ts, lower.end.price) == (bars[18].event_ts, 103.0)
    upper_slope = _line_slope(upper.start.price, upper.end.price, 6, 14)
    lower_slope = _line_slope(lower.start.price, lower.end.price, 10, 18)
    assert upper_slope < 0 < lower_slope  # converging

    # The upside break confirms it bullish, with a measured-move target.
    confirmed = next(h for h in hits if h.state == "confirmed")
    assert confirmed.direction == "bullish"
    assert confirmed.bar_index > forming.bar_index
    assert confirmed.target is not None and confirmed.target > bars[confirmed.bar_index].close


def test_ascending_triangle_flat_upper_rising_lower() -> None:
    bars = _bars_from_path(_ASC_TRIANGLE_ANCHORS)
    hits = _hits_for(bars, "ascending_triangle")
    assert hits
    forming = next(h for h in hits if h.state == "forming")
    assert forming.direction == "bullish"

    upper = next(line for line in forming.lines if line.role == "upper_trendline")
    lower = next(line for line in forming.lines if line.role == "lower_trendline")
    # Flat upper: 111.0 @6 vs 111.04 @14 — within the flatness tolerance.
    upper_rel = _line_slope(upper.start.price, upper.end.price, 6, 14) / (
        (upper.start.price + upper.end.price) / 2.0
    )
    assert abs(upper_rel) <= 0.0005
    # Rising lower: 99 @10 -> 103 @18.
    assert lower.end.price > lower.start.price
    assert 0.0 < forming.strength <= 1.0


def test_descending_triangle_and_wedges_classify() -> None:
    desc = _hits_for(_bars_from_path(_DESC_TRIANGLE_ANCHORS), "descending_triangle")
    assert desc and desc[0].direction == "bearish"

    rising = _hits_for(_bars_from_path(_RISING_WEDGE_ANCHORS), "rising_wedge")
    assert rising
    forming = next(h for h in rising if h.state == "forming")
    assert forming.direction == "bearish"
    upper = next(line for line in forming.lines if line.role == "upper_trendline")
    lower = next(line for line in forming.lines if line.role == "lower_trendline")
    assert upper.end.price > upper.start.price  # both lines rising...
    assert lower.end.price > lower.start.price
    # ...and converging: the lower rises faster (per-bar slopes).
    assert _line_slope(lower.start.price, lower.end.price, 10, 18) > _line_slope(
        upper.start.price, upper.end.price, 6, 14
    )

    falling = _hits_for(_bars_from_path(_FALLING_WEDGE_ANCHORS), "falling_wedge")
    assert falling
    assert next(h for h in falling if h.state == "forming").direction == "bullish"


# --------------------------------------------------------------------------- #
# Out-of-tolerance formations do NOT fire                                       #
# --------------------------------------------------------------------------- #


def test_asymmetric_shoulders_do_not_fire() -> None:
    """Shoulders 111 vs 104 against a 121 head: 5.8% asymmetry, beyond the 5%
    tolerance — no head_shoulders hit in any state."""

    anchors = [
        (0, 100.0),
        (6, 110.0),
        (10, 100.0),
        (14, 120.0),
        (18, 101.0),
        (22, 103.0),
        (29, 92.0),
    ]
    assert _hits_for(_bars_from_path(anchors), "head_shoulders") == []


def test_mismatched_tops_do_not_fire_double_top() -> None:
    """Tops 121 vs 115 (5.1% apart, beyond the 2% match tolerance)."""

    anchors = [(0, 100.0), (6, 120.0), (12, 105.0), (18, 114.0), (29, 95.0)]
    assert _hits_for(_bars_from_path(anchors), "double_top") == []


def test_too_narrow_formation_does_not_fire() -> None:
    """Tops 8 bars apart — below PATTERN_MIN_WIDTH_BARS."""

    anchors = [(0, 100.0), (6, 120.0), (10, 105.0), (14, 119.5), (25, 95.0)]
    assert _hits_for(_bars_from_path(anchors), "double_top") == []


def test_too_wide_formation_does_not_fire() -> None:
    """Tops 130 bars apart — beyond PATTERN_MAX_WIDTH_BARS."""

    anchors = [(0, 100.0), (6, 120.0), (70, 105.0), (136, 119.5), (144, 112.0)]
    assert _hits_for(_bars_from_path(anchors), "double_top") == []


def test_flat_series_yields_no_hits() -> None:
    flat = [(0, 100.0), (40, 100.0)]
    assert detect_chart_patterns(_bars_from_path(flat)) == []


def test_tiny_series_yields_no_hits() -> None:
    assert detect_chart_patterns([]) == []
    assert detect_chart_patterns(_bars_from_path([(0, 100.0), (5, 105.0)])) == []


# --------------------------------------------------------------------------- #
# Determinism                                                                   #
# --------------------------------------------------------------------------- #


def test_detection_deterministic_across_repeat_calls() -> None:
    bars = _bars_from_path(_HS_ANCHORS)
    first = detect_chart_patterns(bars)
    second = detect_chart_patterns(bars)
    assert first == second
    assert [h.model_dump_json() for h in first] == [h.model_dump_json() for h in second]
