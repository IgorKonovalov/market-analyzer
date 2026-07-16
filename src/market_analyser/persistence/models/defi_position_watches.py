"""`defi_position_watches` + `defi_position_alerts` ORM models — Plan 0099
phase 1 (ADR-0093).

A dedicated subsystem, sibling to (not a generalization of) the ADR-0055
`watches`/`alerts` tables — the shipped market-alert path is untouched.

`out_since` + `alert_fired` are the dwell reducer's persisted memory
(`defi/position_watch.py::DwellState`): the first observation time of the
current out-of-range excursion and whether that excursion has already
alerted. Persisting both is what lets "out of range since 03:00" survive a
sidecar restart without re-firing an excursion that already alerted.

`DefiPositionAlertRow.payload` is the condition-only `DefiPositionAlert`
JSON (ADR-0029: facts, never a directive); `watch_id`/`fired_at` are
duplicated as real columns for indexed history reads.

Both classes are re-exported from the package `__init__.py` so
`Base.metadata` sees the tables at migration time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class DefiPositionWatchRow(Base):
    """One persisted position-watch definition. Autoincrement integer PK."""

    __tablename__ = "defi_position_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(String, nullable=False)
    chain: Mapped[str] = mapped_column(String, nullable=False)
    pool_address: Mapped[str] = mapped_column(String, nullable=False)
    nft_token_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwell_hours: Mapped[float] = mapped_column(Float, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    out_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_fired: Mapped[bool] = mapped_column(Boolean, nullable=False)


class DefiPositionAlertRow(Base):
    """One fired out-of-range alert. Append-only history; `payload` is the
    condition-only `DefiPositionAlert` JSON."""

    __tablename__ = "defi_position_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("defi_position_watches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("ix_defi_position_alerts_watch_id", "watch_id"),
        Index("ix_defi_position_alerts_fired_at", "fired_at"),
    )
