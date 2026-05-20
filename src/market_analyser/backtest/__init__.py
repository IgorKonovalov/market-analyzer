"""Backtest layer — Plan 0002 phase 3 ships only the signals→trades adapter.

Engine, costs, metrics, and the `BacktestResult` envelope are deferred to a
dedicated follow-up plan. Until then, `signals_to_trades` and `Trade` are the
entire public surface here; downstream consumers should import from this
module rather than the internal submodules.
"""

from __future__ import annotations

from market_analyser.backtest.adapter import signals_to_trades
from market_analyser.backtest.types import Trade

__all__ = ["Trade", "signals_to_trades"]
