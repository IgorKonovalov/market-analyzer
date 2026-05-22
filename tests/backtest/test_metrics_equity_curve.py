"""Tests for `_build_equity_curve`.

Per Plan 0008 phase 1 done-when:

- Flat (no trades) → every `EquityPoint.equity == initial_capital`.
- One closed trade entered at bar i (open=100, close set per test), exited at
  bar j (open=110): bars before i are cash; bars [i, j-1] are
  `units * close`; bar j shows the exited-at-open proceeds; bars after j are
  cash again. Convention pinned here per Plan 0008 phase 1 done-when.
- Determinism: two calls on the same input return equal lists.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import isclose

from market_analyser.backtest import EquityPoint, Trade, _build_equity_curve
from market_analyser.data.types import Bar


def _bars(closes: Sequence[float], opens: Sequence[float] | None = None) -> list[Bar]:
    """Build a Bar list. If `opens` is given, OHLC = (open, max, min, close);
    else open == close (high/low cloned). All bars satisfy `Bar`'s invariants.
    """

    start = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Bar] = []
    use_opens = opens if opens is not None else closes
    for i, (o, c) in enumerate(zip(use_opens, closes, strict=True)):
        high = max(o, c)
        low = min(o, c)
        out.append(
            Bar(
                symbol="TEST",
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=o,
                high=high,
                low=low,
                close=c,
                volume=0.0,
                source="fixture",
            )
        )
    return out


def test_flat_no_trades_equals_initial_capital_everywhere() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    curve = _build_equity_curve(bars, [], initial_capital=10_000.0)
    assert len(curve) == len(bars)
    for point in curve:
        assert point.equity == 10_000.0


def test_curve_length_equals_bar_count() -> None:
    bars = _bars([10.0, 11.0, 12.0])
    curve = _build_equity_curve(bars, [], initial_capital=5_000.0)
    assert len(curve) == 3


def test_curve_ts_matches_bar_event_ts() -> None:
    bars = _bars([10.0, 11.0, 12.0])
    curve = _build_equity_curve(bars, [], initial_capital=1_000.0)
    for bar, point in zip(bars, curve, strict=True):
        assert point.ts == bar.event_ts


def test_one_closed_trade_marks_position_to_close_and_exits_at_open() -> None:
    # Bars: i=0..4. Open/close set so we can pin equity convention.
    # bars[2].open == 100 == entry_price; bars[2].close == 105 → mark-to-close.
    # bars[4].open == 110 == exit_price; bars[4].close == 108 (irrelevant since exited).
    bars = _bars(
        closes=[100.0, 100.0, 105.0, 108.0, 108.0],
        opens=[100.0, 100.0, 100.0, 108.0, 110.0],
    )
    trade = Trade(
        entry_bar_index=2,
        exit_bar_index=4,
        entry_price=100.0,
        exit_price=110.0,
        kind="long",
    )
    curve = _build_equity_curve(bars, [trade], initial_capital=10_000.0)
    # Units bought at open of bar 2: 10_000 / 100 = 100 units.
    units = 100.0
    assert curve[0].equity == 10_000.0  # flat
    assert curve[1].equity == 10_000.0  # flat
    assert isclose(curve[2].equity, units * 105.0, abs_tol=1e-9)  # mark-to-close
    assert isclose(curve[3].equity, units * 108.0, abs_tol=1e-9)  # mark-to-close
    # At bar 4: exit at open=110 → cash = units * 110 = 11_000. Held through close.
    assert isclose(curve[4].equity, units * 110.0, abs_tol=1e-9)


def test_one_closed_trade_explicit_pnl_calculation() -> None:
    # Plan 0008 phase 1 done-when example: "11_000 = 10_000 + (110 - 100) * (10_000 / 100)".
    bars = _bars(
        closes=[100.0, 100.0, 100.0, 110.0, 110.0],
        opens=[100.0, 100.0, 100.0, 100.0, 110.0],
    )
    trade = Trade(
        entry_bar_index=2,
        exit_bar_index=4,
        entry_price=100.0,
        exit_price=110.0,
        kind="long",
    )
    curve = _build_equity_curve(bars, [trade], initial_capital=10_000.0)
    assert isclose(curve[-1].equity, 11_000.0, abs_tol=1e-9)


def test_dangling_trade_holds_through_end() -> None:
    # Entry at bar 2 at open=100; no exit. Final equity = units * last close.
    bars = _bars(
        closes=[50.0, 50.0, 100.0, 110.0, 120.0],
        opens=[50.0, 50.0, 100.0, 105.0, 115.0],
    )
    trade = Trade(
        entry_bar_index=2,
        exit_bar_index=None,
        entry_price=100.0,
        exit_price=None,
        kind="long",
    )
    curve = _build_equity_curve(bars, [trade], initial_capital=10_000.0)
    units = 100.0
    assert curve[0].equity == 10_000.0
    assert curve[1].equity == 10_000.0
    assert isclose(curve[2].equity, units * 100.0, abs_tol=1e-9)
    assert isclose(curve[3].equity, units * 110.0, abs_tol=1e-9)
    assert isclose(curve[4].equity, units * 120.0, abs_tol=1e-9)


def test_deterministic_across_two_calls() -> None:
    bars = _bars(
        closes=[100.0, 105.0, 110.0, 108.0, 112.0],
        opens=[100.0, 100.0, 105.0, 110.0, 108.0],
    )
    trades = [
        Trade(
            entry_bar_index=1,
            exit_bar_index=3,
            entry_price=100.0,
            exit_price=110.0,
            kind="long",
        ),
    ]
    a = _build_equity_curve(bars, trades, initial_capital=10_000.0)
    b = _build_equity_curve(bars, trades, initial_capital=10_000.0)
    assert a == b
    # Element-wise check beyond `==` for stronger signal of byte-equivalence.
    for pa, pb in zip(a, b, strict=True):
        assert pa.ts == pb.ts
        assert pa.equity == pb.equity


def test_equity_point_is_equity_point_type() -> None:
    bars = _bars([10.0, 11.0])
    [p0, p1] = _build_equity_curve(bars, [], initial_capital=1_000.0)
    assert isinstance(p0, EquityPoint)
    assert isinstance(p1, EquityPoint)
