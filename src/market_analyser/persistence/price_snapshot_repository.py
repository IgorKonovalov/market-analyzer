"""Repository for the `price_snapshots` cache (Plan 0035 phase 4, ADR-0036).

The determinism mechanism of the P&L replay: every historical price resolved
from the upstream source is written here on first lookup and re-read
thereafter. Writes are **first-write-wins** — an existing `(token, ts)` row is
never overwritten, so an upstream revision cannot change a re-run's output.
There is deliberately no refresh path: unlike metric points (ADR-0051), a
snapshotted valuation input has no legitimate revision story — a bad snapshot
is a bug to investigate, not data to absorb.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from sqlalchemy.orm import Session

from market_analyser.persistence.models.price_snapshots import PriceSnapshotRow


class PriceSnapshotRepository:
    """Storage facade for `price_snapshots`. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get(self, token: str, ts: int) -> float | None:
        """The snapshotted price for `(token, ts)`, or `None` when no lookup
        has been snapshotted yet."""
        with self._session_factory() as session:
            row = session.get(PriceSnapshotRow, (token, ts))
            return row.price if row is not None else None

    def put(self, token: str, ts: int, price: float) -> bool:
        """Snapshot a resolved price. First-write-wins: returns True when the
        row was newly written, False when a snapshot already existed (the
        existing value is kept untouched). Rejects a non-finite or
        non-positive price at the boundary — a zero/NaN valuation input would
        poison every replay that reads it."""
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"price snapshot for ({token!r}, ts={ts}) must be finite and > 0")
        with self._session_factory() as session:
            if session.get(PriceSnapshotRow, (token, ts)) is not None:
                return False
            session.add(PriceSnapshotRow(token=token, ts=ts, price=price))
            session.commit()
            return True


__all__ = ["PriceSnapshotRepository"]
