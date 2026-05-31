"""Phase-1 done-when for Plan 0021: `analysis/multi_timeframe.py`.

The alignment is a thin composition over `condition_snapshot`, so the load-bearing
checks are: (1) the agreement arithmetic on explicit up/down fixtures, (2) that
each per-timeframe view embeds exactly what a direct `condition_snapshot` call
returns, (3) truncation invariance (the anti-lookahead property the snapshot
already has, preserved through the alignment), and (4) the degenerate
empty-/missing-bars paths. Trends are produced by strongly-monotonic fixtures and
*verified* against a direct snapshot call rather than assumed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.analysis.multi_timeframe import multi_timeframe_alignment
from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import Trend
from market_analyser.data.types import Bar

_TOL = 1e-9


def _bar(symbol: str, timeframe: str, i: int, *, base: float) -> Bar:
    """A bar at index `i` with a tight OHLC band around `base` so the OHLC
    invariants hold and ATR/ADX are defined."""

    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        event_ts=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=base,
        high=base + 1.5,
        low=base - 1.5,
        close=base + 0.6,
        volume=1_000_000.0,
        source="fixture",
    )


def _uptrend(symbol: str, timeframe: str, n: int = 80) -> list[Bar]:
    """A steady rise with a small wobble — strong +DI, EMA20 > EMA50, close above
    EMA20 → `Trend.UP` once the EMA-50/ADX legs are warm."""

    return [
        _bar(symbol, timeframe, i, base=100.0 + 0.8 * i + (1.5 if i % 3 == 0 else -1.0))
        for i in range(n)
    ]


def _downtrend(symbol: str, timeframe: str, n: int = 80) -> list[Bar]:
    """The mirror of `_uptrend` → `Trend.DOWN`."""

    return [
        _bar(symbol, timeframe, i, base=200.0 - 0.8 * i + (1.5 if i % 3 == 0 else -1.0))
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# Agreement arithmetic                                                          #
# --------------------------------------------------------------------------- #


def test_all_up_full_agreement() -> None:
    bars_by_tf = {
        "1w": _uptrend("AAPL", "1w"),
        "1d": _uptrend("AAPL", "1d"),
        "1h": _uptrend("AAPL", "1h"),
    }
    alignment = multi_timeframe_alignment("AAPL", bars_by_tf)

    assert alignment.symbol == "AAPL"
    # Sanity: the fixtures really do classify as UP.
    assert [v.snapshot.trend for v in alignment.timeframes] == [Trend.UP, Trend.UP, Trend.UP]  # type: ignore[union-attr]
    assert alignment.dominant_trend == Trend.UP
    assert abs(alignment.agreement - 1.0) < _TOL
    assert [v.timeframe for v in alignment.timeframes] == ["1w", "1d", "1h"]


def test_one_disagreeing_timeframe_drops_agreement_and_is_named() -> None:
    bars_by_tf = {
        "1w": _uptrend("AAPL", "1w"),
        "1d": _uptrend("AAPL", "1d"),
        "1h": _downtrend("AAPL", "1h"),  # the dissenter
    }
    alignment = multi_timeframe_alignment("AAPL", bars_by_tf)

    assert alignment.dominant_trend == Trend.UP
    assert abs(alignment.agreement - (2.0 / 3.0)) < _TOL
    # The disagreeing timeframe is named by its own view: the one whose snapshot
    # trend differs from the dominant trend.
    dissenters = [
        v.timeframe
        for v in alignment.timeframes
        if v.snapshot is not None and v.snapshot.trend != alignment.dominant_trend
    ]
    assert dissenters == ["1h"]


# --------------------------------------------------------------------------- #
# Per-timeframe views match a direct snapshot call                              #
# --------------------------------------------------------------------------- #


def test_views_match_direct_condition_snapshot() -> None:
    up = _uptrend("MSFT", "1d")
    down = _downtrend("MSFT", "4h")
    alignment = multi_timeframe_alignment("MSFT", {"1d": up, "4h": down})

    by_tf = {v.timeframe: v for v in alignment.timeframes}
    expected_1d = condition_snapshot(up, "1d")
    expected_4h = condition_snapshot(down, "4h")

    assert by_tf["1d"].snapshot == expected_1d
    assert by_tf["4h"].snapshot == expected_4h
    # And the trend/momentum the alignment reasons over are exactly the snapshot's.
    assert by_tf["1d"].snapshot.trend == expected_1d.trend
    assert by_tf["1d"].snapshot.momentum == expected_1d.momentum


# --------------------------------------------------------------------------- #
# Anti-lookahead: truncation invariance through the alignment                   #
# --------------------------------------------------------------------------- #


def test_truncation_invariance() -> None:
    full: Sequence[Bar] = _uptrend("NVDA", "1d", n=80)
    cut = 60
    truncated = list(full[: cut + 1])

    alignment = multi_timeframe_alignment("NVDA", {"1d": truncated})
    view = alignment.timeframes[0]

    # The alignment's snapshot on truncated bars equals a direct snapshot on the
    # same truncated bars — appending future bars cannot have leaked in.
    assert view.snapshot == condition_snapshot(truncated, "1d")
    # And it differs from the full-series snapshot, so the truncation is real.
    assert view.snapshot.as_of != condition_snapshot(list(full), "1d").as_of


# --------------------------------------------------------------------------- #
# Degenerate paths                                                              #
# --------------------------------------------------------------------------- #


def test_missing_bars_timeframe_is_null_not_crash() -> None:
    alignment = multi_timeframe_alignment("AAPL", {"1d": _uptrend("AAPL", "1d"), "1h": []})
    by_tf = {v.timeframe: v for v in alignment.timeframes}
    assert by_tf["1h"].snapshot is None  # honest gap
    assert by_tf["1d"].snapshot is not None
    # The empty timeframe is excluded from the agreement: 1/1 available agree.
    assert alignment.dominant_trend == Trend.UP
    assert abs(alignment.agreement - 1.0) < _TOL


def test_all_timeframes_empty_falls_back_to_sideways_zero_agreement() -> None:
    alignment = multi_timeframe_alignment("AAPL", {"1d": [], "1h": []})
    assert all(v.snapshot is None for v in alignment.timeframes)
    assert alignment.dominant_trend == Trend.SIDEWAYS
    assert alignment.agreement == 0.0


def test_determinism_two_calls_equal() -> None:
    bars_by_tf = {"1d": _uptrend("AAPL", "1d"), "1h": _downtrend("AAPL", "1h")}
    assert multi_timeframe_alignment("AAPL", bars_by_tf) == multi_timeframe_alignment(
        "AAPL", bars_by_tf
    )
