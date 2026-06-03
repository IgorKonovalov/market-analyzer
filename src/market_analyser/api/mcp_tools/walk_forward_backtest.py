"""`walk_forward_backtest` MCP tool — Plan 0020 phase 3.

Surfaces the phase-2 `walk_forward()` engine routine over MCP: validate ->
resolve strategy via `discover()` -> fetch bars via the provider -> run
`walk_forward()` (offloaded with `asyncio.to_thread`) -> return the
`WalkForwardResult` (per-fold metrics + aggregate + full-run baseline).

**Scope honesty (Plan 0020 / ADR-0024).** This is rolling *out-of-sample
evaluation*, not walk-forward *optimization*: the strategy runs at fixed
params on each contiguous, non-overlapping test window and we report
whether its metrics hold up across unseen windows. There is no per-fold
re-fit — true walk-forward optimization needs a parameter-search facility
we don't have yet.

Nothing is persisted — the fold report is the artifact. Errors surface as
MCP tool errors (not HTTP 500): an unknown `strategy_id` raises
`ValueError`, and an invalid `n_splits` raises `WalkForwardConfigError`
(a `ValueError` subclass) from the engine.

The body is factored out as `_walk_forward_backtest_response` so it is
unit-testable on a single event loop without a live MCP server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
)
from market_analyser.backtest.walk_forward import walk_forward
from market_analyser.backtest.walk_forward_types import WalkForwardResult
from market_analyser.contracts.strategy import discover
from market_analyser.data.provider import MarketDataProvider

WALK_FORWARD_BACKTEST_DESCRIPTION = (
    "Evaluate one strategy across n_splits rolling out-of-sample folds and return "
    "per-fold metrics plus an aggregate (mean/std of total_return and sharpe) and a "
    "full-run baseline. The bar series is partitioned into contiguous, "
    "non-overlapping test windows — fold k's bars strictly follow fold k-1's, so "
    "there is no lookahead. This is rolling out-of-sample EVALUATION, not "
    "walk-forward optimization: fixed params per fold, no re-fitting. params "
    "defaults to the strategy's own defaults. Not persisted. n_splits must be "
    ">=1 and <= the number of bars. Supported timeframes: 1d, 1h, 1m."
)


async def _walk_forward_backtest_response(
    *,
    provider: MarketDataProvider,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    n_splits: int,
    params: dict[str, Any] | None,
    commission_bps: float,
    slippage_bps: float,
    initial_capital: float,
) -> WalkForwardResult:
    """Body of `walk_forward_backtest`: validate, resolve, fetch, fold."""

    _require_non_empty_symbol(symbol)
    _require_ordered_range(range_start, range_end)
    if commission_bps < 0:
        raise ValueError("commission_bps must be >= 0")
    if slippage_bps < 0:
        raise ValueError("slippage_bps must be >= 0")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")

    strategies = discover()
    if strategy_id not in strategies:
        raise ValueError(
            f"unknown strategy_id {strategy_id!r}; known: {sorted(strategies)}",
        )
    strategy_module = strategies[strategy_id]

    bars = list(
        await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, range_start, range_end)
    )
    if not bars:
        raise ValueError(
            f"no cached bars for {symbol} {timeframe} over the requested window; "
            "backfill via get_ohlcv first",
        )

    # walk_forward raises WalkForwardConfigError (a ValueError) for a bad
    # n_splits; it surfaces as an MCP tool error, not a 500.
    return await asyncio.to_thread(
        walk_forward,
        strategy_module,
        bars,
        params if params is not None else {},
        timeframe=timeframe,
        n_splits=n_splits,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        initial_capital=initial_capital,
    )


def register_walk_forward_backtest(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind `walk_forward_backtest` to `server`. The provider is captured by closure
    so the tool body keeps the parameter list FastMCP introspects for the schema."""

    @server.tool(description=WALK_FORWARD_BACKTEST_DESCRIPTION)
    async def walk_forward_backtest(
        strategy_id: str,
        symbol: str,
        timeframe: Literal["1d", "1h", "1m"],
        range_start: datetime,
        range_end: datetime,
        n_splits: int = 4,
        params: dict[str, Any] | None = None,
        commission_bps: float = 0.0,
        slippage_bps: float = 0.0,
        initial_capital: float = 10_000.0,
    ) -> WalkForwardResult:
        return await _walk_forward_backtest_response(
            provider=provider,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            n_splits=n_splits,
            params=params,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )


__all__ = [
    "WALK_FORWARD_BACKTEST_DESCRIPTION",
    "_walk_forward_backtest_response",
    "register_walk_forward_backtest",
]
