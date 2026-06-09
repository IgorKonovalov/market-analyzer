"""`metric_points` ORM model — Plan 0055 phase 1 (ADR-0051).

One generic row shape for every historized external metric series: a scalar
`value` per (namespaced `series_id`, UTC epoch-second `ts`). The composite
primary key makes `(series_id, ts)` upsert-once and gives `range` / `as_of`
reads index-ordered determinism for free (ADR-0051 Notes).

`ts` is an integer (UTC epoch seconds), not a DateTime: the contract's only
time operations are ordering comparisons, and an integer column avoids the
naive/aware round-trip normalisation the datetime-keyed tables need.

`Base` lives in `_base.py`; the class is re-exported from the package
`__init__.py` so `Base.metadata` sees the table at migration time.
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class MetricPointRow(Base):
    """One metric point. Composite PK `(series_id, ts)` — upsert-once per ADR-0051."""

    __tablename__ = "metric_points"

    series_id: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
