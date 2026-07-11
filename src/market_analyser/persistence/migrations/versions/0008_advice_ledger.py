"""advice_ledger table — Plan 0080 phase 1 (ADR-0075).

The append-only index over the advisor's own `recommend` calls: one row per call
(directional and flat), written beside the existing `runs/advice` explanation
artifact (the ADR-0018 disk-artifact + SQLite-index pattern). The nullable
outcome columns are filled later by the phase-3 scheduled scorer once each call's
horizon matures; the recorded call itself is never mutated or deleted.

Revision ID: 0008_advice_ledger
Revises: 0007_price_snapshots
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_advice_ledger"
down_revision: str | None = "0007_price_snapshots"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "advice_ledger",
        sa.Column("call_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("timeframe", sa.String(), nullable=False),
        sa.Column("strategy_id", sa.String(), nullable=False),
        sa.Column("as_of_bar_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_bars", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("entry_low", sa.Float(), nullable=True),
        sa.Column("entry_high", sa.Float(), nullable=True),
        sa.Column("stop", sa.Float(), nullable=True),
        sa.Column("targets_json", sa.String(), nullable=False),
        sa.Column("conviction", sa.Float(), nullable=False),
        sa.Column("forecast_prob", sa.Float(), nullable=True),
        sa.Column("artifact_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_class", sa.String(), nullable=True),
        sa.Column("realized_return", sa.Float(), nullable=True),
        sa.Column("realized_r", sa.Float(), nullable=True),
        sa.Column("directional_correct", sa.Boolean(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_advice_ledger_symbol", "advice_ledger", ["symbol"])
    op.create_index("ix_advice_ledger_outcome_class", "advice_ledger", ["outcome_class"])
    op.create_index("ix_advice_ledger_created_at", "advice_ledger", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_advice_ledger_created_at", table_name="advice_ledger")
    op.drop_index("ix_advice_ledger_outcome_class", table_name="advice_ledger")
    op.drop_index("ix_advice_ledger_symbol", table_name="advice_ledger")
    op.drop_table("advice_ledger")
