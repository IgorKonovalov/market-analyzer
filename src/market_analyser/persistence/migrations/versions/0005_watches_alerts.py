"""watches + alerts tables — Plan 0060 phase 1 (ADR-0055).

`watches` holds the persisted watch definitions the in-sidecar scheduler
ticks; `last_state` is the edge-detector's memory (NULL until the first
evaluation), persisted so a sidecar restart does not re-fire a condition that
was already true. `alerts` is the append-only fire history; `payload` is the
condition-only `alert.triggered v1` JSON (ADR-0029: facts, never a directive).

Revision ID: 0005_watches_alerts
Revises: 0004_metric_points
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_watches_alerts"
down_revision: str | None = "0004_metric_points"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "watches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("params", sa.String(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_state", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "watch_id",
            sa.Integer(),
            sa.ForeignKey("watches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.String(), nullable=False),
    )
    op.create_index("ix_alerts_watch_id", "alerts", ["watch_id"])
    op.create_index("ix_alerts_fired_at", "alerts", ["fired_at"])


def downgrade() -> None:
    op.drop_index("ix_alerts_fired_at", table_name="alerts")
    op.drop_index("ix_alerts_watch_id", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("watches")
