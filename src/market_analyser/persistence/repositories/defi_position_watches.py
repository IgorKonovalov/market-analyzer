"""Repositories for `defi_position_watches` + `defi_position_alerts` —
Plan 0099 phase 1 (ADR-0093).

`DefiPositionWatchesRepository` owns the watch definitions the phase-2
monitor ticks. Writes validate at the boundary (address shapes, chain,
positive dwell/interval, tz-aware timestamps) before touching the table;
reads re-validate through the `DefiPositionWatch` model so a corrupted row
fails loudly instead of leaking an untyped shape downstream.

`out_since` + `alert_fired` columns persist the dwell reducer's memory
(`defi/position_watch.py::DwellState`): the monitor writes the reduced state
after every successful observation, so the dwell survives a sidecar restart
and an already-fired excursion does not re-fire. A failed RPC read writes
nothing (ADR-0093 — the prior state stays untouched).

`DefiPositionAlertsRepository` is the append-only fire history, mirroring
the ADR-0055 `AlertsRepository` shape: newest-first (`fired_at` desc, `id`
desc tiebreak — deterministic, never hash order) with offset/limit paging
plus a total count.

Wall-clock stays out of this module: `created_at` / `fired_at` / dwell
timestamps are injected by the caller (the tool / monitor own the clock
read), keeping repository behaviour replayable in tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import get_args

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from market_analyser.defi.models import Chain, PositionToken
from market_analyser.defi.position_watch import (
    DefiPositionAlert,
    DefiPositionWatch,
    DwellState,
    WatchSource,
    validate_evm_address,
)
from market_analyser.persistence.models.defi_position_watches import (
    DefiPositionAlertRow,
    DefiPositionWatchRow,
)

CHAINS: frozenset[str] = frozenset(get_args(Chain))
WATCH_SOURCES: frozenset[str] = frozenset(get_args(WatchSource))


class DefiPositionWatchesRepository:
    """CRUD facade for `defi_position_watches`. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        *,
        wallet: str,
        chain: str,
        pool_address: str,
        nft_token_id: int | None = None,
        dwell_hours: float,
        interval_seconds: int,
        enabled: bool = True,
        source: str,
        created_at: datetime,
    ) -> DefiPositionWatch:
        """Validate at the boundary, insert, and return the persisted watch.

        Raises `ValueError` for a malformed wallet/pool address, an unknown
        chain or source, a non-positive dwell/interval, a negative
        `nft_token_id`, or a naive `created_at` — all before any write.
        """
        validate_evm_address(wallet, field="wallet")
        validate_evm_address(pool_address, field="pool_address")
        if chain not in CHAINS:
            raise ValueError(f"unknown chain {chain!r} (supported: {sorted(CHAINS)})")
        if source not in WATCH_SOURCES:
            raise ValueError(f"unknown source {source!r} (supported: {sorted(WATCH_SOURCES)})")
        if nft_token_id is not None and nft_token_id < 0:
            raise ValueError(f"nft_token_id must be >= 0, got {nft_token_id}")
        if not dwell_hours > 0:  # also rejects NaN
            raise ValueError(f"dwell_hours must be > 0, got {dwell_hours}")
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC)")

        row = DefiPositionWatchRow(
            wallet=wallet,
            chain=chain,
            pool_address=pool_address,
            nft_token_id=nft_token_id,
            dwell_hours=dwell_hours,
            interval_seconds=interval_seconds,
            enabled=enabled,
            source=source,
            created_at=created_at,
            out_since=None,
            alert_fired=False,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
            return _row_to_watch(row)

    def get(self, watch_id: int) -> DefiPositionWatch | None:
        with self._session_factory() as session:
            row = session.get(DefiPositionWatchRow, watch_id)
            return _row_to_watch(row) if row is not None else None

    def list(self, *, enabled_only: bool = False) -> list[DefiPositionWatch]:
        """All watches ordered by `id` ascending (creation order, deterministic)."""
        stmt = select(DefiPositionWatchRow).order_by(DefiPositionWatchRow.id.asc())
        if enabled_only:
            stmt = stmt.where(DefiPositionWatchRow.enabled.is_(True))
        with self._session_factory() as session:
            return [_row_to_watch(row) for row in session.scalars(stmt)]

    def delete(self, watch_id: int) -> bool:
        """Delete the watch and its alert history. Returns False when absent.

        The alerts cleanup is explicit (not left to the FK cascade) because
        SQLite only honours `ON DELETE CASCADE` with `PRAGMA foreign_keys=ON`,
        which the engine does not set.
        """
        with self._session_factory() as session:
            row = session.get(DefiPositionWatchRow, watch_id)
            if row is None:
                return False
            alerts = select(DefiPositionAlertRow).where(DefiPositionAlertRow.watch_id == watch_id)
            for alert_row in session.scalars(alerts):
                session.delete(alert_row)
            session.delete(row)
            session.commit()
            return True

    def set_enabled(self, watch_id: int, *, enabled: bool) -> bool:
        """Flip the enabled flag. Returns False when the watch is absent."""
        with self._session_factory() as session:
            row = session.get(DefiPositionWatchRow, watch_id)
            if row is None:
                return False
            row.enabled = enabled
            session.commit()
            return True

    def set_dwell_state(self, watch_id: int, state: DwellState) -> bool:
        """Persist the dwell reducer's memory after a successful observation.

        Returns False when the watch is absent (deleted mid-tick — a no-op,
        not an error, so the monitor never crashes on a benign race). Raises
        `ValueError` for a naive `out_since`.
        """
        if state.out_since is not None and state.out_since.tzinfo is None:
            raise ValueError("out_since must be timezone-aware (UTC)")
        with self._session_factory() as session:
            row = session.get(DefiPositionWatchRow, watch_id)
            if row is None:
                return False
            row.out_since = state.out_since
            row.alert_fired = state.fired
            session.commit()
            return True


class DefiPositionAlertsRepository:
    """Append-only fire history for `defi_position_alerts`."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def insert(
        self,
        *,
        watch_id: int,
        wallet: str,
        chain: str,
        pool_address: str,
        nft_token_id: int | None,
        fired_at: datetime,
        out_since: datetime,
        hours_out: float,
        tick_lower: int,
        tick_upper: int,
        current_tick: int,
        uncollected_fees: list[PositionToken] | None,
    ) -> DefiPositionAlert:
        """Append one fired alert and return it (with its assigned id).

        The condition-fact fields validate through `DefiPositionAlert` before
        any write (`in_range` is False by construction — an alert IS the
        out-of-range fact). Raises `ValueError` for a naive timestamp or an
        unknown `watch_id` (checked explicitly — SQLite FKs are unenforced
        without the pragma).
        """
        if fired_at.tzinfo is None or out_since.tzinfo is None:
            raise ValueError("fired_at and out_since must be timezone-aware (UTC)")
        candidate = DefiPositionAlert(
            id=0,  # placeholder until the row's autoincrement id is assigned
            watch_id=watch_id,
            wallet=wallet,
            chain=chain,  # type: ignore[arg-type]  # validated by the model
            pool_address=pool_address,
            nft_token_id=nft_token_id,
            fired_at=fired_at,
            out_since=out_since,
            hours_out=hours_out,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
            in_range=False,
            uncollected_fees=uncollected_fees,
        )
        with self._session_factory() as session:
            if session.get(DefiPositionWatchRow, watch_id) is None:
                raise ValueError(f"unknown watch_id {watch_id}")
            row = DefiPositionAlertRow(
                watch_id=watch_id,
                fired_at=fired_at,
                # sort_keys keeps the stored blob byte-stable for identical facts.
                payload=json.dumps(
                    candidate.model_dump(mode="json", exclude={"id"}), sort_keys=True
                ),
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
    ) -> tuple[list[DefiPositionAlert], int]:
        """One newest-first page of alert history plus the total match count.

        Newest-first is `fired_at` desc with `id` desc as the tiebreak —
        deterministic under identical timestamps. `watch_id=None` reads
        across all watches.
        """
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        stmt = select(DefiPositionAlertRow).order_by(
            DefiPositionAlertRow.fired_at.desc(), DefiPositionAlertRow.id.desc()
        )
        count_stmt = select(func.count()).select_from(DefiPositionAlertRow)
        if watch_id is not None:
            stmt = stmt.where(DefiPositionAlertRow.watch_id == watch_id)
            count_stmt = count_stmt.where(DefiPositionAlertRow.watch_id == watch_id)
        stmt = stmt.offset(offset).limit(limit)
        with self._session_factory() as session:
            total = session.scalar(count_stmt) or 0
            return [_row_to_alert(row) for row in session.scalars(stmt)], total


def _ensure_utc(value: datetime) -> datetime:
    """SQLite loses tzinfo on round-trip; stored datetimes are UTC by contract."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_watch(row: DefiPositionWatchRow) -> DefiPositionWatch:
    return DefiPositionWatch(
        id=row.id,
        wallet=row.wallet,
        chain=row.chain,  # type: ignore[arg-type]  # re-validated by the model
        pool_address=row.pool_address,
        nft_token_id=row.nft_token_id,
        dwell_hours=row.dwell_hours,
        interval_seconds=row.interval_seconds,
        enabled=row.enabled,
        source=row.source,  # type: ignore[arg-type]  # re-validated by the model
        created_at=_ensure_utc(row.created_at),
        dwell_state=DwellState(
            out_since=_ensure_utc(row.out_since) if row.out_since is not None else None,
            fired=row.alert_fired,
        ),
    )


def _row_to_alert(row: DefiPositionAlertRow) -> DefiPositionAlert:
    payload = json.loads(row.payload)
    return DefiPositionAlert(id=row.id, **{k: v for k, v in payload.items() if k != "id"})


__all__ = [
    "CHAINS",
    "WATCH_SOURCES",
    "DefiPositionAlertsRepository",
    "DefiPositionWatchesRepository",
]
