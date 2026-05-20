"""annotations table — Plan 0006 phase 2.

Revision ID: 0002_annotations_table
Revises: 0001_bars_table
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_annotations_table"
down_revision: str | None = "0001_bars_table"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_annotations_symbol_timeframe_event_ts",
        "annotations",
        ["symbol", "timeframe", "event_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_annotations_symbol_timeframe_event_ts", table_name="annotations")
    op.drop_table("annotations")
