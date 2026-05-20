"""Backtest-layer data shapes.

Phase 3 of [Plan 0002](../../../docs/architecture/plans/0002-strategy-interface.md)
ships only the `Trade` model — the smallest output type the
`signals_to_trades` adapter needs. The full backtest engine, its costs, its
metrics, and its `BacktestResult` envelope all move to a dedicated follow-up
plan (see the 2026-05-19 reframe note in Plan 0002).

`Trade` describes one round-trip position: an entry that may or may not have
yet closed. Short trades are reserved (`kind` is `Literal["long"]` for now) and
will land alongside `ENTER_SHORT`/`EXIT_SHORT` in their own plan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Trade(BaseModel):
    """A long-only round-trip trade produced by `signals_to_trades`.

    `entry_bar_index` is the bar at whose OPEN the position was opened — i.e.
    one bar after the `ENTER_LONG` signal's `bar_index`. `exit_bar_index` /
    `exit_price` are `None` when the position is still open at the end of the
    bar series (dangling entry).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_bar_index: int
    exit_bar_index: int | None
    entry_price: float
    exit_price: float | None
    kind: Literal["long"]


__all__ = ["Trade"]
