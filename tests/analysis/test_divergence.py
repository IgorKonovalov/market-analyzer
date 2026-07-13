"""Phase-4 done-when for Plan 0091: `analysis/divergence.py::detect_divergences`.

Fixtures are constructed close paths whose price / RSI pivot geometry is the
divergence being tested; the assertions check the *semantic* shape (price slope vs
oscillator slope) and the anti-lookahead (truncation-invariance) guarantee, not
brittle exact bar positions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.divergence import detect_divergences
from market_analyser.analysis.levels import swing_pivots
from market_analyser.analysis.types import Divergence, PivotPoint
from market_analyser.data.types import Bar


def _bars_from_closes(closes: Sequence[float], spread: float = 0.5) -> list[Bar]:
    """Bars whose high/low track the close by ±`spread`, so a close swing high is a
    high pivot and a close swing low is a low pivot (price pivots align with the
    close path the RSI is computed from)."""

    return [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
            open=c,
            high=c + spread,
            low=c - spread,
            close=c,
            volume=1000.0,
            source="synthetic",
        )
        for i, c in enumerate(closes)
    ]


# A steep first rally (pure gains -> high RSI peak) to ~116.5, a pullback, then a
# choppy second rally to a *higher* price (~125.5) whose down-days dilute the
# average gain -> a *lower* RSI peak: the textbook regular bearish divergence.
_REGULAR_BEARISH_CLOSES: list[float] = [
    *[100.0 - 0.5 * i for i in range(16)],  # 0..15  warmup gentle decline -> 92.5
    *[92.5 + 3.0 * k for k in range(1, 10)],  # 16..24 steep pure rally -> 116.5 (peak1)
    116.5 - 2.0,  # 25
    116.5 - 4.0,  # 26
    116.5 - 6.0,  # 27
    116.5 - 8.0,  # 28  pull1 trough 108.5
    113.5,  # 29  choppy rally 2 (up/down net up) ...
    112.5,  # 30
    117.5,  # 31
    116.5,  # 32
    121.5,  # 33
    120.5,  # 34
    125.5,  # 35  peak2 (higher price than peak1)
    123.5,  # 36
    121.5,  # 37
    119.5,  # 38
    117.5,  # 39  pull2 confirms peak2
]


def _regular_bearish_bars() -> list[Bar]:
    return _bars_from_closes(_REGULAR_BEARISH_CLOSES)


def test_regular_bearish_divergence_detected() -> None:
    """Higher price high + lower RSI high -> exactly one regular bearish divergence,
    anchored on the two most recent price high pivots."""

    bars = _regular_bearish_bars()
    result = detect_divergences(bars, "rsi")
    assert len(result) == 1
    div = result[0]
    assert div.oscillator == "rsi"
    assert div.kind == "regular_bearish"
    # Price higher high, RSI lower high (the divergence's defining slopes).
    assert div.price_pivots[1].price > div.price_pivots[0].price
    assert div.oscillator_pivots[1].price < div.oscillator_pivots[0].price
    # The anchors are the two most recent confirmed price high pivots.
    high_pivots = [p for p in swing_pivots(bars, 3, 3) if p.kind == "high"]
    assert [pp.price for pp in div.price_pivots] == [high_pivots[-2].price, high_pivots[-1].price]
    assert 0.0 <= div.strength <= 1.0


def test_regular_bullish_is_the_vertical_mirror() -> None:
    """Reflecting the closes vertically swaps gains<->losses (RSI -> 100-RSI) and
    turns the higher-high/lower-RSI-high into a lower-low/higher-RSI-low: the
    textbook regular bullish divergence."""

    mirrored = [200.0 - c for c in _REGULAR_BEARISH_CLOSES]
    bars = _bars_from_closes(mirrored)
    result = detect_divergences(bars, "rsi")
    assert len(result) == 1
    div = result[0]
    assert div.kind == "regular_bullish"
    # Price lower low, RSI higher low.
    assert div.price_pivots[1].price < div.price_pivots[0].price
    assert div.oscillator_pivots[1].price > div.oscillator_pivots[0].price


# A lower-high price path where up-bars carry heavy volume and down-bars light, so
# OBV *accumulates* to a HIGHER high at the lower price peak: hidden bearish on OBV.
# (OBV's unbounded, direction-driven slope makes the hidden geometry exact, where
# RSI's bounded Wilder smoothing would need heroic tuning.)
_HIDDEN_BEARISH_CLOSES: list[float] = [
    *[100.0] * 4,  # 0..3   flat seed
    104.0,
    108.0,
    112.0,
    116.0,
    120.0,  # 8  rally1 peak1 = 120
    116.0,
    112.0,
    108.0,
    105.0,  # 12 pull1
    108.0,
    111.0,
    114.0,
    115.0,  # 16 rally2 peak2 = 115 (a LOWER high)
    112.0,
    109.0,
    107.0,  # 19 pull2 confirms peak2
]


def _accumulation_bars(closes: Sequence[float]) -> list[Bar]:
    """Bars whose volume is heavy on up-closes and light on down-closes, so OBV
    climbs regardless of the price making a lower high."""

    bars: list[Bar] = []
    prev: float | None = None
    for i, c in enumerate(closes):
        if prev is None:
            v = 500.0
        elif c > prev:
            v = 1000.0
        elif c < prev:
            v = 100.0
        else:
            v = 300.0
        bars.append(
            Bar(
                symbol="TEST",
                timeframe="1d",
                event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                open=c,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                volume=v,
                source="synthetic",
            )
        )
        prev = c
    return bars


def test_hidden_bearish_divergence_detected() -> None:
    bars = _accumulation_bars(_HIDDEN_BEARISH_CLOSES)
    result = detect_divergences(bars, "obv")
    bearish = [d for d in result if d.kind == "hidden_bearish"]
    assert len(bearish) == 1
    div = bearish[0]
    assert div.oscillator == "obv"
    # Price lower high, OBV higher high.
    assert div.price_pivots[1].price < div.price_pivots[0].price
    assert div.oscillator_pivots[1].price > div.oscillator_pivots[0].price


def test_no_divergence_when_price_and_oscillator_agree() -> None:
    """Two rising peaks reached by ever-steeper rallies: price higher high AND RSI
    higher high (they agree) -> no bearish divergence, and no low-family one -> []."""

    closes = [
        *[100.0 - 0.3 * i for i in range(16)],  # warmup
        *[95.2 + 1.0 * k for k in range(1, 8)],  # gentle rally 1 -> ~102 (peak1, modest RSI)
        *[102.2 - 1.0 * k for k in range(1, 5)],  # pullback
        *[98.2 + 4.0 * k for k in range(1, 8)],  # steep rally 2 -> higher price + higher RSI
        *[126.2 - 1.0 * k for k in range(1, 5)],  # pullback confirms peak2
    ]
    bars = _bars_from_closes(closes)
    result = detect_divergences(bars, "rsi")
    assert result == []


def test_empty_and_too_short_series_yield_no_divergences() -> None:
    assert detect_divergences([], "rsi") == []
    assert detect_divergences(_bars_from_closes([100.0, 101.0, 102.0]), "rsi") == []


def test_truncation_invariance_no_future_pivot_leaks() -> None:
    """A divergence reported at bar `i` is byte-identical when recomputed on
    `bars[0..=i]` — the later bars that exist in the full series never leaked into
    it (ADR-0023 anti-lookahead)."""

    bars = _regular_bearish_bars()
    full = detect_divergences(bars, "rsi")
    assert full
    div = full[0]
    truncated = detect_divergences(bars[: div.bar_index + 1], "rsi")
    assert div in truncated
    assert truncated[0] == div


def test_oscillator_selection_and_validation() -> None:
    bars = _regular_bearish_bars()
    # Every declared oscillator runs without error and stays trailing.
    for osc in ("rsi", "macd_hist", "obv", "mfi"):
        result = detect_divergences(bars, osc)  # type: ignore[arg-type]
        assert all(d.oscillator == osc for d in result)
    with pytest.raises(ValueError, match="oscillator must be one of"):
        detect_divergences(bars, "stochastic")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lookback must be >= 1"):
        detect_divergences(bars, "rsi", lookback=0)
    with pytest.raises(ValueError, match="pivot_window must be >= 1"):
        detect_divergences(bars, "rsi", pivot_window=0)


def test_determinism() -> None:
    bars = _regular_bearish_bars()
    assert detect_divergences(bars, "rsi") == detect_divergences(bars, "rsi")


def test_divergence_model_forbids_extra_fields() -> None:
    anchor = PivotPoint(ts=datetime(2025, 1, 1, tzinfo=UTC), price=100.0)
    with pytest.raises(ValidationError):
        Divergence(
            oscillator="rsi",
            kind="regular_bearish",
            price_pivots=[anchor, anchor],
            oscillator_pivots=[anchor, anchor],
            bar_index=10,
            strength=0.5,
            bogus=1,  # type: ignore[call-arg]
        )
