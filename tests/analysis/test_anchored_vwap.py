"""Plan 0092 phase 3 done-when: anchored VWAP (`analysis/volume.py`).

- The anchored VWAP from a given anchor matches a hand-computed accumulation and
  is trailing (the value at bar `i` uses only `anchor..i`).
- A degenerate zero-volume window yields `None` (no divide-by-zero), matching the
  existing `vwap` guard.
- An out-of-range anchor raises; empty bars yield `[]`.
- `AnchoredVwapValue` rejects an extra field (`extra="forbid"`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.analysis.types import AnchoredVwapValue
from market_analyser.analysis.volume import anchored_vwap, anchored_vwap_value
from market_analyser.data.types import Bar

_TOL = 1e-9
_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _bar(i: int, *, tp: float, vol: float) -> Bar:
    # A flat bar (open=high=low=close=tp) makes the typical price exactly `tp`.
    return Bar(
        symbol="TEST",
        timeframe="1d",
        event_ts=_T0 + timedelta(days=i),
        open=tp,
        high=tp,
        low=tp,
        close=tp,
        volume=vol,
        source="synthetic",
    )


def _bars() -> list[Bar]:
    return [
        _bar(0, tp=10.0, vol=100.0),  # before the anchor
        _bar(1, tp=20.0, vol=100.0),  # anchor
        _bar(2, tp=30.0, vol=200.0),
        _bar(3, tp=40.0, vol=100.0),
    ]


def test_anchored_vwap_matches_hand_accumulation() -> None:
    series = anchored_vwap(_bars(), anchor_index=1)
    assert series[0] is None  # before the anchor
    assert series[1] == pytest.approx(20.0, abs=_TOL)  # 2000/100
    assert series[2] == pytest.approx(8000.0 / 300.0, abs=_TOL)  # (2000+6000)/300
    assert series[3] == pytest.approx(30.0, abs=_TOL)  # 12000/400


def test_anchored_vwap_is_trailing() -> None:
    bars = _bars()
    full = anchored_vwap(bars, 1)
    # The value at each bar depends only on anchor..that bar: truncating the future
    # leaves the earlier values byte-identical.
    assert anchored_vwap(bars[:3], 1) == full[:3]


def test_anchored_vwap_zero_volume_is_none() -> None:
    bars = [_bar(0, tp=10.0, vol=0.0), _bar(1, tp=20.0, vol=0.0), _bar(2, tp=30.0, vol=50.0)]
    series = anchored_vwap(bars, anchor_index=0)
    assert series[0] is None  # cumulative volume still 0 — undefined, not a crash
    assert series[1] is None
    assert series[2] == pytest.approx(30.0, abs=_TOL)  # volume finally accrues


def test_anchored_vwap_value_composes_latest_with_provenance() -> None:
    bars = _bars()
    value = anchored_vwap_value(bars, anchor_index=1)
    assert value.anchor_index == 1
    assert value.anchor_ts == bars[1].event_ts
    assert value.value == pytest.approx(30.0, abs=_TOL)


def test_anchored_vwap_value_none_when_all_zero_volume() -> None:
    bars = [_bar(0, tp=10.0, vol=0.0), _bar(1, tp=20.0, vol=0.0)]
    assert anchored_vwap_value(bars, anchor_index=0).value is None


def test_anchored_vwap_out_of_range_anchor_raises() -> None:
    bars = _bars()
    with pytest.raises(ValueError, match="out of range"):
        anchored_vwap(bars, anchor_index=len(bars))
    with pytest.raises(ValueError, match="out of range"):
        anchored_vwap(bars, anchor_index=-1)


def test_anchored_vwap_empty_bars_is_empty() -> None:
    assert anchored_vwap([], anchor_index=0) == []
    with pytest.raises(ValueError, match="at least one bar"):
        anchored_vwap_value([], anchor_index=0)


def test_anchored_vwap_value_forbids_extra_field() -> None:
    with pytest.raises(ValidationError):
        AnchoredVwapValue(
            anchor_index=0,
            anchor_ts=_T0,
            value=1.0,
            bogus=1,  # type: ignore[call-arg]
        )
