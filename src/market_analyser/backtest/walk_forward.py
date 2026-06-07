"""Rolling out-of-sample (walk-forward) evaluation.

[Plan 0020](../../../docs/architecture/plans/0020-backtest-metrics-walk-forward.md)
phase 2. `walk_forward()` partitions a bar series into `n_splits`
contiguous, non-overlapping test windows, runs the engine's `run()` on
each, and reports per-fold metrics plus an aggregate and a full-run
baseline (see `walk_forward_types`).

**Scope honesty (Plan 0020 / ADR-0024).** This is rolling *evaluation*,
not walk-forward *optimization*: our strategies are fixed-parameter (no
fitting step), so each window simply runs the same params and we report
whether the metrics hold up across unseen windows. There is no train
window — true walk-forward optimization (re-fit per fold) is a future plan
gated on a parameter-search facility.

**Anti-lookahead.** Folds are contiguous and non-overlapping, so fold *k*'s
first bar strictly follows fold *k-1*'s last bar, and each fold's `run()`
sees only its own window — no fold can read a future fold's bars. The
partition follows ``numpy.array_split`` semantics: with ``base, remainder =
divmod(n_bars, n_splits)``, the first `remainder` folds get ``base + 1``
bars and the rest get ``base``, so window sizes differ by at most one and
sum to `n_bars`.

Pure and deterministic: `WalkForwardResult` stores only deterministic
values (no `run_id` / timestamps), so identical inputs produce identical
results.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from types import ModuleType
from typing import Any

from market_analyser.backtest.engine import run
from market_analyser.backtest.walk_forward_types import WalkForwardFold, WalkForwardResult
from market_analyser.contracts.strategy import BaseParams
from market_analyser.data.types import Bar


class WalkForwardConfigError(ValueError):
    """Raised when `n_splits` is invalid for the given bar series.

    Typed (a `ValueError` subclass) so the phase-3 MCP tool can map it to a
    client error instead of a 500.
    """


def fold_bounds(n_bars: int, n_splits: int) -> list[tuple[int, int]]:
    """Return the `[start, end)` index bounds of each contiguous fold.

    `array_split` semantics: the first ``n_bars % n_splits`` folds get one
    extra bar. Bounds are contiguous (each `start` equals the previous
    `end`) and the window sizes sum to `n_bars`. The caller guarantees
    ``1 <= n_splits <= n_bars``, so every window has at least one bar.

    Public seam: the forecasting validation harness (`forecast/validation.py`,
    Plan 0036) reuses this exact partition so its train/test folds inherit the
    same contiguous, non-overlapping, anti-lookahead semantics as the strategy
    walk-forward — one source of truth for "how a bar series is split".
    """

    base, remainder = divmod(n_bars, n_splits)
    bounds: list[tuple[int, int]] = []
    start = 0
    for k in range(n_splits):
        size = base + (1 if k < remainder else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


def walk_forward(
    strategy_module: ModuleType,
    bars: Sequence[Bar],
    params: dict[str, Any] | BaseParams,
    *,
    timeframe: str,
    n_splits: int,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    initial_capital: float = 10_000.0,
) -> WalkForwardResult:
    """Run a strategy across `n_splits` rolling out-of-sample folds.

    Raises `ValueError` on empty `bars` and `WalkForwardConfigError` when
    `n_splits < 1` or `n_splits` exceeds the bar count (which would force a
    zero-bar fold). A window that is non-empty but too short for the
    strategy to warm up / trade is *not* an error: its `run()` yields a
    degenerate-but-valid `BacktestResult` (zero closed trades), per the
    metric helpers' NaN-safe / `None` conventions.
    """

    if not bars:
        raise ValueError("bars must not be empty")
    if n_splits < 1:
        raise WalkForwardConfigError(f"n_splits must be >= 1, got {n_splits}")
    if n_splits > len(bars):
        raise WalkForwardConfigError(
            f"n_splits ({n_splits}) exceeds bar count ({len(bars)}); "
            f"each fold needs at least one bar"
        )

    # Full-series baseline (the in-sample-equivalent for the degradation check).
    full_run = run(
        strategy_module,
        bars,
        params,
        timeframe=timeframe,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        initial_capital=initial_capital,
    )

    folds: list[WalkForwardFold] = []
    for fold_index, (start, end) in enumerate(fold_bounds(len(bars), n_splits)):
        window = bars[start:end]
        result = run(
            strategy_module,
            window,
            params,
            timeframe=timeframe,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                range_start=window[0].event_ts,
                range_end=window[-1].event_ts,
                metrics=result.metrics,
                trade_count=len(result.trades),
            )
        )

    total_returns = [fold.metrics.total_return for fold in folds]
    sharpes = [fold.metrics.sharpe for fold in folds]
    multi_fold = len(folds) >= 2
    aggregate: dict[str, float | None] = {
        "total_return_mean": statistics.fmean(total_returns),
        "total_return_std": statistics.stdev(total_returns) if multi_fold else None,
        "sharpe_mean": statistics.fmean(sharpes),
        "sharpe_std": statistics.stdev(sharpes) if multi_fold else None,
    }

    return WalkForwardResult(
        strategy_id=strategy_module.META.id,
        symbol=bars[0].symbol,
        timeframe=timeframe,
        n_splits=n_splits,
        folds=folds,
        aggregate=aggregate,
        full_run_baseline=full_run.metrics,
    )


__all__ = ["WalkForwardConfigError", "fold_bounds", "walk_forward"]
