"""Pure backtest orchestrator.

Per Plan 0008 phase 2: `run()` composes the strategy contract (Plan 0002),
the four metric helpers (phase 1), and the `BacktestResult` envelope
(ADR-0018) into one frozen object. No I/O, no DB, no event bus, no random
state beyond UUID4 + wall-clock timestamps (the three fields the
determinism contract permits to vary).

Re-running with identical
``(strategy_module, bars, params, timeframe, commission_bps, slippage_bps,
initial_capital)`` produces two `BacktestResult` objects whose
`.model_dump(mode="json", exclude={"run_id", "started_at", "finished_at"})`
dicts are equal element-by-element. The phase-2 golden test pins this
property against a committed JSON fixture so cross-process equivalence is
also asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

from market_analyser.backtest._bars_hash import bars_hash as _bars_hash
from market_analyser.backtest._version import ENGINE_VERSION
from market_analyser.backtest.adapter import signals_to_trades
from market_analyser.backtest.metrics import (
    _apply_costs,
    _build_equity_curve,
    _buy_and_hold_return,
    _calc_metrics,
)
from market_analyser.backtest.result import BacktestResult
from market_analyser.contracts.strategy import BaseParams, StrategyMeta
from market_analyser.data.types import Bar


class StrategyContractError(TypeError):
    """A strategy module is missing `META`, `Params`, or `generate_signals`."""


def _validate_strategy_module(strategy_module: ModuleType) -> None:
    """Raise `StrategyContractError` if the module is not contract-shaped."""

    meta = getattr(strategy_module, "META", None)
    if not isinstance(meta, StrategyMeta):
        raise StrategyContractError(
            f"strategy module {strategy_module.__name__!r} is missing 'META' "
            f"(or it is not a StrategyMeta instance)"
        )
    params_cls = getattr(strategy_module, "Params", None)
    if not (isinstance(params_cls, type) and issubclass(params_cls, BaseParams)):
        raise StrategyContractError(
            f"strategy module {strategy_module.__name__!r} is missing 'Params' "
            f"(or it is not a BaseParams subclass)"
        )
    generate_signals_fn = getattr(strategy_module, "generate_signals", None)
    if not callable(generate_signals_fn):
        raise StrategyContractError(
            f"strategy module {strategy_module.__name__!r} is missing "
            f"'generate_signals' (or it is not callable)"
        )


def run(
    strategy_module: ModuleType,
    bars: Sequence[Bar],
    params: dict[str, Any] | BaseParams,
    *,
    timeframe: str,
    commission_bps: float = 0.0,
    slippage_bps: float = 0.0,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Run a single backtest and return a `BacktestResult`.

    `params` may be a raw dict (validated against
    `strategy_module.Params`) or an already-validated `BaseParams`
    instance. `timeframe` is required because Sharpe annualization needs
    it and `Bar.timeframe` is per-bar (not part of the engine's
    contract).
    """

    _validate_strategy_module(strategy_module)

    if not bars:
        raise ValueError("bars must not be empty")

    params_cls = strategy_module.Params
    params_instance: BaseParams
    if isinstance(params, BaseParams):
        params_instance = params
    else:
        params_instance = params_cls(**params)

    started_at = datetime.now(UTC)

    signals = strategy_module.generate_signals(bars, params_instance)
    raw_trades = signals_to_trades(bars, signals)
    adjusted_trades = _apply_costs(
        raw_trades,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
    )
    equity_curve = _build_equity_curve(bars, adjusted_trades, initial_capital)
    buy_and_hold = _buy_and_hold_return(bars, initial_capital)
    metrics = _calc_metrics(
        adjusted_trades,
        equity_curve,
        initial_capital,
        timeframe,
        buy_and_hold_return=buy_and_hold,
    )

    finished_at = datetime.now(UTC)

    return BacktestResult(
        run_id=uuid.uuid4().hex,
        engine_version=ENGINE_VERSION,
        strategy_id=strategy_module.META.id,
        strategy_version=strategy_module.META.version,
        symbol=bars[0].symbol,
        timeframe=timeframe,
        range_start=bars[0].event_ts,
        range_end=bars[-1].event_ts,
        bars_hash=_bars_hash(bars),
        params=params_instance.model_dump(mode="json"),
        costs={"commission_bps": commission_bps, "slippage_bps": slippage_bps},
        initial_capital=initial_capital,
        sizing="fixed_fraction",
        started_at=started_at,
        finished_at=finished_at,
        trades=list(adjusted_trades),
        equity_curve=equity_curve,
        metrics=metrics,
    )


__all__ = ["StrategyContractError", "run"]
