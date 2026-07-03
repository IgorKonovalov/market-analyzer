"""`watches` + `alerts` ORM models — Plan 0060 phase 1 (ADR-0055).

`WatchRow.params` and `AlertRow.payload` are JSON strings: the boundary
models in `alerts/types.py` (and the `alert.triggered v1` payload model in
`events/`) are the schema; the column stays an opaque validated blob so a new
watch kind is a vocabulary change, not a migration.

`WatchRow.last_state` is the edge-detector's persisted memory: the previous
evaluation's predicate value, NULL until the first evaluation. Persisting it
is what keeps a sidecar restart from re-firing a condition that was already
true (the false→true transition is detectable across process death).

Both classes are re-exported from the package `__init__.py` so
`Base.metadata` sees the tables at migration time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class WatchRow(Base):
    """One persisted watch definition. Autoincrement integer PK."""

    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timeframe: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[str] = mapped_column(String, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_state: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertRow(Base):
    """One fired alert. Append-only history; `payload` is the condition-only
    `alert.triggered v1` JSON."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("watches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("ix_alerts_watch_id", "watch_id"),
        Index("ix_alerts_fired_at", "fired_at"),
    )
