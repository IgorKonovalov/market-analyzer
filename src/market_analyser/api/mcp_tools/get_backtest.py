"""`get_backtest` MCP tool — Plan 0050 phase 3 (finding #1, ADR-0046).

The agent-facing path to a persisted backtest's full detail. `run_backtest`
returns only a 5-metric summary + `run_id` (by design, so the conversation window
stays compact); the full `BacktestResult` lives on disk and the renderer fetches
it via the bearer-gated `GET /backtests/{run_id}` REST route — a route the MCP
tenant deliberately cannot reach (ADR-0017 cross-tenant isolation). Before this
tool, breaking trades down over MCP meant reading `runs/<run_id>/result.json`
off the filesystem directly — unsupported and unavailable to a sandboxed agent.

`get_backtest` reads the same on-disk artifact the REST route does (via
`BacktestRunsRepository.get` + `read_result`) and returns metrics + spec + the
**full trade list** inline. The equity curve — one point per bar, the same
unbounded series `get_ohlcv` pages — is omitted unless `include_equity=true`, and
when included it obeys the ADR-0046 page contract (`too_large` + offset/limit).
An unknown `run_id` raises a typed not-found error (an MCP error result, never a
500), mirroring the REST route's 404.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.backtest.persistence import read_result
from market_analyser.backtest.result import BacktestMetrics, EquityPoint
from market_analyser.backtest.types import Trade
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)

# Maximum equity points returned inline in one page (ADR-0046), mirroring
# get_ohlcv's MAX_OHLCV_BARS. An equity point (`{ts, equity}`) is smaller than a
# bar, so a larger page fits the same token budget; kept centralized + pinned by
# a test so a harness change is a one-line retune.
MAX_EQUITY_POINTS = 1_000

GET_BACKTEST_DESCRIPTION = (
    "Fetch a persisted backtest's full detail by run_id (the id run_backtest "
    "returns). Returns the spec, the full metrics block, and the COMPLETE trade "
    "list (entry/exit bar index + price per round-trip) inline — the trade-by-"
    "trade breakdown run_backtest's compact summary omits. The equity curve is "
    "one point per bar and can be large, so it is NOT returned unless "
    "include_equity=true; when requested it is paged like get_ohlcv "
    f"(equity_offset / max_equity_points, capped at {MAX_EQUITY_POINTS}, with "
    "partial_reason='too_large' and total_available/offset/returned when more "
    "remain). An unknown run_id is a not-found error, not a result."
)


class BacktestNotFoundError(ValueError):
    """Raised when `run_id` has no indexed run, or its on-disk artifact is gone.

    Subclasses `ValueError` so it surfaces as an MCP error result at the tool
    boundary (the same class boundary validation uses) rather than a 500."""


class EquityPage(BaseModel):
    """One page of the equity curve (ADR-0046). Present only when
    `include_equity=true`. `total_available` is the FULL curve length; `points`
    is the slice `[offset : offset + page_size]`; `partial_reason="too_large"`
    when more points remain past this page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[EquityPoint]
    partial_reason: Literal["too_large"] | None = None
    message: str | None = None
    total_available: int
    offset: int
    returned: int


class GetBacktestResponse(BaseModel):
    """A persisted backtest's full detail. Spec + metrics + the complete trade
    list are always present; `equity` is `None` unless `include_equity=true`
    (then a paged `EquityPage`)."""

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
    params: dict[str, Any]
    costs: dict[str, float]
    initial_capital: float
    sizing: str
    # --- Output ---
    metrics: BacktestMetrics
    trades: list[Trade]
    equity: EquityPage | None = None


def _page_equity(curve: list[EquityPoint], *, offset: int, max_points: int | None) -> EquityPage:
    """Slice the equity curve to one inline page (ADR-0046). `total_available`
    is the full curve length — paging never shrinks the persisted curve."""
    page_size = MAX_EQUITY_POINTS if max_points is None else min(max_points, MAX_EQUITY_POINTS)
    total = len(curve)
    page = curve[offset : offset + page_size]
    returned = len(page)
    more_remain = offset + returned < total
    if more_remain:
        reason: Literal["too_large"] | None = "too_large"
        message: str | None = (
            f"returned equity[{offset}:{offset + returned}] of {total} total — more "
            f"remain; page on with equity_offset={offset + returned} "
            f"(page size {page_size}, max {MAX_EQUITY_POINTS})."
        )
    else:
        reason = None
        message = None
    return EquityPage(
        points=page,
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


def _get_backtest_response(
    *,
    repository: BacktestRunsRepository,
    runs_dir: Path,
    run_id: str,
    include_equity: bool,
    equity_offset: int,
    max_equity_points: int | None,
) -> GetBacktestResponse:
    """Body of `get_backtest`, factored out so the read path is unit-testable
    without a live MCP server."""
    if not run_id:
        raise ValueError("run_id must be non-empty")
    if equity_offset < 0:
        raise ValueError(f"equity_offset must be >= 0, got {equity_offset}")
    if max_equity_points is not None and max_equity_points < 1:
        raise ValueError(f"max_equity_points must be >= 1, got {max_equity_points}")

    summary = repository.get(run_id)
    if summary is None:
        raise BacktestNotFoundError(f"no backtest run with id {run_id!r}")
    artifact_dir = runs_dir / summary.artifact_path
    try:
        result = read_result(artifact_dir)
    except FileNotFoundError as exc:
        # Index row exists but the artifact is gone (deleted out from under the
        # index). The result is genuinely unavailable — surface as not-found.
        raise BacktestNotFoundError(
            f"artifact for run_id {run_id!r} is missing on disk: {exc}"
        ) from exc

    equity = (
        _page_equity(list(result.equity_curve), offset=equity_offset, max_points=max_equity_points)
        if include_equity
        else None
    )

    return GetBacktestResponse(
        run_id=result.run_id,
        engine_version=result.engine_version,
        strategy_id=result.strategy_id,
        strategy_version=result.strategy_version,
        symbol=result.symbol,
        timeframe=result.timeframe,
        range_start=result.range_start,
        range_end=result.range_end,
        params=result.params,
        costs=result.costs,
        initial_capital=result.initial_capital,
        sizing=result.sizing,
        metrics=result.metrics,
        trades=list(result.trades),
        equity=equity,
    )


def register_get_backtest(
    server: FastMCP,
    *,
    repository: BacktestRunsRepository,
    runs_dir: Path,
) -> None:
    """Bind the `get_backtest` tool to `server`. The repository + runs_dir are
    captured by closure so the tool body keeps the parameter list FastMCP
    introspects for the input schema. Registered alongside `run_backtest` (both
    need the SQLite index and the disk root)."""

    @server.tool(description=GET_BACKTEST_DESCRIPTION)
    async def get_backtest(
        run_id: str,
        include_equity: bool = False,
        equity_offset: int = 0,
        max_equity_points: int | None = None,
    ) -> GetBacktestResponse:
        return await asyncio.to_thread(
            _get_backtest_response,
            repository=repository,
            runs_dir=runs_dir,
            run_id=run_id,
            include_equity=include_equity,
            equity_offset=equity_offset,
            max_equity_points=max_equity_points,
        )


__all__ = [
    "GET_BACKTEST_DESCRIPTION",
    "MAX_EQUITY_POINTS",
    "BacktestNotFoundError",
    "EquityPage",
    "GetBacktestResponse",
    "_get_backtest_response",
    "register_get_backtest",
]
