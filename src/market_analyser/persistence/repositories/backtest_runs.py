"""Repository for `backtest_runs` — Plan 0008 phase 3.

The SQLite row is an index over the on-disk artifacts under `runs/<run_id>/`;
disk is the source of truth for the full `BacktestResult`. This repository
serves the list view (sortable, filterable summary) and primary-key lookups;
the artifact reader lives in `market_analyser.backtest.persistence`.

Filters compose with AND semantics. Default ordering is `finished_at DESC` so
the Recent Backtests view's first row is the most recent run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_analyser.backtest.result import BacktestRunSummary
from market_analyser.persistence.models.backtest_runs import BacktestRunRow

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


class BacktestRunsRepository:
    """CRUD façade for the `backtest_runs` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def insert(self, summary: BacktestRunSummary) -> None:
        """Insert one row. Raises if `run_id` collides — caller is responsible
        for the atomic on-disk-then-DB ordering documented in
        `market_analyser.backtest.persistence.persist`."""
        row = BacktestRunRow(
            run_id=summary.run_id,
            strategy_id=summary.strategy_id,
            strategy_version=summary.strategy_version,
            symbol=summary.symbol,
            timeframe=summary.timeframe,
            range_start=summary.range_start,
            range_end=summary.range_end,
            total_return=summary.total_return,
            sharpe=summary.sharpe,
            max_drawdown=summary.max_drawdown,
            win_rate=summary.win_rate,
            trade_count=summary.trade_count,
            finished_at=summary.finished_at,
            artifact_path=summary.artifact_path,
            engine_version=summary.engine_version,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def list(
        self,
        *,
        symbol: str | None = None,
        strategy_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[BacktestRunSummary]:
        """List backtest runs, most-recent first.

        `symbol` / `strategy_id` filters apply with AND semantics. `limit` is
        clamped to `[1, MAX_LIST_LIMIT]` — values outside the range raise
        rather than silently coerce so the renderer's `?limit=` validation
        surfaces honestly.
        """
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise ValueError(
                f"limit must be in [1, {MAX_LIST_LIMIT}], got {limit}",
            )

        stmt = select(BacktestRunRow)
        if symbol is not None:
            if not symbol:
                raise ValueError("symbol filter, when set, must be non-empty")
            stmt = stmt.where(BacktestRunRow.symbol == symbol.upper())
        if strategy_id is not None:
            if not strategy_id:
                raise ValueError("strategy_id filter, when set, must be non-empty")
            stmt = stmt.where(BacktestRunRow.strategy_id == strategy_id)
        stmt = stmt.order_by(BacktestRunRow.finished_at.desc()).limit(limit)

        with self._session_factory() as session:
            return [_row_to_summary(row) for row in session.scalars(stmt)]

    def get(self, run_id: str) -> BacktestRunSummary | None:
        """Return the row for `run_id` or `None`. Unknown id is not an error
        — callers use the None signal to decide between 200 and 404."""
        if not run_id:
            raise ValueError("run_id must be non-empty")
        with self._session_factory() as session:
            row = session.get(BacktestRunRow, run_id)
            return _row_to_summary(row) if row is not None else None


def _row_to_summary(row: BacktestRunRow) -> BacktestRunSummary:
    range_start = (
        row.range_start
        if row.range_start.tzinfo is not None
        else row.range_start.replace(tzinfo=UTC)
    )
    range_end = (
        row.range_end if row.range_end.tzinfo is not None else row.range_end.replace(tzinfo=UTC)
    )
    finished_at = (
        row.finished_at
        if row.finished_at.tzinfo is not None
        else row.finished_at.replace(tzinfo=UTC)
    )
    return BacktestRunSummary(
        run_id=row.run_id,
        strategy_id=row.strategy_id,
        strategy_version=row.strategy_version,
        symbol=row.symbol,
        timeframe=row.timeframe,
        range_start=range_start,
        range_end=range_end,
        total_return=row.total_return,
        sharpe=row.sharpe,
        max_drawdown=row.max_drawdown,
        win_rate=row.win_rate,
        trade_count=row.trade_count,
        finished_at=finished_at,
        artifact_path=row.artifact_path,
        engine_version=row.engine_version,
    )


__all__ = ["DEFAULT_LIST_LIMIT", "MAX_LIST_LIMIT", "BacktestRunsRepository"]
