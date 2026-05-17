"""Repository layer over the SQLAlchemy ORM. All persistence callers go through here.

Per ADR-0006, `BarRepository.upsert_bars` deduplicates on the composite key
`(symbol, timeframe, event_ts)` so re-fetching the same range is idempotent.
The `as_of` argument on reads is the anti-lookahead filter: rows with
`ingested_at > as_of` are excluded so a backtest at simulated time `T` only
sees data that existed at `T`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from market_analyser.data.types import Bar
from market_analyser.persistence.models import BarRow


class BarRepository:
    """CRUD façade for the `bars` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")

        stmt = (
            select(BarRow)
            .where(BarRow.symbol == symbol.upper())
            .where(BarRow.timeframe == timeframe)
            .where(BarRow.event_ts >= start)
            .where(BarRow.event_ts <= end)
            .order_by(BarRow.event_ts)
        )
        if as_of is not None:
            stmt = stmt.where(BarRow.ingested_at <= as_of)

        with self._session_factory() as session:
            return [_row_to_bar(row) for row in session.scalars(stmt)]

    def upsert_bars(self, bars: Iterable[Bar]) -> int:
        """Insert-or-replace each bar. Returns the count actually written.

        Defends against empty `source` strings at the repository boundary, per
        the phase-3 security checklist.
        """
        rows = list(bars)
        if not rows:
            return 0
        now = datetime.now(tz=UTC)
        payload: list[dict[str, object]] = []
        for bar in rows:
            if not bar.source:
                raise ValueError("bar.source must be non-empty")
            payload.append(
                {
                    "symbol": bar.symbol,
                    "timeframe": bar.timeframe,
                    "event_ts": bar.event_ts,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "source": bar.source,
                    "ingested_at": now,
                },
            )

        insert_stmt = sqlite_insert(BarRow).values(payload)
        update_cols = {
            "open": insert_stmt.excluded.open,
            "high": insert_stmt.excluded.high,
            "low": insert_stmt.excluded.low,
            "close": insert_stmt.excluded.close,
            "volume": insert_stmt.excluded.volume,
            "source": insert_stmt.excluded.source,
            "ingested_at": insert_stmt.excluded.ingested_at,
        }
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "event_ts"],
            set_=update_cols,
        )
        with self._session_factory() as session:
            session.execute(upsert_stmt)
            session.commit()
        return len(rows)


def _row_to_bar(row: BarRow) -> Bar:
    event_ts = row.event_ts if row.event_ts.tzinfo is not None else row.event_ts.replace(tzinfo=UTC)
    return Bar(
        symbol=row.symbol,
        timeframe=row.timeframe,
        event_ts=event_ts,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        source=row.source,
    )
