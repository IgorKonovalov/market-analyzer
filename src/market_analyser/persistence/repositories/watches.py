"""Repositories for `watches` + `alerts` — Plan 0060 phase 1 (ADR-0055).

`WatchesRepository` owns the watch definitions the scheduler ticks. Params
cross the storage boundary through `alerts/types.validate_watch_params` in
**both** directions: writes refuse an unknown kind or malformed params before
touching the table, and reads re-validate the stored JSON so a corrupted row
fails loudly instead of leaking an untyped dict downstream.

`last_state` is the edge-detector's persisted memory (ADR-0055): the scheduler
writes the predicate value after every evaluation, so the false→true
transition survives a sidecar restart — a condition that was already true
before the restart does not re-fire.

`AlertsRepository` is the append-only fire history. Reads are newest-first
(`fired_at` desc, `id` desc tiebreak — deterministic, never hash order) with
offset/limit paging plus a total count, the shape the ADR-0046-paged
`list_alerts` tool serves directly.

Wall-clock stays out of this module: `created_at` / `fired_at` are injected by
the caller (the tool / scheduler own the clock read), keeping repository
behaviour replayable in tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from market_analyser.alerts.types import Alert, Watch, validate_watch_params
from market_analyser.data.timeframes import registry_timeframes
from market_analyser.persistence.models.watches import AlertRow, WatchRow


class WatchesRepository:
    """CRUD facade for the `watches` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        symbol: str,
        timeframe: str,
        kind: str,
        params: Mapping[str, Any],
        interval_seconds: int,
        enabled: bool = True,
        created_at: datetime,
    ) -> Watch:
        """Validate at the boundary, insert, and return the persisted `Watch`.

        Raises `UnknownWatchKindError` / pydantic `ValidationError` for a bad
        `(kind, params)` pair and `ValueError` for an empty symbol, an
        unregistered timeframe, a non-positive interval, or a naive
        `created_at` — all before any write.
        """
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if timeframe not in registry_timeframes():
            raise ValueError(
                f"unknown timeframe {timeframe!r} (supported: {sorted(registry_timeframes())})",
            )
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC)")
        params_model = validate_watch_params(kind, params)

        row = WatchRow(
            symbol=symbol,
            timeframe=timeframe,
            kind=kind,
            # sort_keys keeps the stored blob byte-stable for identical params.
            params=json.dumps(params_model.model_dump(mode="json"), sort_keys=True),
            interval_seconds=interval_seconds,
            enabled=enabled,
            last_state=None,
            created_at=created_at,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
            return _row_to_watch(row)

    def get(self, watch_id: int) -> Watch | None:
        with self._session_factory() as session:
            row = session.get(WatchRow, watch_id)
            return _row_to_watch(row) if row is not None else None

    def list(self, *, enabled_only: bool = False) -> list[Watch]:
        """All watches ordered by `id` ascending (creation order, deterministic)."""
        stmt = select(WatchRow).order_by(WatchRow.id.asc())
        if enabled_only:
            stmt = stmt.where(WatchRow.enabled.is_(True))
        with self._session_factory() as session:
            return [_row_to_watch(row) for row in session.scalars(stmt)]

    def delete(self, watch_id: int) -> bool:
        """Delete the watch and its alert history. Returns False when absent.

        The alerts cleanup is explicit (not left to the FK cascade) because
        SQLite only honours `ON DELETE CASCADE` with `PRAGMA foreign_keys=ON`,
        which the engine does not set — orphaned history rows would silently
        accumulate otherwise.
        """
        with self._session_factory() as session:
            row = session.get(WatchRow, watch_id)
            if row is None:
                return False
            for alert_row in session.scalars(select(AlertRow).where(AlertRow.watch_id == watch_id)):
                session.delete(alert_row)
            session.delete(row)
            session.commit()
            return True

    def set_enabled(self, watch_id: int, *, enabled: bool) -> bool:
        """Flip the enabled flag. Returns False when the watch is absent."""
        with self._session_factory() as session:
            row = session.get(WatchRow, watch_id)
            if row is None:
                return False
            row.enabled = enabled
            session.commit()
            return True

    def set_last_state(self, watch_id: int, *, last_state: bool) -> bool:
        """Persist the edge-detector's memory after an evaluation. Returns
        False when the watch is absent (e.g. deleted mid-tick — a no-op, not
        an error, so the scheduler never crashes on a benign race)."""
        with self._session_factory() as session:
            row = session.get(WatchRow, watch_id)
            if row is None:
                return False
            row.last_state = last_state
            session.commit()
            return True


class AlertsRepository:
    """Append-only fire history for the `alerts` table."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def insert(self, *, watch_id: int, fired_at: datetime, payload: Mapping[str, Any]) -> Alert:
        """Append one fired alert and return it (with its assigned id).

        Raises `ValueError` for a naive `fired_at` or an unknown `watch_id`
        (checked explicitly — SQLite FKs are unenforced without the pragma).
        """
        if fired_at.tzinfo is None:
            raise ValueError("fired_at must be timezone-aware (UTC)")
        with self._session_factory() as session:
            if session.get(WatchRow, watch_id) is None:
                raise ValueError(f"unknown watch_id {watch_id}")
            row = AlertRow(
                watch_id=watch_id,
                fired_at=fired_at,
                payload=json.dumps(dict(payload), sort_keys=True),
            )
            session.add(row)
            session.commit()
            return _row_to_alert(row)

    def list(
        self,
        *,
        watch_id: int | None = None,
        offset: int = 0,
        limit: int,
    ) -> tuple[list[Alert], int]:
        """One newest-first page of alert history plus the total match count.

        Newest-first is `fired_at` desc with `id` desc as the tiebreak —
        deterministic under identical timestamps. `watch_id=None` reads across
        all watches.
        """
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        stmt = select(AlertRow).order_by(AlertRow.fired_at.desc(), AlertRow.id.desc())
        count_stmt = select(func.count()).select_from(AlertRow)
        if watch_id is not None:
            stmt = stmt.where(AlertRow.watch_id == watch_id)
            count_stmt = count_stmt.where(AlertRow.watch_id == watch_id)
        stmt = stmt.offset(offset).limit(limit)
        with self._session_factory() as session:
            total = session.scalar(count_stmt) or 0
            return [_row_to_alert(row) for row in session.scalars(stmt)], total


def _ensure_utc(value: datetime) -> datetime:
    """SQLite loses tzinfo on round-trip; stored datetimes are UTC by contract."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_watch(row: WatchRow) -> Watch:
    params = validate_watch_params(row.kind, json.loads(row.params))
    return Watch(
        id=row.id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        kind=row.kind,  # type: ignore[arg-type]  # re-validated by the Watch model
        params=params,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        last_state=row.last_state,
        created_at=_ensure_utc(row.created_at),
    )


def _row_to_alert(row: AlertRow) -> Alert:
    payload: dict[str, Any] = json.loads(row.payload)
    return Alert(
        id=row.id,
        watch_id=row.watch_id,
        fired_at=_ensure_utc(row.fired_at),
        payload=payload,
    )


__all__ = ["AlertsRepository", "WatchesRepository"]
