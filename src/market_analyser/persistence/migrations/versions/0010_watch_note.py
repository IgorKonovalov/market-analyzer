"""Nullable `note` on watches — Plan 0110 phase 1.

Free-text context attached to a watch definition ("ETH long scenario A —
neckline retest"): agent-set at `create_watch` time, viewer-edited later.
Context, not a condition fact — it never enters `alert.triggered` payloads
(ADR-0029 boundary), so `alerts` is untouched.

Revision ID: 0010_watch_note
Revises: 0009_purge_orphaned_yahoo_crypto_bars
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_watch_note"
down_revision: str | None = "0009_purge_orphaned_yahoo_crypto_bars"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("watches", sa.Column("note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("watches", "note")
