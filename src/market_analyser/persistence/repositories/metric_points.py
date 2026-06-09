"""Repository for `metric_points` — Plan 0055 phase 1 (ADR-0051).

Reads come in exactly two shapes (the contract's whole read surface):

- ``range(series_id, start, end)`` — every point with ``start <= ts <= end``,
  ordered by ``ts`` ascending (primary-key order, never hash iteration);
- ``as_of(series_id, ts)`` — the latest point with ``point.ts <= ts``, **never**
  a later one. This is the only join primitive the forecast feature pipeline is
  allowed to call: the no-lookahead rule enforced at the storage seam,
  mirroring ADR-0007's `as_of` argument.

Writes are upsert-once: a point that already exists with the same value is a
no-op; a re-fetch that *disagrees* with a stored value is refused with
`MetricPointConflictError` unless the caller takes the explicit ``refresh``
path — revisions are a source-quality problem to surface, not silently absorb
(ADR-0051 Immutability).

Every method validates the series id against the registry first
(`data/metric_series.py`): the table is generic, so the registry is the schema
and an unregistered id fails loudly before touching the table.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_analyser.data.metric_series import MetricPoint, get_series_spec
from market_analyser.persistence.models.metric_points import MetricPointRow


class MetricPointConflictError(ValueError):
    """An upsert carried a different value for an existing `(series_id, ts)`
    and the caller did not take the explicit `refresh` path (ADR-0051)."""


class MetricPointsRepository:
    """CRUD facade for the `metric_points` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def upsert_points(self, points: Sequence[MetricPoint], *, refresh: bool = False) -> int:
        """Insert `points`, returning how many were newly inserted.

        Per point: absent -> insert; present with the same value -> no-op
        (idempotent re-backfill); present with a different value -> raise
        `MetricPointConflictError`, unless `refresh=True` overwrites it (the
        explicit revision path). Registration is checked for the whole batch
        before any write; the batch commits as one transaction, so a rejected
        batch (unregistered id or conflict) writes nothing.
        """
        for point in points:
            get_series_spec(point.series_id)
        inserted = 0
        with self._session_factory() as session:
            to_insert: list[MetricPointRow] = []
            for point in points:
                existing = session.get(MetricPointRow, (point.series_id, point.ts))
                if existing is None:
                    to_insert.append(
                        MetricPointRow(series_id=point.series_id, ts=point.ts, value=point.value),
                    )
                    continue
                if existing.value == point.value:
                    continue
                if not refresh:
                    raise MetricPointConflictError(
                        f"metric point ({point.series_id!r}, ts={point.ts}) already stored "
                        f"with value {existing.value!r}; refusing to overwrite with "
                        f"{point.value!r} outside the explicit refresh path (ADR-0051)",
                    )
                existing.value = point.value
            for row in to_insert:
                session.add(row)
            session.commit()
            inserted = len(to_insert)
        return inserted

    def range(self, series_id: str, start: int, end: int) -> list[MetricPoint]:
        """Every stored point with `start <= ts <= end` (inclusive both ends),
        ordered by `ts` ascending."""
        get_series_spec(series_id)
        if start > end:
            raise ValueError(f"range start ({start}) must be <= end ({end})")
        stmt = (
            select(MetricPointRow)
            .where(
                MetricPointRow.series_id == series_id,
                MetricPointRow.ts >= start,
                MetricPointRow.ts <= end,
            )
            .order_by(MetricPointRow.ts.asc())
        )
        with self._session_factory() as session:
            return [_row_to_point(row) for row in session.scalars(stmt)]

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
        """The latest point with `point.ts <= ts`, or `None` when no point
        exists at or before the bound. Never returns a later point — this is
        the anti-lookahead join primitive (ADR-0051 / ADR-0030 invariant 1)."""
        get_series_spec(series_id)
        stmt = (
            select(MetricPointRow)
            .where(
                MetricPointRow.series_id == series_id,
                MetricPointRow.ts <= ts,
            )
            .order_by(MetricPointRow.ts.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalars(stmt).first()
            return _row_to_point(row) if row is not None else None


def _row_to_point(row: MetricPointRow) -> MetricPoint:
    return MetricPoint(series_id=row.series_id, ts=row.ts, value=row.value)


__all__ = ["MetricPointConflictError", "MetricPointsRepository"]
