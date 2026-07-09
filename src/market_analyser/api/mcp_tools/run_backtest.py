"""`run_backtest` MCP tool — Plan 0008 phase 4.

The agent-facing tool that composes engine + persistence + event bus. The
flow is straight-line:

    validate inputs (FastMCP/Pydantic at the boundary)
        -> resolve strategy_module = discover()[strategy_id]
        -> fetch bars via MarketDataProvider
        -> engine.run(strategy_module, bars, params, **costs)
        -> persist(result, runs_dir, repository)
        -> bus.publish("run.completed v1", {...})
        -> return {run_id, status, summary}

The `run.completed v1` envelope is published exactly once on success and
not at all on failure (any raise above the publish call leaves the bus
untouched). The renderer's `useEventStream` subscribes to this envelope
and fetches the full BacktestResult via the renderer-bearer-gated
`GET /backtests/{run_id}` route (Plan 0008 phase 3); the MCP reply
itself only carries the 5-field summary so the agent's conversation
window stays compact.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._shared.backtest_timeframe import BACKTEST_TIMEFRAME
from market_analyser.backtest.engine import run as engine_run
from market_analyser.backtest.persistence import persist
from market_analyser.contracts.strategy import discover
from market_analyser.data.provider import MarketDataProvider
from market_analyser.events import EventBus, RunCompletedPayloadV1
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)


def register_run_backtest(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    repository: BacktestRunsRepository,
    event_bus: EventBus,
    runs_dir: Path,
) -> None:
    """Bind the `run_backtest` tool to `server`. Dependencies are captured by
    closure so the tool body keeps its declared parameter list (FastMCP
    introspects that list to build the input schema)."""

    @server.tool(
        description=(
            "Run a backtest for a single strategy/symbol/timeframe window. "
            "Composes the strategy contract (Plan 0002), the engine "
            "(Plan 0008), and the persistence layer. The full BacktestResult "
            "is written to runs/<run_id>/ on disk and indexed in SQLite; this "
            "call returns a compact summary (5 metrics) and the run_id you can "
            "use to fetch the full result. Publishes a `run.completed v1` event "
            "to the SSE bus so the renderer's BacktestView opens automatically."
        ),
    )
    def run_backtest(
        strategy_id: str,
        symbol: str,
        timeframe: BACKTEST_TIMEFRAME,
        range_start: datetime,
        range_end: datetime,
        params: dict[str, Any],
        commission_bps: float = 0.0,
        slippage_bps: float = 0.0,
        initial_capital: float = 10_000.0,
    ) -> dict[str, Any]:
        if commission_bps < 0:
            raise ValueError("commission_bps must be >= 0")
        if slippage_bps < 0:
            raise ValueError("slippage_bps must be >= 0")
        if initial_capital <= 0:
            raise ValueError("initial_capital must be > 0")
        if range_end < range_start:
            raise ValueError(
                f"range_end {range_end.isoformat()} must be >= "
                f"range_start {range_start.isoformat()}",
            )

        strategies = discover()
        if strategy_id not in strategies:
            raise ValueError(
                f"unknown strategy_id {strategy_id!r}; known: {sorted(strategies)}",
            )
        strategy_module = strategies[strategy_id]

        bars = list(
            provider.get_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                start=range_start,
                end=range_end,
            ),
        )

        result = engine_run(
            strategy_module,
            bars,
            params,
            timeframe=timeframe,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )

        persist(result, runs_dir, repository)

        # Publish AFTER persist succeeds — Plan 0008 phase 4 done-when
        # §193: failure paths above this line publish zero envelopes.
        event_bus.publish(
            "run.completed",
            RunCompletedPayloadV1(
                kind="backtest",
                run_id=result.run_id,
                artifact_path=result.run_id,
            ),
        )

        return {
            "run_id": result.run_id,
            "status": "complete",
            "summary": {
                "total_return": result.metrics.total_return,
                "sharpe": result.metrics.sharpe,
                "max_drawdown": result.metrics.max_drawdown,
                "win_rate": result.metrics.win_rate,
                "trade_count": result.metrics.trade_count,
            },
        }


__all__ = ["register_run_backtest"]
