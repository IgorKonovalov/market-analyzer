"""`GET /backtests` and `GET /backtests/{run_id}` — Plan 0008 phase 3.

Both routes are gated by the renderer bearer via the central middleware
(`api.app.create_app`). The MCP bearer must not authenticate either route —
the cross-tenant isolation rule from ADR-0017 applies, mirroring the
`GET /annotations` test from Plan 0006 phase 3.

The list endpoint reads from the SQLite index. The detail endpoint reads the
on-disk artifact and re-merges it into a full `BacktestResult` (the index row
is only used to surface a 404 for an unknown id; the disk is the source of
truth).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from market_analyser.backtest.persistence import read_result
from market_analyser.backtest.result import BacktestResult, BacktestRunSummary
from market_analyser.persistence.repositories.backtest_runs import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    BacktestRunsRepository,
)

router = APIRouter()


@router.get("/backtests", response_model=list[BacktestRunSummary])
def list_backtests(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1),
    strategy_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
) -> list[BacktestRunSummary]:
    repo: BacktestRunsRepository = request.app.state.backtest_runs_repository
    try:
        return repo.list(symbol=symbol, strategy_id=strategy_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/backtests/{run_id}", response_model=BacktestResult)
def get_backtest(request: Request, run_id: str) -> BacktestResult:
    repo: BacktestRunsRepository = request.app.state.backtest_runs_repository
    runs_dir: Path = request.app.state.runs_dir
    summary = repo.get(run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"no backtest run with id {run_id!r}")
    artifact_dir = runs_dir / summary.artifact_path
    try:
        return read_result(artifact_dir)
    except FileNotFoundError as exc:
        # Index row exists but the artifact is gone — the user (or a future
        # cleanup tool) deleted files out from under the index. Surface as
        # 404 (the result is genuinely unavailable) but with a distinct
        # detail so the cause is visible in logs.
        raise HTTPException(
            status_code=404,
            detail=f"artifact for run_id {run_id!r} is missing on disk: {exc}",
        ) from exc
