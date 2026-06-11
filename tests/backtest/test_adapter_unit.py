"""Unit tests for `signals_to_trades`.

Hand-rolled `Signal` lists, hand-computed expected `Trade` lists. These tests
exist to catch adapter bugs that the golden test cannot — the golden test
compares two computed values (RSI signals → adapter output → JSON) and would
pass if both sides were equally wrong. The unit tests anchor against values a
human wrote down.

Cases (per Plan 0002 phase 3 done-when, plus terminal-signal drop):

1. Clean entry → exit pair: one trade, both ends populated.
2. Dangling entry: enter with no exit → one trade, `exit_*` is `None`.
3. EXIT_LONG with no prior ENTER_LONG: ignored, no trade emitted.
4. Back-to-back ENTER_LONGs (no exit between): the second is ignored.
5. Signal whose `bar_index + 1` is past the end of the series: silently
   dropped (no executable open price).

Plan 0053 phase 2 (ADR-0050) adds the flat/long/short cases:

6. Short entry → exit pair and dangling short (mirrors of 1-2).
7. Single-direction invariants: `enter_*` while in any position is a no-op;
   `exit_*` only closes a position of its own direction.
8. Same-bar exit + enter (e.g. `exit_long` + `enter_short` at one
   `bar_index`) executes exit-first-then-enter, both at the same next open,
   regardless of emission order.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import Trade, signals_to_trades
from market_analyser.contracts import Bar, Signal, SignalKind


def _bars(opens: Sequence[float]) -> list[Bar]:
    """Build a Bar list whose OPEN sequence is `opens` (high/low/close cloned).

    The adapter only reads `bar.open`, so cloning the open into the other OHLC
    fields keeps `Bar`'s high/low invariants satisfied without inventing data
    that could mislead a reader.
    """

    start = datetime(2026, 1, 1, tzinfo=UTC)
    out: list[Bar] = []
    for i, price in enumerate(opens):
        out.append(
            Bar(
                symbol="TEST",
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0.0,
                source="fixture",
            )
        )
    return out


def test_clean_entry_exit_pair_produces_one_trade() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.EXIT_LONG),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=3,
            entry_price=11.0,
            exit_price=13.0,
            kind="long",
        )
    ]


def test_dangling_entry_emits_trade_with_none_exit() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=0, kind=SignalKind.ENTER_LONG)]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=None,
            entry_price=11.0,
            exit_price=None,
            kind="long",
        )
    ]


def test_exit_without_prior_entry_is_ignored() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=0, kind=SignalKind.EXIT_LONG)]
    assert signals_to_trades(bars, signals) == []


def test_second_entry_is_ignored_while_long() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=4, kind=SignalKind.EXIT_LONG),
    ]
    # Only the first ENTER opens a trade; the second is ignored. Exit closes
    # the original position at bar 5 (executes at the open of bar_index + 1).
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=5,
            entry_price=11.0,
            exit_price=15.0,
            kind="long",
        )
    ]


def test_signal_on_last_bar_is_dropped() -> None:
    # bars has indices 0..3; a signal at bar_index = 3 would need bars[4] to
    # execute at. There isn't one, so the adapter drops it silently. (Future
    # engine plans can surface this via BacktestResult; here it's just a
    # missing trade.)
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=3, kind=SignalKind.ENTER_LONG)]
    assert signals_to_trades(bars, signals) == []


def test_referentially_transparent() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.EXIT_LONG),
    ]
    a = signals_to_trades(bars, signals)
    b = signals_to_trades(bars, signals)
    assert a == b


# --- Plan 0053 phase 2: flat/long/short state machine (ADR-0050) --------------


def test_short_entry_exit_pair_produces_one_short_trade() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=3,
            entry_price=11.0,
            exit_price=13.0,
            kind="short",
        )
    ]


def test_dangling_short_emits_trade_with_none_exit() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=0, kind=SignalKind.ENTER_SHORT)]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=None,
            entry_price=11.0,
            exit_price=None,
            kind="short",
        )
    ]


def test_exit_short_without_prior_entry_is_ignored() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=0, kind=SignalKind.EXIT_SHORT)]
    assert signals_to_trades(bars, signals) == []


def test_enter_short_while_long_is_ignored() -> None:
    # Done-when: `enter_short` while long (with no same-bar long-exit) is a
    # no-op — the long rides through it and closes on its own exit.
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=4, kind=SignalKind.EXIT_LONG),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=5,
            entry_price=11.0,
            exit_price=15.0,
            kind="long",
        )
    ]


def test_enter_long_while_short_is_ignored() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=4, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=5,
            entry_price=11.0,
            exit_price=15.0,
            kind="short",
        )
    ]


def test_second_enter_short_is_ignored_while_short() -> None:
    # No pyramiding, in either direction.
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=4, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=5,
            entry_price=11.0,
            exit_price=15.0,
            kind="short",
        )
    ]


def test_exit_of_the_other_direction_is_ignored() -> None:
    # `exit_long` while short (and vice versa) closes nothing.
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.EXIT_LONG),
        Signal(bar_index=4, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=5,
            entry_price=11.0,
            exit_price=15.0,
            kind="short",
        )
    ]


def _same_bar_flip_expected() -> list[Trade]:
    """The pinned ADR-0050 outcome for exit_long+enter_short at bar 2: the long
    closes and the short opens at the SAME next open (bars[3].open = 13.0)."""

    return [
        Trade(
            entry_bar_index=1,
            exit_bar_index=3,
            entry_price=11.0,
            exit_price=13.0,
            kind="long",
        ),
        Trade(
            entry_bar_index=3,
            exit_bar_index=5,
            entry_price=13.0,
            exit_price=15.0,
            kind="short",
        ),
    ]


def test_same_bar_exit_long_enter_short_executes_exit_first() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.EXIT_LONG),
        Signal(bar_index=2, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=4, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == _same_bar_flip_expected()


def test_same_bar_flip_is_emission_order_independent() -> None:
    # ADR-0050 pins exit-first-then-enter even when the strategy emitted the
    # enter before the exit for the same bar.
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.EXIT_LONG),
        Signal(bar_index=4, kind=SignalKind.EXIT_SHORT),
    ]
    assert signals_to_trades(bars, signals) == _same_bar_flip_expected()


def test_same_bar_exit_short_enter_long_executes_exit_first() -> None:
    # The mirror flip: short → flat → long, both legs at bars[3].open.
    bars = _bars([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    signals = [
        Signal(bar_index=0, kind=SignalKind.ENTER_SHORT),
        Signal(bar_index=2, kind=SignalKind.ENTER_LONG),
        Signal(bar_index=2, kind=SignalKind.EXIT_SHORT),
        Signal(bar_index=4, kind=SignalKind.EXIT_LONG),
    ]
    assert signals_to_trades(bars, signals) == [
        Trade(
            entry_bar_index=1,
            exit_bar_index=3,
            entry_price=11.0,
            exit_price=13.0,
            kind="short",
        ),
        Trade(
            entry_bar_index=3,
            exit_bar_index=5,
            entry_price=13.0,
            exit_price=15.0,
            kind="long",
        ),
    ]


def test_short_signal_on_last_bar_is_dropped() -> None:
    bars = _bars([10.0, 11.0, 12.0, 13.0])
    signals = [Signal(bar_index=3, kind=SignalKind.ENTER_SHORT)]
    assert signals_to_trades(bars, signals) == []
