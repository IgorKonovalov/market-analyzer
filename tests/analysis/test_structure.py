"""Plan 0092 phase 2 done-when: `analysis/structure.py` (ADR-0084).

- A constructed HH/HL fixture yields `structural_trend="up"` with the right labels;
  the LH/LL mirror yields `"down"`; a choppy fixture yields `"range"`.
- A fixture that takes out a prior swing low after an uptrend emits a `CHoCH` at
  the correct bar (and the establishing upside break is a `BOS`).
- Truncation invariance: every label / event reported on `bars[0..=k]` is a prefix
  of the full-series read — confirmed-pivot-only, no future leak.
- `MarketStructure` / `StructureEvent` reject an extra field (`extra="forbid"`).
- `structural_trend` is a distinct read (ADR-0084): a plain literal with a `range`
  value the indicator `Trend` enum cannot express — a separate fact, not `trend`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.structure import market_structure
from market_analyser.analysis.types import (
    MarketStructure,
    PivotPoint,
    StructureEvent,
    Trend,
)
from market_analyser.data.types import Bar

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _bars_from_values(values: Sequence[float]) -> list[Bar]:
    """One bar per value: `high = v + 0.5`, `low = v - 0.5`, `open = close = v`, so
    a local extreme in `values` is a strict swing pivot in high/low, and the close
    equals the value (drives the break detection)."""

    bars: list[Bar] = []
    for i, v in enumerate(values):
        bars.append(
            Bar(
                symbol="TEST",
                timeframe="1d",
                event_ts=_T0 + timedelta(days=i),
                open=v,
                high=v + 0.5,
                low=v - 0.5,
                close=v,
                volume=1000.0,
                source="synthetic",
            )
        )
    return bars


# Ascending swings: L@4=90, H@8=110, L@12=95(HL), H@16=120(HH), L@20=100(HL), H@24=130(HH)
_UPTREND = [
    110,
    105,
    100,
    95,
    90,
    95,
    100,
    105,
    110,
    106.25,
    102.5,
    98.75,
    95,
    101.25,
    107.5,
    113.75,
    120,
    115,
    110,
    105,
    100,
    107.5,
    115,
    122.5,
    130,
    125,
    120,
    115,
]
# Descending swings: H@4=130, L@8=100, H@12=120(LH), L@16=90(LL), H@20=110(LH), L@24=80(LL)
_DOWNTREND = [
    110,
    115,
    120,
    125,
    130,
    122.5,
    115,
    107.5,
    100,
    105,
    110,
    115,
    120,
    112.5,
    105,
    97.5,
    90,
    95,
    100,
    105,
    110,
    102.5,
    95,
    87.5,
    80,
    85,
    90,
    95,
]
# Mixed: L@4=100, H@8=110, L@12=90(LL), H@16=120(HH) -> HH high + LL low -> range
_RANGE = [
    108,
    106,
    104,
    102,
    100,
    102.5,
    105,
    107.5,
    110,
    105,
    100,
    95,
    90,
    97.5,
    105,
    112.5,
    120,
    115,
    110,
    105,
]
# Uptrend then breakdown: H1@4=110, L1@8=100, H2@12=116, L2@20=85. A close clears
# H1 at bar 11 (BOS up), then closes below L1 at bar 17 (CHoCH down, bias was up).
_CHOCH = [
    100,
    102.5,
    105,
    107.5,
    110,
    107.5,
    105,
    102.5,
    100,
    104,
    108,
    112,
    116,
    112.125,
    108.25,
    104.375,
    100.5,
    96.625,
    92.75,
    88.875,
    85,
    90,
    95,
    100,
    105,
]


def _labels(ms: MarketStructure) -> list[str]:
    return [label for _, label in ms.labeled_pivots]


def test_uptrend_hh_hl_yields_up() -> None:
    ms = market_structure(_bars_from_values(_UPTREND))
    assert ms.structural_trend == "up"
    assert _labels(ms) == ["HL", "HH", "HL", "HH"]


def test_downtrend_lh_ll_yields_down() -> None:
    ms = market_structure(_bars_from_values(_DOWNTREND))
    assert ms.structural_trend == "down"
    assert _labels(ms) == ["LH", "LL", "LH", "LL"]


def test_mixed_structure_yields_range() -> None:
    ms = market_structure(_bars_from_values(_RANGE))
    assert ms.structural_trend == "range"
    assert _labels(ms) == ["LL", "HH"]


def test_choch_after_uptrend_at_correct_bar() -> None:
    bars = _bars_from_values(_CHOCH)
    ms = market_structure(bars, bos_margin_atr=0.0)  # isolate structure from ATR margin
    # Establishing upside break of the prior swing high, then the counter-trend
    # break of the prior swing low.
    assert ms.events == [
        StructureEvent(kind="BOS", direction="bullish", bar_index=11, price=110.5),
        StructureEvent(kind="CHoCH", direction="bearish", bar_index=17, price=99.5),
    ]


# --------------------------------------------------------------------------- #
# Truncation invariance (anti-lookahead)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("values", [_UPTREND, _DOWNTREND, _RANGE, _CHOCH])
def test_truncation_invariance(values: list[float]) -> None:
    bars = _bars_from_values(values)
    full = market_structure(bars, bos_margin_atr=0.0)
    for k in range(1, len(bars) + 1):
        partial = market_structure(bars[:k], bos_margin_atr=0.0)
        # Events on bars[0..=k-1] are exactly the full-series events at bar < k.
        assert partial.events == [e for e in full.events if e.bar_index < k]
        # Labeled pivots are a prefix of the full-series labeling.
        assert partial.labeled_pivots == full.labeled_pivots[: len(partial.labeled_pivots)]


# --------------------------------------------------------------------------- #
# Model hygiene + ADR-0084 distinctness                                        #
# --------------------------------------------------------------------------- #


def test_structure_event_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        StructureEvent(
            kind="BOS",
            direction="bullish",
            bar_index=1,
            price=100.0,
            bogus=1,  # type: ignore[call-arg]
        )


def test_market_structure_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        MarketStructure(
            structural_trend="range",
            labeled_pivots=[],
            events=[],
            bogus=1,  # type: ignore[call-arg]
        )


def test_structural_trend_is_a_distinct_read() -> None:
    ms = market_structure(_bars_from_values(_RANGE))
    # A plain string literal, not the indicator Trend enum member...
    assert isinstance(ms.structural_trend, str)
    assert not isinstance(ms.structural_trend, Trend)
    # ...with a `range` value the indicator Trend vocabulary cannot express — proof
    # this is a genuinely separate classification, not a view of `trend` (ADR-0084).
    assert ms.structural_trend == "range"
    assert "range" not in {t.value for t in Trend}


def test_empty_bars_is_range_with_no_events() -> None:
    ms = market_structure([])
    assert ms == MarketStructure(structural_trend="range", labeled_pivots=[], events=[])
    assert isinstance(ms.labeled_pivots, list) and isinstance(ms.events, list)
    # A well-formed anchor still round-trips through the model (sanity on the tuple).
    assert PivotPoint(ts=_T0, price=1.0).price == 1.0
