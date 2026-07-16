"""defi_position_watches + defi_position_alerts tables — Plan 0099 phase 1
(ADR-0093).

A dedicated DeFi LP out-of-range monitor subsystem, sibling to the ADR-0055
`watches`/`alerts` pair (which is untouched). `out_since` + `alert_fired`
are the dwell reducer's persisted memory: when the current out-of-range
excursion was first observed and whether it has already alerted — persisted
so the dwell survives a sidecar restart without re-firing.
`defi_position_alerts` is the append-only fire history; `payload` is the
condition-only `DefiPositionAlert` JSON (ADR-0029: facts, never a
directive).

Revision ID: 0011_defi_position_watches
Revises: 0010_watch_note
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_defi_position_watches"
down_revision: str | None = "0010_watch_note"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "defi_position_watches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("wallet", sa.String(), nullable=False),
        sa.Column("chain", sa.String(), nullable=False),
        sa.Column("pool_address", sa.String(), nullable=False),
        sa.Column("nft_token_id", sa.Integer(), nullable=True),
        sa.Column("dwell_hours", sa.Float(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("out_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_fired", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "defi_position_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "watch_id",
            sa.Integer(),
            sa.ForeignKey("defi_position_watches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.String(), nullable=False),
    )
    op.create_index("ix_defi_position_alerts_watch_id", "defi_position_alerts", ["watch_id"])
    op.create_index("ix_defi_position_alerts_fired_at", "defi_position_alerts", ["fired_at"])


def downgrade() -> None:
    op.drop_index("ix_defi_position_alerts_fired_at", table_name="defi_position_alerts")
    op.drop_index("ix_defi_position_alerts_watch_id", table_name="defi_position_alerts")
    op.drop_table("defi_position_alerts")
    op.drop_table("defi_position_watches")
