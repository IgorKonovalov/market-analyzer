"""listing_snapshots table — Plan 0113 phase 3 (ADR-0107).

The self-diff baseline behind the event calendar's `listings` category: one row
per venue holding the last observed set of tradeable symbols (a sorted JSON array)
and its UTC capture time. The provider diffs the current live set against this
baseline to emit listing/delisting events, then overwrites the row. `venue` is the
primary key ("binance", "coinbase") — one row per venue, not a history.

Revision ID: 0012_listing_snapshots
Revises: 0011_defi_position_watches
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_listing_snapshots"
down_revision: str | None = "0011_defi_position_watches"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "listing_snapshots",
        sa.Column("venue", sa.String(), primary_key=True, nullable=False),
        sa.Column("symbols_json", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("listing_snapshots")
