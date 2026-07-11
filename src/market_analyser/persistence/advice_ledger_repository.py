"""Repository for the `advice_ledger` — Plan 0080 phase 1 (ADR-0075).

The append-only index over the advisor's own `recommend` calls. The SQLite row
is a queryable projection beside the on-disk `runs/advice` explanation artifact
(the ADR-0018 disk-artifact + SQLite-index pattern); disk stays the source of
truth for the full verdict + leg inputs.

Two structural honesty properties live here:

* **Append-only, first-write-wins.** `record` inserts one row per call and never
  overwrites an existing one — so a re-run of the same recommendation at the same
  bar does not duplicate it, and a call whose outcome the phase-3 scorer already
  wrote can never be quietly replaced by a fresh (unscored) copy. Cherry-picking
  a loser out of the record is impossible: there is no update/delete path for a
  recorded call, and no code path that skips recording one.
* **Every call, directional or flat.** Flat "no actionable edge" calls are
  recorded too (with no levels and null outcome); they are excluded from the
  directional hit-rate but keep the "how often did the advisor commit" denominator
  honest.

The identity of a call is the synthetic `call_id`
(`symbol|timeframe|strategy_id|as_of_bar_ts|horizon_bars`), computed here so
callers deal only in the domain `AdviceLedgerEntry`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from market_analyser.persistence.models.advice_ledger import AdviceLedgerRow

DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 1000

_CALL_ID_DELIMITER = "|"


class AdviceLedgerEntry(BaseModel):
    """One recorded advisory recommendation, as the repository speaks it.

    The call half is written once and is immutable; the outcome half is null
    until the phase-3 scorer matures and scores the call (a flat call keeps it
    null forever). Frozen + ``extra="forbid"`` — a typo'd field fails loudly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity.
    symbol: str
    timeframe: str
    strategy_id: str
    as_of_bar_ts: datetime
    horizon_bars: int
    # The recorded ticket. Flat calls carry no levels: entry_zone/stop None,
    # targets empty.
    direction: Literal["long", "short", "flat"]
    entry_zone: tuple[float, float] | None
    stop: float | None
    targets: list[float]
    conviction: float
    forecast_prob: float | None
    artifact_path: str | None
    created_at: datetime
    # Outcome (null until scored — phase 3).
    outcome_class: str | None = None
    realized_return: float | None = None
    realized_r: float | None = None
    directional_correct: bool | None = None
    scored_at: datetime | None = None


def _call_id(
    *, symbol: str, timeframe: str, strategy_id: str, as_of_bar_ts: datetime, horizon_bars: int
) -> str:
    """The deterministic identity of a call. Same recommendation at the same bar
    → same id (idempotent record); a new bar → a new id."""
    return _CALL_ID_DELIMITER.join(
        (symbol, timeframe, strategy_id, as_of_bar_ts.isoformat(), str(horizon_bars))
    )


