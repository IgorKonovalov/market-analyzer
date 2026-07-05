"""price_snapshots table — Plan 0035 phase 4 (ADR-0036).

The `(token, timestamp) → price` snapshot cache: every historical price the
P&L engine resolves is written on first lookup and re-read thereafter, so a
re-run is byte-identical even if the upstream price API later revises its
numbers (ADR-0036 determinism, mirroring ADR-0018). `token` is the canonical
DefiLlama coin key (`chain:address`, or `coingecko:ethereum` for the native
coin); `ts` is the UTC epoch-second block timestamp the lookup was made at.

Revision ID: 0007_price_snapshots
Revises: 0006_defi_tx_cache
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_price_snapshots"
down_revision: str | None = "0006_defi_tx_cache"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "price_snapshots",
        sa.Column("token", sa.String(), primary_key=True, nullable=False),
        sa.Column("ts", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("price_snapshots")
