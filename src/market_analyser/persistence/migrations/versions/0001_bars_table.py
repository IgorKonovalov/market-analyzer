"""bars table — Plan 0001 phase 3.

Revision ID: 0001_bars_table
Revises:
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_bars_table"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "bars",
        sa.Column("symbol", sa.String(), primary_key=True, nullable=False),
        sa.Column("timeframe", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "event_ts",
            sa.DateTime(timezone=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bars")
