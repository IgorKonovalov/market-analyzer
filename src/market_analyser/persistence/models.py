"""SQLAlchemy ORM declarations.

Plan 0001 phase 3 introduced `bars`. Plan 0006 phase 2 adds `annotations` —
agent-written chart markers, keyed independently by uuid `id` so two identical
(symbol, timeframe, event_ts) inserts don't silently dedupe.

Per ADR-0006, `event_ts` (market time) is distinct from any wall-clock
timestamp — that gap is what makes historical replay deterministic.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for ORM models."""


class BarRow(Base):
    """One OHLCV bar row. Composite PK `(symbol, timeframe, event_ts)`."""

    __tablename__ = "bars"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String, primary_key=True)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnnotationRow(Base):
    """One agent-written annotation. PK is uuid `id`; the composite index serves the chart query."""

    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timeframe: Mapped[str] = mapped_column(String, nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_annotations_symbol_timeframe_event_ts",
            "symbol",
            "timeframe",
            "event_ts",
        ),
    )
