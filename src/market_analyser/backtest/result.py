"""`BacktestResult` and its sub-models.

Per [ADR-0018](../../../docs/architecture/adrs/0018-backtest-result-schema.md):
one frozen Pydantic model that holds everything `run()` produces — spec,
identity, timing, output. Persistence splits the in-memory object across
`spec.json` + `result.json` + `equity_curve.csv`, and the SQLite
`backtest_runs` row is a searchable projection, but the canonical shape is
this object.

Determinism contract: two `run()` invocations with identical
`(strategy, bars, params, costs, initial_capital, timeframe)` produce two
`BacktestResult` objects whose `.model_dump(mode="json")` outputs are equal
after stripping `run_id`, `started_at`, `finished_at`. The
[Plan 0008](../../../docs/architecture/plans/0008-backtest-engine-v1.md)
phase-2 golden test pins this property.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.backtest.types import Trade


class EquityPoint(BaseModel):
    """One bar's mark-to-market equity. `ts` is UTC, bar close time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    equity: float


class BacktestMetrics(BaseModel):
    """The four-helper output cluster.

    `sharpe` and `win_rate` are NaN-safe — degenerate inputs (zero-std
    equity curve, zero closed trades) collapse to `0.0` rather than `NaN`
    so consumers never have to special-case the float.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_return: float
    sharpe: float
    max_drawdown: float
    max_drawdown_duration_bars: int
    win_rate: float
    trade_count: int
    buy_and_hold_return: float


class BacktestResult(BaseModel):
    """Everything `run()` produces, in one frozen object.

    Field groups: identity, spec, timing, output. Order is wire-stable —
    `model_dump(mode="json")` field order is part of the determinism
    contract, so adding fields means appending to the end of their group.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- Identity ---
    run_id: str
    engine_version: str

    # --- Spec ---
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    bars_hash: str
    params: dict[str, Any]
    costs: dict[str, float]
    initial_capital: float
    sizing: Literal["fixed_fraction"]

    # --- Timing ---
    started_at: datetime
    finished_at: datetime

    # --- Output ---
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics


class BacktestRunSummary(BaseModel):
    """List-view projection of a persisted backtest run — the SQLite row shape.

    Plan 0008 phase 3: `BacktestRunsRepository.list()` / `.get()` return
    these so the renderer's Recent Backtests view can render a sortable
    table without re-reading every artifact from disk. The full
    `BacktestResult` is still available via `GET /backtests/{run_id}`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    total_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    trade_count: int
    finished_at: datetime
    artifact_path: str
    engine_version: str


__all__ = ["BacktestMetrics", "BacktestResult", "BacktestRunSummary", "EquityPoint"]
