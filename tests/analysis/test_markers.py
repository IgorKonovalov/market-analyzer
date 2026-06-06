"""Plan 0049 phase 3: the pure detect→map core in `analysis/markers.py`.

`patterns_to_markers` maps `PatternHit`s + bars to span-bearing `chart.highlight`
markers; `markers_for_range` composes detect + filter + map. Both are pure — no
event bus, no persistence — so they are unit-tested here in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_analyser.analysis.markers import markers_for_range, patterns_to_markers
from market_analyser.analysis.patterns import detect_patterns
from market_analyser.analysis.types import PatternHit
from market_analyser.data.types import Bar


def _bar(day: int, *, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=datetime(2026, 5, day, tzinfo=UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1_000.0,
        source="test",
    )


def _doji_and_hammer_bars() -> list[Bar]:
    """A declining series whose last bar (index 4) is BOTH a doji (tiny body) and a
    hammer (long lower shadow, prior downtrend) — two distinct patterns completing
    on the same bar with different directions (neutral / bullish)."""
    return [
        _bar(11, o=111.0, h=112.0, low=109.5, c=110.0),
        _bar(12, o=110.0, h=111.0, low=108.5, c=109.0),
        _bar(13, o=109.0, h=110.0, low=106.5, c=107.0),
        _bar(14, o=107.0, h=108.0, low=104.5, c=105.0),
        _bar(15, o=108.4, h=108.8, low=100.0, c=108.0),
    ]


def test_patterns_to_markers_multibar_carries_resolved_span() -> None:
    bars = [_bar(d, o=100.0, h=101.0, low=99.0, c=100.5) for d in range(1, 5)]
    hit = PatternHit(
        bar_index=3, pattern="morning_star", direction="bullish", strength=0.7, span_bars=3
    )
    (marker,) = patterns_to_markers([hit], bars)
    assert marker.pattern == "morning_star"
    assert marker.kind == "bullish_marker"
    assert marker.strength == 0.7
    assert marker.event_ts == bars[3].event_ts
    assert marker.span_start_ts == bars[1].event_ts
    assert marker.span_end_ts == bars[3].event_ts


def test_patterns_to_markers_single_bar_has_no_span_and_neutral_maps() -> None:
    bars = [_bar(d, o=100.0, h=101.0, low=99.0, c=100.5) for d in range(1, 5)]
    hit = PatternHit(bar_index=2, pattern="doji", direction="neutral", strength=0.9, span_bars=1)
    (marker,) = patterns_to_markers([hit], bars)
    assert marker.kind == "neutral_marker"
    assert marker.pattern == "doji"
    assert marker.span_start_ts is None
    assert marker.span_end_ts is None
    assert marker.event_ts == bars[2].event_ts


def test_markers_for_range_emits_one_marker_per_detected_pattern() -> None:
    bars = _doji_and_hammer_bars()
    hits = detect_patterns(bars)
    markers = markers_for_range(bars)
    assert len(markers) == len(hits)
    # The same-bar doji (neutral) and hammer (bullish) both survive — distinct
    # pattern + kind on one bar, no collapse at the mapper.
    last_ts = bars[-1].event_ts
    same_bar = {(m.pattern, m.kind) for m in markers if m.event_ts == last_ts}
    assert ("doji", "neutral_marker") in same_bar
    assert ("hammer", "bullish_marker") in same_bar


def test_markers_for_range_patterns_filter() -> None:
    bars = _doji_and_hammer_bars()
    markers = markers_for_range(bars, patterns=["doji"])
    assert {m.pattern for m in markers} == {"doji"}


def test_markers_for_range_min_strength_filter() -> None:
    bars = _doji_and_hammer_bars()
    hits = {h.pattern: h.strength for h in detect_patterns(bars)}
    # The doji scores higher than the hammer on this bar; a threshold between them
    # keeps only the doji.
    threshold = (hits["doji"] + hits["hammer"]) / 2
    markers = markers_for_range(bars, min_strength=threshold)
    assert {m.pattern for m in markers} == {"doji"}
    assert all(m.strength is not None and m.strength >= threshold for m in markers)
