"""Backtest-layer data shapes.

Phase 3 of [Plan 0002](../../../docs/architecture/plans/0002-strategy-interface.md)
ships only the `Trade` model — the smallest output type the
`signals_to_trades` adapter needs. The full backtest engine, its costs, its
metrics, and its `BacktestResult` envelope all move to a dedicated follow-up
plan (see the 2026-05-19 reframe note in Plan 0002).

`Trade` describes one round-trip position: an entry that may or may not have
yet closed. `kind` carries the direction (`"long"` or `"short"`) per
[ADR-0050](../../../docs/architecture/adrs/0050-short-selling-strategy-backtest.md):
a short's realized P&L is `entry - exit` (the inverse of a long), charged the
same transaction cost a long pays. Positions are single-direction at a time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.contracts.strategy import SignalKind


class Trade(BaseModel):
    """A round-trip trade produced by `signals_to_trades`.

    `entry_bar_index` is the bar at whose OPEN the position was opened — i.e.
    one bar after the `ENTER_LONG` / `ENTER_SHORT` signal's `bar_index`.
    `exit_bar_index` / `exit_price` are `None` when the position is still open
    at the end of the bar series (dangling entry). `kind` is the position
    direction: a long profits when `exit > entry`, a short when `exit < entry`
    (P&L = `entry - exit`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_bar_index: int
    exit_bar_index: int | None
    entry_price: float
    exit_price: float | None
    kind: Literal["long", "short"]


class EvaluatedSignal(BaseModel):
    """The most-recent signal in a live evaluation (Plan 0026).

    Unlike a backtest `Trade`, this is not executed against a future open — it
    is the strategy's decision *as emitted*. `bar_index` and `event_ts` index
    into the CLOSED-bar series (any still-forming latest bar is excluded before
    indexing), so they are stable across an intrabar re-call until a new bar
    closes. `kind` carries the strategy contract's `SignalKind` directly (it
    serialises to `"enter_long"` / `"exit_long"` / `"enter_short"` /
    `"exit_short"`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SignalKind
    bar_index: int
    event_ts: datetime
    # `= None` default (not just `| None`) so the SSE bus's `exclude_none` dump
    # omits it when absent AND `model_json_schema()` marks it optional — keeping
    # the wire shape, the schema, and the renderer's TS mirror consistent (the
    # same pattern the chart payloads' optional fields use).
    reason: str | None = None


class SignalEvaluation(BaseModel):
    """The current signal state of one strategy on one symbol (Plan 0026).

    A *condition report*, never a recommendation: it states what the strategy's
    signals are (implied position, most-recent signal, freshness), not what to
    do about them. Produced by `backtest.live_signal.evaluate_signals` over a
    closed-bar series; the wall-clock dependence is confined to deciding which
    bars count as closed (`latest_bar_excluded_as_forming`), so the financially
    meaningful computation stays pure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    symbol: str
    timeframe: str
    evaluated_through_ts: datetime  # event_ts of the last CLOSED bar fed to the strategy
    closed_bar_count: int  # bars actually passed to generate_signals
    latest_bar_excluded_as_forming: bool  # True iff a still-forming latest bar was dropped
    current_position: Literal["flat", "long", "short"]  # implied by folding the signal stream
    # `= None` defaults so the SSE bus's `exclude_none` dump omits these when
    # absent and the schema marks them optional — see EvaluatedSignal.reason.
    last_signal: EvaluatedSignal | None = None  # most recent signal, or None if none fired
    bars_since_last_signal: int | None = None  # 0 == fired on the last closed bar; None if none
    fresh_signal: bool  # last_signal fired on the last closed bar


__all__ = ["EvaluatedSignal", "SignalEvaluation", "Trade"]
