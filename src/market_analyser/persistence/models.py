"""SQLAlchemy ORM declarations.

Plan 0001 phase 3 introduces the `bars` table only. Strategy/run/trade tables
land in their owning plans (Plan 0002 / future backtest plan). Per ADR-0006,
`event_ts` (market time) is distinct from `ingested_at` (wall-clock time at
write) — that gap is what makes historical replay deterministic.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String
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
