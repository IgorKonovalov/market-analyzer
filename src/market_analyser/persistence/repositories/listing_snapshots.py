"""Repository for `listing_snapshots` — Plan 0113 phase 3 (ADR-0107).

The self-diff baseline behind the event calendar's `listings` category. Two
operations, the whole contract:

- ``get_symbols(venue)`` — the venue's last observed tradeable-symbol set, or
  ``None`` when no baseline exists yet (the cold-start signal the provider records
  a baseline on instead of diffing);
- ``replace(venue, symbols, captured_at)`` — overwrite the venue's baseline with the
  current set (upsert). One row per venue — this is a moving baseline, not a history.

Symbols are serialized as a **sorted** JSON array so the stored blob is deterministic
for a given set (no `set`-iteration order on the wire), and re-hydrated into a `set`
for the caller's diff.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy.orm import Session

from market_analyser.persistence.models.listing_snapshots import ListingSnapshotRow


class ListingSnapshotsRepository:
    """CRUD facade for the `listing_snapshots` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_symbols(self, venue: str) -> set[str] | None:
        """The venue's stored tradeable-symbol set, or `None` when no baseline exists
        (cold start). A shape-broken stored blob reads as `None` rather than raising —
        the provider then re-baselines, which is the safe degrade."""
        with self._session_factory() as session:
            row = session.get(ListingSnapshotRow, venue)
            if row is None:
                return None
        try:
            parsed = json.loads(row.symbols_json)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, list):
            return None
        return {str(symbol) for symbol in parsed}

    def replace(self, venue: str, symbols: Iterable[str], captured_at: datetime) -> None:
        """Overwrite the venue's baseline with `symbols` (upsert). Symbols are stored
        as a sorted JSON array for a deterministic blob."""
        payload = json.dumps(sorted({str(symbol) for symbol in symbols}))
        with self._session_factory() as session:
            row = session.get(ListingSnapshotRow, venue)
            if row is None:
                session.add(
                    ListingSnapshotRow(venue=venue, symbols_json=payload, captured_at=captured_at)
                )
            else:
                row.symbols_json = payload
                row.captured_at = captured_at
            session.commit()
