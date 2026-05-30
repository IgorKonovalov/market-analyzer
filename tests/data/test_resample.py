"""Plan 0025 phase 2: the in-house 1h -> 4h resampler (`data/resample.py`).

Covers the ADR-0028 contract: UTC-aligned bucketing, partial trailing bucket,
the load-bearing anti-lookahead property (appending future bars never mutates a
completed 4h bar), and determinism.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.data.resample import resample_ohlcv
from market_analyser.data.types import Bar


def _h(hour: int, *, o: float, h: float, low: float, c: float, v: float) -> Bar:
    """A 1h bar on 2026-01-05 at `hour:00` UTC with explicit OHLCV."""
    return Bar(
        symbol="AAPL",
        timeframe="1h",
        event_ts=datetime(2026, 1, 5, hour, 0, tzinfo=UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        source="yahoo",
    )


# Two complete 4h buckets (04:00 and 08:00) plus a partial one (12:00 with only
# two 1h bars). OHLCV chosen so first/last/max/min/sum are each unambiguous.
_BARS_1H: list[Bar] = [
    _h(4, o=100, h=105, low=99, c=104, v=10),
    _h(5, o=104, h=106, low=103, c=105, v=11),
    _h(6, o=105, h=108, low=102, c=107, v=12),
    _h(7, o=107, h=109, low=106, c=108, v=13),
    _h(8, o=108, h=110, low=107, c=109, v=14),
    _h(9, o=109, h=111, low=108, c=110, v=15),
    _h(10, o=110, h=112, low=104, c=111, v=16),
    _h(11, o=111, h=113, low=110, c=112, v=17),
    _h(12, o=112, h=115, low=111, c=114, v=18),
    _h(13, o=114, h=116, low=110, c=115, v=19),
]


def test_bucketing_is_correct_bar_by_bar() -> None:
    bars = resample_ohlcv(_BARS_1H, target="4h")

    assert [b.event_ts for b in bars] == [
        datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
    ]
    assert all(b.timeframe == "4h" for b in bars)

    # bucket 04:00 — aggregates the 04..07 1h bars.
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        100.0,
        109.0,
        99.0,
        108.0,
        46.0,
    )
    # bucket 08:00 — aggregates the 08..11 1h bars.
    assert (bars[1].open, bars[1].high, bars[1].low, bars[1].close, bars[1].volume) == (
        108.0,
        113.0,
        104.0,
        112.0,
        62.0,
    )


def test_partial_trailing_bucket_is_emitted_from_available_bars() -> None:
    bars = resample_ohlcv(_BARS_1H, target="4h")

    # bucket 12:00 holds only the 12:00 + 13:00 1h bars (a full window would be
    # 12..15). It is emitted from those two, not dropped, not padded.
    partial = bars[-1]
    assert partial.event_ts == datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    assert (partial.open, partial.high, partial.low, partial.close, partial.volume) == (
        112.0,
        116.0,
        110.0,
        115.0,
        37.0,
    )


def test_anti_lookahead_completed_buckets_never_change() -> None:
    # Load-bearing: appending future 1h bars must not mutate any already-completed
    # 4h bar. For every prefix of the 1h series, every 4h bar EXCEPT the still-
    # accumulating last one must equal the corresponding bar of the full resample.
    full = [b.model_dump() for b in resample_ohlcv(_BARS_1H, target="4h")]
    for k in range(1, len(_BARS_1H) + 1):
        prefix = [b.model_dump() for b in resample_ohlcv(_BARS_1H[:k], target="4h")]
        for i in range(len(prefix) - 1):
            assert prefix[i] == full[i], f"bucket {i} changed at prefix length {k}"


def test_prefix_ending_on_a_boundary_matches_full_exactly() -> None:
    # bars[:8] ends at 11:00 — both the 04:00 and 08:00 buckets are complete, so
    # the truncated resample equals the first two bars of the full resample exactly.
    truncated = [b.model_dump() for b in resample_ohlcv(_BARS_1H[:8], target="4h")]
    full = [b.model_dump() for b in resample_ohlcv(_BARS_1H, target="4h")]
    assert truncated == full[:2]


def test_deterministic_across_calls_and_input_order() -> None:
    a = [b.model_dump() for b in resample_ohlcv(_BARS_1H, target="4h")]
    b = [b.model_dump() for b in resample_ohlcv(_BARS_1H, target="4h")]
    assert a == b
    # No dependence on input ordering — a reversed input yields the same buckets.
    reversed_in = [b.model_dump() for b in resample_ohlcv(list(reversed(_BARS_1H)), target="4h")]
    assert reversed_in == a


def test_empty_input_yields_no_bars() -> None:
    assert resample_ohlcv([], target="4h") == []


def test_native_target_rejected() -> None:
    with pytest.raises(ValueError, match="native"):
        resample_ohlcv(_BARS_1H, target="1h")
