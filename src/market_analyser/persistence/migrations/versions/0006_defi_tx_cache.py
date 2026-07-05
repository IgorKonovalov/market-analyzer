"""defi_tx table — Plan 0035 phase 3 (ADR-0035/0036).

The immutable decoded-transaction cache behind the P&L replay: a re-scan
re-reads SQLite instead of re-paging Zerion. Keyed `(wallet, chain, hash)` —
wallet included because Zerion's decode is wallet-relative (transfer directions
are relative to the scanned address); see `models/defi_tx.py`.

Revision ID: 0006_defi_tx_cache
Revises: 0005_watches_alerts
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_defi_tx_cache"
down_revision: str | None = "0005_watches_alerts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "defi_tx",
        sa.Column("wallet", sa.String(), primary_key=True, nullable=False),
        sa.Column("chain", sa.String(), primary_key=True, nullable=False),
        sa.Column("hash", sa.String(), primary_key=True, nullable=False),
        sa.Column("mined_at", sa.Integer(), nullable=False),
        sa.Column("mined_at_block", sa.Integer(), nullable=False),
        sa.Column("in_block_index", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("defi_tx")
