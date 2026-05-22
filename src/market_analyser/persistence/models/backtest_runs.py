"""`backtest_runs` ORM model — Plan 0008 phase 3.

The searchable projection from
[ADR-0018](../../../../docs/architecture/adrs/0018-backtest-result-schema.md):
SQLite holds only the columns we want to filter/sort by; the canonical
`BacktestResult` lives on disk under `runs/<run_id>/{spec,result,equity_curve}`.

`Base` lives in the package `__init__.py` to keep the pre-package import
contract (`from market_analyser.persistence.models import Base`) intact while
opening per-domain-file growth.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class BacktestRunRow(Base):
    """One indexed backtest run. PK is the engine-issued `run_id` (UUID4 hex).

    `artifact_path` is relative to the sidecar's `runs_dir` (equals `run_id`
    in v1). `engine_version` is the value that produced the on-disk artifact,
    so a regenerated golden fixture's runs can be filtered separately from
    legacy runs.
    """

    __tablename__ = "backtest_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timeframe: Mapped[str] = mapped_column(String, nullable=False)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_return: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String, nullable=False)
    engine_version: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("ix_backtest_runs_finished_at", "finished_at"),
        Index("ix_backtest_runs_symbol_timeframe", "symbol", "timeframe"),
        Index("ix_backtest_runs_strategy_id", "strategy_id"),
    )
