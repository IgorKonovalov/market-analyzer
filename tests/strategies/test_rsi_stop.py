"""Plan 0050 phase 6 done-when: the `rsi_stop` strategy (RSI + stop-loss).

Two load-bearing checks, both hand-computed from the Wilder recurrence (not read
off the implementation):

- On an all-declining series the strategy enters at bar 14 (RSI = 0 crosses below
  oversold) and the 5% stop breaches at bar 14's close (86) * 0.95 = 81.7; the
  first close at or below that is 81 at `bar_index = 19`. So EXIT_LONG fires at
  bar 19 — the stop bar — even though the RSI cross-up exit never comes in a
  downtrend (plain `rsi` would enter and never exit here).
- With the stop set wide enough never to breach, the signals are byte-identical
  to the plain `rsi` strategy on the same down-then-up fixture: enter 14, exit 42.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.contracts import Bar, Signal
from market_analyser.strategies import rsi, rsi_stop


def _bars(closes: Sequence[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=0.0,
            source="fixture",
        )
        for i, close in enumerate(closes)
    ]


def _all_declining_fixture() -> list[Bar]:
    """25 bars, each close 1 lower than the last (100 down to 76)."""
    return _bars([100.0 - i for i in range(25)])


def _down_then_up_fixture() -> list[Bar]:
    """The plain-`rsi` fixture: 30 declines then 30 advances."""
    return _bars([100.0 - i for i in range(30)] + [71.0 + i for i in range(1, 31)])


def test_stop_breach_exits_at_the_breaching_bar() -> None:
    bars = _all_declining_fixture()
    # entry_close = close[14] = 86; 5% stop level = 81.7; first close <= 81.7 is
    # 81 at bar 19.
    signals = list(rsi_stop.generate_signals(bars, rsi_stop.Params(stop_loss_pct=0.05)))
    assert [(s.bar_index, s.kind.value) for s in signals] == [
        (14, "enter_long"),
        (19, "exit_long"),
    ]


def test_stop_is_the_only_exit_in_a_downtrend_where_rsi_never_recovers() -> None:
    # Plain rsi on the same all-declining series enters but never exits (RSI never
    # crosses up through overbought). rsi_stop adds exactly the stop exit at 19.
    bars = _all_declining_fixture()
    plain = [(s.bar_index, s.kind.value) for s in rsi.generate_signals(bars, rsi.Params())]
    stopped = [
        (s.bar_index, s.kind.value)
        for s in rsi_stop.generate_signals(bars, rsi_stop.Params(stop_loss_pct=0.05))
    ]
    assert plain == [(14, "enter_long")]  # enters, never exits
    assert stopped == [(14, "enter_long"), (19, "exit_long")]  # stop adds the exit


def test_wide_stop_matches_plain_rsi() -> None:
    bars = _down_then_up_fixture()
    # 0.99 stop => level = 86 * 0.01 = 0.86; the lowest close (71) never reaches it.
    stopped = list(rsi_stop.generate_signals(bars, rsi_stop.Params(stop_loss_pct=0.99)))
    plain = list(rsi.generate_signals(bars, rsi.Params()))
    assert stopped == plain
    assert [(s.bar_index, s.kind.value) for s in stopped] == [
        (14, "enter_long"),
        (42, "exit_long"),
    ]


def test_module_loads_and_params_construct() -> None:
    assert rsi_stop.META.id == "rsi_stop"
    params = rsi_stop.Params()
    assert params.stop_loss_pct == 0.05
    assert params.period == 14


def test_returns_signals_with_in_range_indices() -> None:
    bars = _all_declining_fixture()
    signals = rsi_stop.generate_signals(bars, rsi_stop.Params())
    assert all(isinstance(s, Signal) for s in signals)
    assert all(0 <= s.bar_index < len(bars) for s in signals)


def test_is_deterministic() -> None:
    bars = _all_declining_fixture()
    a = list(rsi_stop.generate_signals(bars, rsi_stop.Params(stop_loss_pct=0.05)))
    b = list(rsi_stop.generate_signals(bars, rsi_stop.Params(stop_loss_pct=0.05)))
    assert a == b


def test_has_no_lookahead() -> None:
    # A signal at bar_index = k must depend only on bars[0..=k]: truncating the
    # input at each signal bar yields the same prefix of signals.
    bars = _all_declining_fixture()
    params = rsi_stop.Params(stop_loss_pct=0.05)
    full = list(rsi_stop.generate_signals(bars, params))
    for sig in full:
        prefix = list(rsi_stop.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_params_validate_field_constraints() -> None:
    with pytest.raises(ValidationError):
        rsi_stop.Params(period=1)
    with pytest.raises(ValidationError):
        rsi_stop.Params(stop_loss_pct=0.0)  # must be > 0
    with pytest.raises(ValidationError):
        rsi_stop.Params(stop_loss_pct=1.5)  # must be <= 1.0


def test_is_discoverable() -> None:
    from market_analyser.contracts.strategy import discover

    assert "rsi_stop" in discover()
