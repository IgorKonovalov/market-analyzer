"""`listing_snapshots` ORM model — Plan 0113 phase 3 (ADR-0107).

The self-diff baseline for crypto listings/delistings: one row per venue holding
the last observed set of tradeable symbols (as a sorted JSON array) plus when it was
captured. The listings provider reads the prior set, diffs it against the current
live set, emits one event per add/remove, then overwrites the row. A venue with no
row yet is a cold start — the provider records the baseline and emits nothing (by
design: day-1 has nothing to diff against).

`venue` is the primary key ("binance", "coinbase"); `symbols_json` is the sorted
tradeable-symbol set serialized deterministically; `captured_at` is the UTC read time.

`Base` lives in `_base.py`; the class is re-exported from the package `__init__.py`
so `Base.metadata` sees the table at migration time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class ListingSnapshotRow(Base):
    """One venue's last-observed tradeable-symbol set. PK `venue` — one row per venue,
    overwritten each read (the diff baseline, not a history)."""

    __tablename__ = "listing_snapshots"

    venue: Mapped[str] = mapped_column(String, primary_key=True)
    symbols_json: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
