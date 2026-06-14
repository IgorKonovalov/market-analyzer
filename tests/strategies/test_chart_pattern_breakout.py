"""Plan 0054 phase 1 done-when: the `chart_pattern_breakout` strategy.

The strategy maps each *confirmed* `ChartPatternHit` to a directional entry at
its confirming bar. The fixtures are the exact constructed paths the detector's
own test pins (`tests/analysis/test_chart_patterns.py`), so the geometry is
known-good; the assertions here read the *confirming* bar straight off
`detect_chart_patterns` (the single source of truth for "where the breakout
confirmed") and check the strategy entered there — proving the entry sits at the
confirming bar, never the formation bar, without hardcoding a bar index that
can't be hand-derived from the ATR-margin break rule.

Done-when coverage:
- confirmed inverse-H&S -> exactly one `enter_long` at the confirming bar;
- confirmed H&S -> an `enter_short` at the confirming bar;
- a never-breaking (forming-only) fixture -> no signal;
- purity / no-lookahead pinned by a truncation test;
- `discover()` finds the module by its `META.id`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from market_analyser.analysis.chart_patterns import detect_chart_patterns
from market_analyser.analysis.types import ChartPatternHit
from market_analyser.contracts import Bar, Signal
from market_analyser.strategies import chart_pattern_breakout as cpb


def _bars_from_path(anchors: list[tuple[int, float]]) -> list[Bar]:
    """Sample a piecewise-linear base path into bars (the detector test's
    fixture builder): high/low straddle the base by 1.0, open/close on it, so
    the only swing pivots are the interior anchor extremes."""

    n = anchors[-1][0] + 1
    bases: list[float] = []
    for i in range(n):
        for (x1, p1), (x2, p2) in pairwise(anchors):
            if x1 <= i <= x2:
                bases.append(p1 + (p2 - p1) * (i - x1) / (x2 - x1))
                break
    return [
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


# Constructed fixtures lifted from the detector's own test — known to confirm.
_INVERSE_HS_ANCHORS = [
    (0, 120.0),
    (6, 110.0),
    (10, 120.0),
    (14, 100.0),
    (18, 119.0),
    (22, 109.5),
    (35, 142.0),
]
_HS_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 78.0),
]
# Same H&S geometry, but the tail drifts sideways above the neckline forever:
# forming only, never confirmed.
_HS_NO_BREAK_ANCHORS = [
    (0, 100.0),
    (6, 110.0),
    (10, 100.0),
    (14, 120.0),
    (18, 101.0),
    (22, 110.5),
    (35, 104.0),
]


def _confirmed_hit(bars: list[Bar], pattern: str) -> ChartPatternHit:
    """The single confirmed hit for `pattern` (the source of truth for the
    confirming bar)."""

    confirmed = [
        h for h in detect_chart_patterns(bars) if h.pattern == pattern and h.state == "confirmed"
    ]
    assert len(confirmed) == 1, f"{pattern}: expected exactly one confirmed hit"
    return confirmed[0]


def _forming_bar(bars: list[Bar], pattern: str) -> int:
    forming = next(
        h for h in detect_chart_patterns(bars) if h.pattern == pattern and h.state == "forming"
    )
    return forming.bar_index


def test_confirmed_inverse_hs_emits_one_enter_long_at_the_confirming_bar() -> None:
    bars = _bars_from_path(_INVERSE_HS_ANCHORS)
    confirm = _confirmed_hit(bars, "inverse_head_shoulders")
    assert confirm.direction == "bullish"
    # The fixture is a single clean bullish formation: its only confirmed hit is
    # the inverse-H&S, so the strategy emits exactly one entry.
    all_confirmed = [h for h in detect_chart_patterns(bars) if h.state == "confirmed"]
    assert all_confirmed == [confirm]

    signals = list(cpb.generate_signals(bars, cpb.Params()))
    longs = [s for s in signals if s.kind.value == "enter_long"]
    assert len(longs) == 1
    # At the *confirming* bar, not the formation completion bar.
    assert longs[0].bar_index == confirm.bar_index
    assert confirm.bar_index > _forming_bar(bars, "inverse_head_shoulders")
    # A bullish-only fixture trades long-only — no short, no exit.
    assert [s.kind.value for s in signals] == ["enter_long"]


def test_confirmed_hs_emits_enter_short_at_the_confirming_bar() -> None:
    bars = _bars_from_path(_HS_ANCHORS)
    confirm = _confirmed_hit(bars, "head_shoulders")
    assert confirm.direction == "bearish"
    all_confirmed = [h for h in detect_chart_patterns(bars) if h.state == "confirmed"]
    assert all_confirmed == [confirm]

    signals = list(cpb.generate_signals(bars, cpb.Params()))
    shorts = [s for s in signals if s.kind.value == "enter_short"]
    assert len(shorts) == 1
    assert shorts[0].bar_index == confirm.bar_index
    assert confirm.bar_index > _forming_bar(bars, "head_shoulders")
    assert [s.kind.value for s in signals] == ["enter_short"]


def test_long_only_suppresses_the_short_on_a_bearish_breakout() -> None:
    bars = _bars_from_path(_HS_ANCHORS)
    signals = list(cpb.generate_signals(bars, cpb.Params(long_only=True)))
    assert not any(s.kind.value in ("enter_short", "exit_short") for s in signals)


def test_forming_only_fixture_emits_no_signal() -> None:
    """A formation that never breaks the neckline stays `forming` on every
    truncation and never confirms — so the strategy never trades it."""

    bars = _bars_from_path(_HS_NO_BREAK_ANCHORS)
    hits = detect_chart_patterns(bars)
    assert any(h.pattern == "head_shoulders" and h.state == "forming" for h in hits)
    assert not any(h.state == "confirmed" for h in hits)
    assert list(cpb.generate_signals(bars, cpb.Params())) == []


def test_no_lookahead_truncation_invariance() -> None:
    """A signal at bar_index = k depends only on bars[0..=k]: truncating the
    input at each signal bar reproduces the same prefix of signals."""

    bars = _bars_from_path(_HS_ANCHORS)
    params = cpb.Params()
    full = list(cpb.generate_signals(bars, params))
    assert full, "the H&S fixture must produce at least one signal"
    for sig in full:
        prefix = list(cpb.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_is_deterministic() -> None:
    bars = _bars_from_path(_INVERSE_HS_ANCHORS)
    a = list(cpb.generate_signals(bars, cpb.Params()))
    b = list(cpb.generate_signals(bars, cpb.Params()))
    assert a == b


def test_returns_signals_with_in_range_indices() -> None:
    bars = _bars_from_path(_HS_ANCHORS)
    signals = cpb.generate_signals(bars, cpb.Params())
    assert all(isinstance(s, Signal) for s in signals)
    assert all(0 <= s.bar_index < len(bars) for s in signals)


def test_module_loads_and_params_defaults() -> None:
    assert cpb.META.id == "chart_pattern_breakout"
    params = cpb.Params()
    assert params.long_only is False  # both directions by default
    assert params.enable_triangles_wedges is True
    assert params.exit_only_on_opposite is True


def test_is_discoverable() -> None:
    from market_analyser.contracts.strategy import discover

    assert "chart_pattern_breakout" in discover()
