"""Signal → Trade adapter — the bridge between strategy output and the engine.

This is the only piece of the backtest layer that Plan 0002 ships. The full
engine (costs, equity, metrics, `BacktestResult`) lands in a dedicated
follow-up plan; until then, the trade list is the entire deliverable a
strategy produces.

Execution-timing convention (from `Signal`'s docstring and the no-lookahead
rule): a signal at `bar_index = i` is interpreted as "decision at the close
of bar `i`, executed at the OPEN of bar `i + 1`". This adapter enforces the
offset; strategies that emit signals whose `bar_index + 1` is past the end of
the series have those signals silently dropped, since there is no future open
to execute against.

State machine, in order over the (sorted) signal stream:

- flat + `ENTER_LONG`: open a `Trade` at `bars[i+1].open`, mark long.
- long + `EXIT_LONG`: close the open trade at `bars[i+1].open`, return to flat.
- flat + `EXIT_LONG`: ignore (no position to close).
- long + `ENTER_LONG`: ignore (no pyramiding in v1).
- any signal with `bar_index >= len(bars) - 1`: ignore (no executable open).
- end of stream with an open trade: emit it with `exit_*` set to `None`.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.backtest.types import Trade
from market_analyser.contracts import Bar, Signal, SignalKind


def signals_to_trades(bars: Sequence[Bar], signals: Sequence[Signal]) -> list[Trade]:
    """Convert a `Signal` stream into a list of long-only `Trade`s.

    Pure function: no I/O, no module-level state, no costs, no sizing. Given
    the same `bars` and `signals`, returns the same list of `Trade`s.

    Signals are processed in input order; callers that care about chronology
    must sort by `bar_index` themselves (the adapter does not reorder, so
    deterministic strategy output remains deterministic here).
    """

    trades: list[Trade] = []
    last_executable_index = len(bars) - 2  # signal at i executes at bars[i+1]
    open_entry_index: int | None = None
    open_entry_price: float | None = None

    for signal in signals:
        if signal.bar_index < 0 or signal.bar_index > last_executable_index:
            continue
        execution_index = signal.bar_index + 1
        execution_price = bars[execution_index].open
        if signal.kind is SignalKind.ENTER_LONG:
            if open_entry_index is not None:
                continue
            open_entry_index = execution_index
            open_entry_price = execution_price
        elif signal.kind is SignalKind.EXIT_LONG:
            if open_entry_index is None:
                continue
            assert open_entry_price is not None
            trades.append(
                Trade(
                    entry_bar_index=open_entry_index,
                    exit_bar_index=execution_index,
                    entry_price=open_entry_price,
                    exit_price=execution_price,
                    kind="long",
                )
            )
            open_entry_index = None
            open_entry_price = None

    if open_entry_index is not None:
        assert open_entry_price is not None
        trades.append(
            Trade(
                entry_bar_index=open_entry_index,
                exit_bar_index=None,
                entry_price=open_entry_price,
                exit_price=None,
                kind="long",
            )
        )

    return trades


__all__ = ["signals_to_trades"]
