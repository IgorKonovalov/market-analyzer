"""`compare_strategies` MCP tool — Plan 0020 phase 3.

Runs every reference strategy (discovered via `discover()`) on one
symbol/timeframe/window at its default params and returns a leaderboard
ranked by a chosen metric. The flow mirrors `run_backtest` (validate ->
discover -> fetch bars via the provider -> `engine.run()`), but:

- it runs *all* discovered strategies over the *same* bars, each at its
  own defaults (cross-strategy comparison only makes sense at a common,
  parameter-free baseline);
- the synchronous engine calls are offloaded with `asyncio.to_thread` so
  they never stall the event loop; and
- nothing is persisted — the leaderboard *is* the artifact (Plan 0020 v1
  decision). No `runs/` row, no SQLite write, no event published.

Ranking is deterministic: descending by the chosen metric, with rows whose
metric is `None` (e.g. Calmar on a never-dipped curve) sorted last, and
`strategy_id` ascending as the stable tie-break throughout. Two identical
calls therefore produce byte-identical row order.

The body is factored out as `_compare_strategies_response` so the
fetch/rank paths are unit-testable on a single event loop without a live
MCP server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.api.mcp_tools._shared.backtest_timeframe import BACKTEST_TIMEFRAME
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
)
from market_analyser.backtest.engine import run as engine_run
from market_analyser.backtest.result import BacktestMetrics
from market_analyser.contracts.strategy import discover
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label

RankBy = Literal["sharpe", "calmar", "total_return", "sortino"]

COMPARE_STRATEGIES_DESCRIPTION = (
    "Run every reference strategy on one symbol/timeframe/window at its default "
    "parameters and return a leaderboard ranked by a chosen metric. rank_by is one "
    "of sharpe|calmar|total_return|sortino (default sharpe), sorted best-first; "
    "rows whose metric is undefined (e.g. Calmar when the curve never dipped, "
    "reported as null) sort last, ties broken by strategy_id. Each row carries the "
    "strategy id/version and its full metric set (the extended ADR-0024 metrics "
    "included). Costs default to 0 bps / $10k capital. Comparison runs are NOT "
    "persisted — the leaderboard is the result. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


class StrategyLeaderboardRow(BaseModel):
    """One strategy's line in the leaderboard: its identity plus full metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    strategy_version: str
    metrics: BacktestMetrics


class CompareStrategiesResponse(BaseModel):
    """The ranked leaderboard. `rows` is ordered best-first by `rank_by`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    rank_by: RankBy
    rows: list[StrategyLeaderboardRow]


def _rank_value(metrics: BacktestMetrics, rank_by: RankBy) -> float | None:
    """The single metric a row is ranked on (explicit, so mypy stays strict)."""

    if rank_by == "sharpe":
        return metrics.sharpe
    if rank_by == "calmar":
        return metrics.calmar
    if rank_by == "total_return":
        return metrics.total_return
    return metrics.sortino  # "sortino"


def _rank_key(row: StrategyLeaderboardRow, rank_by: RankBy) -> tuple[bool, float, str]:
    """Sort key: None last, then metric descending, then strategy_id ascending.

    Tuples sort ascending, so: `value is None` (False < True) puts defined
    metrics first; `-value` ascending puts larger metrics first; `strategy_id`
    breaks ties deterministically.
    """

    value = _rank_value(row.metrics, rank_by)
    return (value is None, -(value if value is not None else 0.0), row.strategy_id)


async def _compare_strategies_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    rank_by: RankBy,
    commission_bps: float,
    slippage_bps: float,
    initial_capital: float,
) -> CompareStrategiesResponse:
    """Body of `compare_strategies`: validate, fetch once, run all, rank."""

    _require_non_empty_symbol(symbol)
    _require_ordered_range(range_start, range_end)
    if commission_bps < 0:
        raise ValueError("commission_bps must be >= 0")
    if slippage_bps < 0:
        raise ValueError("slippage_bps must be >= 0")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")

    bars = list(
        await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, range_start, range_end)
    )
    if not bars:
        raise ValueError(
            f"no cached bars for {symbol} {timeframe} over the requested window; "
            "backfill via get_ohlcv first",
        )

    strategies = discover()
    rows: list[StrategyLeaderboardRow] = []
    for strategy_id in sorted(strategies):
        strategy_module = strategies[strategy_id]
        result = await asyncio.to_thread(
            engine_run,
            strategy_module,
            bars,
            {},  # every strategy runs at its own defaults
            timeframe=timeframe,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )
        rows.append(
            StrategyLeaderboardRow(
                strategy_id=strategy_id,
                strategy_version=strategy_module.META.version,
                metrics=result.metrics,
            )
        )

    ranked = sorted(rows, key=lambda row: _rank_key(row, rank_by))
    return CompareStrategiesResponse(
        symbol=symbol,
        timeframe=timeframe,
        rank_by=rank_by,
        rows=ranked,
    )


def register_compare_strategies(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind `compare_strategies` to `server`. The provider is captured by closure
    so the tool body keeps the parameter list FastMCP introspects for the schema."""

    @server.tool(description=COMPARE_STRATEGIES_DESCRIPTION)
    async def compare_strategies(
        symbol: str,
        timeframe: BACKTEST_TIMEFRAME,
        range_start: datetime,
        range_end: datetime,
        rank_by: RankBy = "sharpe",
        commission_bps: float = 0.0,
        slippage_bps: float = 0.0,
        initial_capital: float = 10_000.0,
    ) -> CompareStrategiesResponse:
        return await _compare_strategies_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            rank_by=rank_by,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )


__all__ = [
    "COMPARE_STRATEGIES_DESCRIPTION",
    "CompareStrategiesResponse",
    "StrategyLeaderboardRow",
    "_compare_strategies_response",
    "register_compare_strategies",
]
