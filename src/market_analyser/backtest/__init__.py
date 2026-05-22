"""Backtest layer — engine, metrics, persistence-facing types.

Phase 1 of [Plan 0008](../../../docs/architecture/plans/0008-backtest-engine-v1.md)
lands the `BacktestResult` envelope from
[ADR-0018](../../../docs/architecture/adrs/0018-backtest-result-schema.md),
the four pure metric helpers, and the `bars_hash` data-identity field.
Phase 2 lands the pure `run()` orchestrator. Phases 3+ (`persist`, the
MCP tool, the UI view) live in dev / ui-builder territory and import
from this module.

`ENGINE_VERSION` lives in `_version.py` so both this package and
`engine.py` can import it without a circular dependency. Bump on any
output-affecting change to the four helpers (`_apply_costs`,
`_build_equity_curve`, `_calc_metrics`, `_buy_and_hold_return`) or the
`run()` orchestrator's composition order. The Plan 0008 phase-2 golden
fixture is the secondary defence: changes that alter outputs break the
golden test and force a deliberate fixture regen + version bump.
"""

from __future__ import annotations

from market_analyser.backtest._bars_hash import bars_hash
from market_analyser.backtest._version import ENGINE_VERSION
from market_analyser.backtest.adapter import signals_to_trades
from market_analyser.backtest.engine import StrategyContractError, run
from market_analyser.backtest.metrics import (
    UnknownTimeframeError,
    _apply_costs,
    _build_equity_curve,
    _buy_and_hold_return,
    _calc_metrics,
)
from market_analyser.backtest.result import (
    BacktestMetrics,
    BacktestResult,
    BacktestRunSummary,
    EquityPoint,
)
from market_analyser.backtest.types import Trade

__all__ = [
    "ENGINE_VERSION",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestRunSummary",
    "EquityPoint",
    "StrategyContractError",
    "Trade",
    "UnknownTimeframeError",
    "_apply_costs",
    "_build_equity_curve",
    "_buy_and_hold_return",
    "_calc_metrics",
    "bars_hash",
    "run",
    "signals_to_trades",
]
