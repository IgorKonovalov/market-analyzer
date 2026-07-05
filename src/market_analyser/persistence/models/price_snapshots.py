"""`price_snapshots` ORM model — Plan 0035 phase 4 (ADR-0036).

One resolved historical price per (canonical token key, UTC epoch-second
timestamp). First-write-wins: a snapshot, once taken, is never overwritten —
that immutability is what makes a P&L re-run byte-identical when the upstream
price API revises (ADR-0036 determinism, the ADR-0018 posture).

`Base` lives in `_base.py`; the class is re-exported from the package
`__init__.py` so `Base.metadata` sees the table at migration time.
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class PriceSnapshotRow(Base):
    """One snapshotted price. Composite PK `(token, ts)` — first-write-wins."""

    __tablename__ = "price_snapshots"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, primary_key=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
