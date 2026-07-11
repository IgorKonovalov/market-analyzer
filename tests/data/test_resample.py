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


def test_unknown_target_rejected() -> None:
    with pytest.raises(ValueError, match="unknown timeframe"):
        resample_ohlcv(_BARS_1H, target="2h")


# --- Plan 0081 / ADR-0076: weekly and monthly calendar resampling (1w/1mo ← 1d) --


def _d(date: datetime, *, o: float, h: float, low: float, c: float, v: float) -> Bar:
    """A 1d bar at `date` (00:00 UTC) with explicit OHLCV."""
    return Bar(
        symbol="BTC-USD",
        timeframe="1d",
        event_ts=date,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        source="coinbase",
    )


# Daily bars straddling two ISO weeks. Note the first bar is a WEDNESDAY, so the
# week-1 bucket must anchor on Monday 2026-01-05 (calendar week), not on the first
# bar's date — the load-bearing property of calendar bucketing.
_DAILY_WEEKS: list[Bar] = [
    _d(datetime(2026, 1, 7, tzinfo=UTC), o=100, h=105, low=98, c=104, v=10),  # Wed, wk1
    _d(datetime(2026, 1, 8, tzinfo=UTC), o=104, h=108, low=103, c=107, v=11),  # Thu, wk1
    _d(datetime(2026, 1, 9, tzinfo=UTC), o=107, h=109, low=106, c=108, v=12),  # Fri, wk1
    _d(datetime(2026, 1, 12, tzinfo=UTC), o=108, h=112, low=101, c=110, v=13),  # Mon, wk2
    _d(datetime(2026, 1, 13, tzinfo=UTC), o=110, h=115, low=104, c=113, v=14),  # Tue, wk2
]


def test_weekly_resample_buckets_on_the_calendar_week() -> None:
    bars = resample_ohlcv(_DAILY_WEEKS, target="1w")

    # Two ISO-week buckets, each stamped at its Monday open.
    assert [b.event_ts for b in bars] == [
        datetime(2026, 1, 5, tzinfo=UTC),  # Monday of week 1 (though data starts Wed)
        datetime(2026, 1, 12, tzinfo=UTC),  # Monday of week 2
    ]
    assert all(b.timeframe == "1w" for b in bars)
    assert all(b.source == "coinbase" for b in bars)
    # week 1: Wed/Thu/Fri aggregated.
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        100.0,
        109.0,
        98.0,
        108.0,
        33.0,
    )
    # week 2: Mon/Tue aggregated.
    assert (bars[1].open, bars[1].high, bars[1].low, bars[1].close, bars[1].volume) == (
        108.0,
        115.0,
        101.0,
        113.0,
        27.0,
    )


# Daily bars straddling the Jan/Feb boundary — the calendar-month bucketing case
# (ADR-0047 revisited for a gap-free 24/7 series).
_DAILY_MONTHS: list[Bar] = [
    _d(datetime(2026, 1, 30, tzinfo=UTC), o=200, h=205, low=199, c=204, v=20),
    _d(datetime(2026, 1, 31, tzinfo=UTC), o=204, h=208, low=203, c=207, v=21),
    _d(datetime(2026, 2, 1, tzinfo=UTC), o=207, h=210, low=206, c=209, v=22),
    _d(datetime(2026, 2, 2, tzinfo=UTC), o=209, h=212, low=205, c=211, v=23),
]


def test_monthly_resample_buckets_on_the_calendar_month() -> None:
    bars = resample_ohlcv(_DAILY_MONTHS, target="1mo")

    assert [b.event_ts for b in bars] == [
        datetime(2026, 1, 1, tzinfo=UTC),  # January bucket (from the 30th/31st)
        datetime(2026, 2, 1, tzinfo=UTC),  # February bucket
    ]
    assert all(b.timeframe == "1mo" for b in bars)
    # January: 30th + 31st.
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (
        200.0,
        208.0,
        199.0,
        207.0,
        41.0,
    )
    # February: 1st + 2nd.
    assert (bars[1].open, bars[1].high, bars[1].low, bars[1].close, bars[1].volume) == (
        207.0,
        212.0,
        205.0,
        211.0,
        45.0,
    )


@pytest.mark.parametrize(
    ("bars", "target"),
    [(_DAILY_WEEKS, "1w"), (_DAILY_MONTHS, "1mo")],
)
def test_weekly_monthly_anti_lookahead_completed_buckets_never_change(
    bars: list[Bar], target: str
) -> None:
    # For every prefix of the daily series, every completed bucket must equal the
    # full resample's corresponding bucket (only the accumulating last one moves).
    full = [b.model_dump() for b in resample_ohlcv(bars, target=target)]
    for k in range(1, len(bars) + 1):
        prefix = [b.model_dump() for b in resample_ohlcv(bars[:k], target=target)]
        for i in range(len(prefix) - 1):
            assert prefix[i] == full[i], f"bucket {i} changed at prefix length {k}"


@pytest.mark.parametrize(
    ("bars", "target"),
    [(_DAILY_WEEKS, "1w"), (_DAILY_MONTHS, "1mo")],
)
def test_weekly_monthly_deterministic_and_order_independent(bars: list[Bar], target: str) -> None:
    a = [b.model_dump() for b in resample_ohlcv(bars, target=target)]
    b = [b.model_dump() for b in resample_ohlcv(list(reversed(bars)), target=target)]
    assert a == b
