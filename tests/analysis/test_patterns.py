"""Phase-2 done-when for Plan 0018: candlestick detectors in `analysis/patterns.py`.

* **Per-pattern positive** — each `patterns_<name>.json` fixture is a hand-built
  sequence known to contain exactly the target pattern at a known index; the
  detector must emit a `PatternHit` there with the expected direction.
* **Negative** — a flat series yields no hits for the multi-bar patterns (guards
  against over-eager detectors).
* **Anti-lookahead** — a pattern reported at bar `i` is still reported when the
  series is truncated to `bars[0..=i]`; no pattern requires `bars[i+1..]`.
* **Determinism** — `detect_patterns` returns an identically ordered list across
  two calls, sorted by `(bar_index, pattern)`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from market_analyser.analysis import PatternHit, detect_patterns, resolve_span
from market_analyser.data.types import Bar

_FIXTURES = Path(__file__).parent / "fixtures"

# (fixture name, expected bar_index, expected direction)
_POSITIVE = [
    ("doji", 2, "neutral"),
    ("hammer", 4, "bullish"),
    ("hanging_man", 4, "bearish"),
    ("marubozu", 2, "bullish"),
    ("bullish_engulfing", 2, "bullish"),
    ("bearish_engulfing", 2, "bearish"),
    ("dark_cloud_cover", 2, "bearish"),
    ("piercing_line", 2, "bullish"),
    ("bullish_harami", 2, "bullish"),
    ("bearish_harami", 2, "bearish"),
    ("morning_star", 2, "bullish"),
    ("evening_star", 2, "bearish"),
    ("three_white_soldiers", 2, "bullish"),
    ("three_black_crows", 2, "bearish"),
]

_MULTI_BAR = {
    "bullish_engulfing",
    "bearish_engulfing",
    "dark_cloud_cover",
    "piercing_line",
    "bullish_harami",
    "bearish_harami",
    "morning_star",
    "evening_star",
    "three_white_soldiers",
    "three_black_crows",
}

# The statically-known bar span per pattern (Plan 0049 phase 1): 1 for the
# single-bar patterns, 2 for the six two-bar patterns, 3 for the four three-bar
# patterns. The detector must report exactly this on its `PatternHit`.
_EXPECTED_SPAN = {
    "doji": 1,
    "hammer": 1,
    "hanging_man": 1,
    "marubozu": 1,
    "bullish_engulfing": 2,
    "bearish_engulfing": 2,
    "dark_cloud_cover": 2,
    "piercing_line": 2,
    "bullish_harami": 2,
    "bearish_harami": 2,
    "morning_star": 3,
    "evening_star": 3,
    "three_white_soldiers": 3,
    "three_black_crows": 3,
}


def _load(name: str) -> list[Bar]:
    rows = json.loads((_FIXTURES / f"patterns_{name}.json").read_text(encoding="utf-8"))
    return [Bar.model_validate(row) for row in rows]


@pytest.mark.parametrize(("name", "index", "direction"), _POSITIVE)
def test_pattern_positive_case(name: str, index: int, direction: str) -> None:
    bars = _load(name)
    hits = detect_patterns(bars)
    matched = [h for h in hits if h.pattern == name and h.bar_index == index]
    assert matched, (
        f"{name} not detected at index {index}; got {[(h.bar_index, h.pattern) for h in hits]}"
    )
    hit = matched[0]
    assert hit.direction == direction
    assert 0.0 <= hit.strength <= 1.0


def test_negative_flat_series_has_no_multibar_hits() -> None:
    bars = _load("flat")
    hits = detect_patterns(bars)
    assert [h.pattern for h in hits if h.pattern in _MULTI_BAR] == []


@pytest.mark.parametrize(("name", "index", "direction"), _POSITIVE)
def test_anti_lookahead_truncation_invariance(name: str, index: int, direction: str) -> None:
    """Truncating the series to bars[0..=index] reproduces every hit at or before
    `index` — a detected pattern never depends on future bars."""

    bars = _load(name)
    full = [h for h in detect_patterns(bars) if h.bar_index <= index]
    truncated = detect_patterns(bars[: index + 1])
    assert full == truncated
    assert any(h.pattern == name and h.bar_index == index for h in truncated)


@pytest.mark.parametrize(("name", "index", "direction"), _POSITIVE)
def test_determinism(name: str, index: int, direction: str) -> None:
    bars = _load(name)
    first = detect_patterns(bars)
    second = detect_patterns(bars)
    assert first == second
    assert first == sorted(first, key=lambda h: (h.bar_index, h.pattern))


def test_detect_patterns_returns_pattern_hits() -> None:
    bars: Sequence[Bar] = _load("doji")
    hits = detect_patterns(bars)
    assert all(isinstance(h, PatternHit) for h in hits)


@pytest.mark.parametrize(("name", "index", "direction"), _POSITIVE)
def test_pattern_reports_static_span(name: str, index: int, direction: str) -> None:
    """Each detector reports its statically-known span: single-bar patterns span 1,
    the six two-bar patterns span 2, the four three-bar patterns span 3."""

    bars = _load(name)
    hit = next(h for h in detect_patterns(bars) if h.pattern == name and h.bar_index == index)
    assert hit.span_bars == _EXPECTED_SPAN[name]


def test_resolve_span_three_bar_pattern() -> None:
    """A 3-bar morning_star resolves to (start_ts, end_ts) where start_ts is the
    timestamp of bar_index - 2 and end_ts is the completing bar's — derived only
    from trailing bars."""

    bars = _load("morning_star")
    hit = next(h for h in detect_patterns(bars) if h.pattern == "morning_star")
    assert hit.span_bars == 3
    start_ts, end_ts = resolve_span(hit, bars)
    assert start_ts == bars[hit.bar_index - 2].event_ts
    assert end_ts == bars[hit.bar_index].event_ts
    assert start_ts < end_ts


def test_resolve_span_single_bar_pattern_is_point() -> None:
    """A single-bar doji resolves to a zero-width span: start_ts == end_ts =="""

    bars = _load("doji")
    hit = next(h for h in detect_patterns(bars) if h.pattern == "doji")
    assert hit.span_bars == 1
    start_ts, end_ts = resolve_span(hit, bars)
    assert start_ts == end_ts == bars[hit.bar_index].event_ts


def test_resolve_span_rejects_span_before_series_start() -> None:
    """A hit whose span reaches before bar 0 is malformed for the given series."""

    bars = _load("doji")
    bad = PatternHit(
        bar_index=0, pattern="morning_star", direction="bullish", strength=0.5, span_bars=3
    )
    with pytest.raises(ValueError, match="reaches before the start"):
        resolve_span(bad, bars)
