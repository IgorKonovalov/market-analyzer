"""metric_points table — Plan 0055 phase 1 (ADR-0051).

One generic table for every historized external metric series: a scalar value
per (namespaced series id, UTC epoch-second timestamp). Plans 0056/0057 register
series ids and adapters on top of this table — no further migrations.

Revision ID: 0004_metric_points
Revises: 0003_create_backtest_runs
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_metric_points"
down_revision: str | None = "0003_create_backtest_runs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "metric_points",
        sa.Column("series_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("ts", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("metric_points")
