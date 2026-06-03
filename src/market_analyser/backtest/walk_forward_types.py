"""Result shapes for rolling out-of-sample (walk-forward) evaluation.

[Plan 0020](../../../docs/architecture/plans/0020-backtest-metrics-walk-forward.md)
phase 2. A `WalkForwardResult` holds one `WalkForwardFold` per
non-overlapping contiguous test window, plus an aggregate (mean/std of the
headline metrics across folds) and a `full_run_baseline` — the same
strategy's metrics over the *entire* bar series, the in-sample-equivalent
a consumer compares the folds against to judge stability.

Both models are frozen + ``extra="forbid"`` and store only deterministic
values (the extended `BacktestMetrics`, fold ranges, trade counts) — no
`run_id` / timestamps — so two `walk_forward()` calls on identical inputs
produce byte-identical dumps. The per-fold `BacktestResult` provenance the
engine generates is intentionally dropped at this layer.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.backtest.result import BacktestMetrics


class WalkForwardFold(BaseModel):
    """One out-of-sample test window's result.

    `range_start` / `range_end` are the UTC close times of the window's
    first and last bar. `metrics` is the extended `BacktestMetrics`
    (ADR-0024) computed on that window alone. `trade_count` is the total
    number of trades the strategy produced in the window — including any
    entry still open at the window's end — so it can exceed
    `metrics.trade_count`, which counts only *closed* trades.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold_index: int
    range_start: datetime
    range_end: datetime
    metrics: BacktestMetrics
    trade_count: int


class WalkForwardResult(BaseModel):
    """Per-fold + aggregate report for one strategy over one bar series.

    `aggregate` carries the mean and sample-stdev (ddof=1) of
    `total_return` and `sharpe` across folds under the keys
    `total_return_mean`, `total_return_std`, `sharpe_mean`, `sharpe_std`.
    The two `_std` values are `None` when there is a single fold (stdev is
    undefined for fewer than two points). `full_run_baseline` is the same
    strategy run over the whole series, for the consumer's degradation
    check.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    symbol: str
    timeframe: str
    n_splits: int
    folds: list[WalkForwardFold]
    aggregate: dict[str, float | None]
    full_run_baseline: BacktestMetrics


__all__ = ["WalkForwardFold", "WalkForwardResult"]
