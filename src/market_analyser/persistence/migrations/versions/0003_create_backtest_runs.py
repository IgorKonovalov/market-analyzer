"""backtest_runs table — Plan 0008 phase 3.

Revision ID: 0003_create_backtest_runs
Revises: 0002_annotations_table
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_create_backtest_runs"
down_revision: str | None = "0002_annotations_table"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("run_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("strategy_id", sa.String(), nullable=False),
        sa.Column("strategy_version", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=False),
        sa.Column("sharpe", sa.Float(), nullable=False),
        sa.Column("max_drawdown", sa.Float(), nullable=False),
        sa.Column("win_rate", sa.Float(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_path", sa.String(), nullable=False),
        sa.Column("engine_version", sa.String(), nullable=False),
    )
    op.create_index("ix_backtest_runs_finished_at", "backtest_runs", ["finished_at"])
    op.create_index(
        "ix_backtest_runs_symbol_timeframe",
        "backtest_runs",
        ["symbol", "timeframe"],
    )
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_strategy_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_symbol_timeframe", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_finished_at", table_name="backtest_runs")
    op.drop_table("backtest_runs")
