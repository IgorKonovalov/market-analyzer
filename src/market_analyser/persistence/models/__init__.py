"""SQLAlchemy ORM declarations.

Plan 0001 phase 3 introduced `bars`. Plan 0006 phase 2 added `annotations` —
agent-written chart markers, keyed independently by uuid `id` so two identical
(symbol, timeframe, event_ts) inserts don't silently dedupe. Plan 0008 phase 3
adds `backtest_runs` (searchable projection of `BacktestResult`, per ADR-0018).

Per ADR-0006, `event_ts` (market time) is distinct from any wall-clock
timestamp — that gap is what makes historical replay deterministic.

`Base` lives in `_base.py` so per-domain sub-modules can import it without
inducing a circular dependency on the package `__init__.py`. Each sub-module's
class is re-exported here so `Base.metadata` sees every table at migration
time and pre-package import paths
(`from market_analyser.persistence.models import BarRow`) keep working.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base
from market_analyser.persistence.models.advice_ledger import AdviceLedgerRow
from market_analyser.persistence.models.backtest_runs import BacktestRunRow
from market_analyser.persistence.models.defi_tx import DefiTxRow
from market_analyser.persistence.models.metric_points import MetricPointRow
from market_analyser.persistence.models.price_snapshots import PriceSnapshotRow
from market_analyser.persistence.models.watches import AlertRow, WatchRow


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


__all__ = [
    "AdviceLedgerRow",
    "AlertRow",
    "AnnotationRow",
    "BacktestRunRow",
    "BarRow",
    "Base",
    "DefiTxRow",
    "MetricPointRow",
    "PriceSnapshotRow",
    "WatchRow",
]
