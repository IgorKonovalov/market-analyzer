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

from market_analyser.analysis import PatternHit, detect_patterns
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