class AdviceLedgerRepository:
    """Storage facade for `advice_ledger`. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record(self, entry: AdviceLedgerEntry) -> bool:
        """Record one call, append-only. First-write-wins: returns True when the
        row was newly written, False when a row for this call already existed
        (the existing row — outcome included — is kept untouched)."""
        call_id = _call_id(
            symbol=entry.symbol,
            timeframe=entry.timeframe,
            strategy_id=entry.strategy_id,
            as_of_bar_ts=entry.as_of_bar_ts,
            horizon_bars=entry.horizon_bars,
        )
        entry_low, entry_high = entry.entry_zone if entry.entry_zone is not None else (None, None)
        with self._session_factory() as session:
            if session.get(AdviceLedgerRow, call_id) is not None:
                return False
            session.add(
                AdviceLedgerRow(
                    call_id=call_id,
                    symbol=entry.symbol,
                    timeframe=entry.timeframe,
                    strategy_id=entry.strategy_id,
                    as_of_bar_ts=entry.as_of_bar_ts,
                    horizon_bars=entry.horizon_bars,
                    direction=entry.direction,
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop=entry.stop,
                    targets_json=json.dumps(entry.targets),
                    conviction=entry.conviction,
                    forecast_prob=entry.forecast_prob,
                    artifact_path=entry.artifact_path,
                    created_at=entry.created_at,
                    outcome_class=entry.outcome_class,
                    realized_return=entry.realized_return,
                    realized_r=entry.realized_r,
                    directional_correct=entry.directional_correct,
                    scored_at=entry.scored_at,
                )
            )
            session.commit()
            return True

    def apply_outcome(
        self,
        *,
        symbol: str,
        timeframe: str,
        strategy_id: str,
        as_of_bar_ts: datetime,
        horizon_bars: int,
        outcome_class: str,
        realized_return: float | None,
        realized_r: float | None,
        directional_correct: bool | None,
        scored_at: datetime,
    ) -> None:
        """Persist a scored outcome onto an existing call row (Plan 0080 phase 3).

        Fills the outcome columns the phase-1 write left null. Takes the outcome
        as primitive fields rather than an `attribution.Outcome` so persistence
        stays free of a dependency on the attribution layer (which itself depends
        on this repository). Raises if the call is not present — the scorer only
        applies outcomes to rows it just read. The call half is never touched, so
        the record stays append-only in its ticket."""
        call_id = _call_id(
            symbol=symbol,
            timeframe=timeframe,
            strategy_id=strategy_id,
            as_of_bar_ts=as_of_bar_ts,
            horizon_bars=horizon_bars,
        )
        with self._session_factory() as session:
            row = session.get(AdviceLedgerRow, call_id)
            if row is None:
                raise ValueError(f"cannot apply an outcome to an unknown call {call_id!r}")
            row.outcome_class = outcome_class
            row.realized_return = realized_return
            row.realized_r = realized_r
            row.directional_correct = directional_correct
            row.scored_at = scored_at
            session.commit()

    def get(
        self,
        *,
        symbol: str,
        timeframe: str,
        strategy_id: str,
        as_of_bar_ts: datetime,
        horizon_bars: int,
    ) -> AdviceLedgerEntry | None:
        """Return the recorded call for this identity, or None."""
        call_id = _call_id(
            symbol=symbol,
            timeframe=timeframe,
            strategy_id=strategy_id,
            as_of_bar_ts=as_of_bar_ts,
            horizon_bars=horizon_bars,
        )
        with self._session_factory() as session:
            row = session.get(AdviceLedgerRow, call_id)
            return _row_to_entry(row) if row is not None else None

    def list(
        self,
        *,
        symbol: str | None = None,
        directional: bool | None = None,
        scored: bool | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[AdviceLedgerEntry]:
        """List recorded calls, most-recent first (by `created_at`).

        `symbol` filters to one symbol. `directional`, when set, filters to
        directional calls (True → `direction != 'flat'`) or flat ones (False).
        `scored`, when set, filters by maturity: True → the call has an outcome
        (`outcome_class` present), False → it does not yet. `limit` is clamped to
        `[1, MAX_LIST_LIMIT]` — an out-of-range value raises rather than silently
        coerce.
        """
        if limit < 1 or limit > MAX_LIST_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIST_LIMIT}], got {limit}")

        stmt = select(AdviceLedgerRow)
        if symbol is not None:
            if not symbol:
                raise ValueError("symbol filter, when set, must be non-empty")
            stmt = stmt.where(AdviceLedgerRow.symbol == symbol)
        if directional is not None:
            if directional:
                stmt = stmt.where(AdviceLedgerRow.direction != "flat")
            else:
                stmt = stmt.where(AdviceLedgerRow.direction == "flat")
        if scored is not None:
            if scored:
                stmt = stmt.where(AdviceLedgerRow.outcome_class.is_not(None))
            else:
                stmt = stmt.where(AdviceLedgerRow.outcome_class.is_(None))
        stmt = stmt.order_by(AdviceLedgerRow.created_at.desc()).limit(limit)

        with self._session_factory() as session:
            return [_row_to_entry(row) for row in session.scalars(stmt)]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_to_entry(row: AdviceLedgerRow) -> AdviceLedgerEntry:
    entry_zone = (
        (row.entry_low, row.entry_high)
        if row.entry_low is not None and row.entry_high is not None
        else None
    )
    direction: Literal["long", "short", "flat"]
    if row.direction in ("long", "short", "flat"):
        direction = row.direction  # type: ignore[assignment]
    else:  # pragma: no cover — the write path only ever stores the three literals
        raise ValueError(f"unexpected direction {row.direction!r} in advice_ledger")
    as_of = _aware(row.as_of_bar_ts)
    created = _aware(row.created_at)
    assert as_of is not None and created is not None  # both NOT NULL columns
    return AdviceLedgerEntry(
        symbol=row.symbol,
        timeframe=row.timeframe,
        strategy_id=row.strategy_id,
        as_of_bar_ts=as_of,
        horizon_bars=row.horizon_bars,
        direction=direction,
        entry_zone=entry_zone,
        stop=row.stop,
        targets=list(json.loads(row.targets_json)),
        conviction=row.conviction,
        forecast_prob=row.forecast_prob,
        artifact_path=row.artifact_path,
        created_at=created,
        outcome_class=row.outcome_class,
        realized_return=row.realized_return,
        realized_r=row.realized_r,
        directional_correct=row.directional_correct,
        scored_at=_aware(row.scored_at),
    )


__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "AdviceLedgerEntry",
    "AdviceLedgerRepository",
]
