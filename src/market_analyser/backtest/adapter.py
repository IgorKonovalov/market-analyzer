"""Signal → Trade adapter — the bridge between strategy output and the engine.

Execution-timing convention (from `Signal`'s docstring and the no-lookahead
rule): a signal at `bar_index = i` is interpreted as "decision at the close
of bar `i`, executed at the OPEN of bar `i + 1`". This adapter enforces the
offset; strategies that emit signals whose `bar_index + 1` is past the end of
the series have those signals silently dropped, since there is no future open
to execute against.

State machine — flat / long / short, single direction at a time per
[ADR-0050](../../../docs/architecture/adrs/0050-short-selling-strategy-backtest.md),
over the ordered signal stream:

- flat + `ENTER_LONG` / `ENTER_SHORT`: open a `Trade` at `bars[i+1].open`,
  mark the matching direction.
- long + `EXIT_LONG` / short + `EXIT_SHORT`: close the open trade at
  `bars[i+1].open`, return to flat.
- any `EXIT_*` while flat or in the other direction: ignore (no position of
  that direction to close).
- any `ENTER_*` while already in a position: ignore (no pyramiding, no
  simultaneous long+short).
- any signal with `bar_index >= len(bars) - 1`: ignore (no executable open).
- end of stream with an open trade: emit it with `exit_*` set to `None`.

Ordering: signals are processed in ascending `bar_index` order, and **within
one bar exits are processed before entries** (stable for remaining ties, so a
deterministic strategy output stays deterministic here). That pins ADR-0050's
same-bar rule: a long-exit plus a short-entry referencing the same bar executes
exit-first-then-enter — flat between, both at the same next open — regardless
of the order the strategy emitted them in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from market_analyser.backtest.types import Trade
from market_analyser.contracts import Bar, Signal, SignalKind

_Direction = Literal["long", "short"]

_ENTER_KIND_TO_DIRECTION: dict[SignalKind, _Direction] = {
    SignalKind.ENTER_LONG: "long",
    SignalKind.ENTER_SHORT: "short",
}
_EXIT_KIND_TO_DIRECTION: dict[SignalKind, _Direction] = {
    SignalKind.EXIT_LONG: "long",
    SignalKind.EXIT_SHORT: "short",
}


def _ordering_key(signal: Signal) -> tuple[int, int]:
    """Sort key: ascending bar, exits before entries within a bar."""

    is_entry = 1 if signal.kind in _ENTER_KIND_TO_DIRECTION else 0
    return (signal.bar_index, is_entry)


def signals_to_trades(bars: Sequence[Bar], signals: Sequence[Signal]) -> list[Trade]:
    """Convert a `Signal` stream into a list of long/short `Trade`s.

    Pure function: no I/O, no module-level state, no costs, no sizing. Given
    the same `bars` and `signals`, returns the same list of `Trade`s.

    Signals are stable-sorted by `(bar_index, exit-before-enter)` before the
    state machine runs — see the module docstring for why the same-bar
    ordering is load-bearing (ADR-0050).
    """

    trades: list[Trade] = []
    last_executable_index = len(bars) - 2  # signal at i executes at bars[i+1]
    open_direction: _Direction | None = None
    open_entry_index: int | None = None
    open_entry_price: float | None = None

    for signal in sorted(signals, key=_ordering_key):
        if signal.bar_index < 0 or signal.bar_index > last_executable_index:
            continue
        execution_index = signal.bar_index + 1
        execution_price = bars[execution_index].open
        if signal.kind in _ENTER_KIND_TO_DIRECTION:
            if open_direction is not None:
                continue  # no pyramiding, no simultaneous long+short
            open_direction = _ENTER_KIND_TO_DIRECTION[signal.kind]
            open_entry_index = execution_index
            open_entry_price = execution_price
        else:
            if open_direction is None or open_direction != _EXIT_KIND_TO_DIRECTION[signal.kind]:
                continue  # flat, or holding the other direction
            assert open_entry_index is not None
            assert open_entry_price is not None
            trades.append(
                Trade(
                    entry_bar_index=open_entry_index,
                    exit_bar_index=execution_index,
                    entry_price=open_entry_price,
                    exit_price=execution_price,
                    kind=open_direction,
                )
            )
            open_direction = None
            open_entry_index = None
            open_entry_price = None

    if open_direction is not None:
        assert open_entry_index is not None
        assert open_entry_price is not None
        trades.append(
            Trade(
                entry_bar_index=open_entry_index,
                exit_bar_index=None,
                entry_price=open_entry_price,
                exit_price=None,
                kind=open_direction,
            )
        )

    return trades


__all__ = ["signals_to_trades"]
