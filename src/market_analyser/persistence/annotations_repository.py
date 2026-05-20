"""Repository for the `annotations` table — Plan 0006 phase 2.

Annotations are agent-written markers on the chart, keyed independently by uuid
`id`. The composite (symbol, timeframe, event_ts) is an index, not a unique
constraint — two inserts with identical canonical fields produce two distinct
rows so the agent can record overlapping observations without surprise dedup.

Validation lives on the Pydantic `Annotation` model; the repository trusts its
input and only enforces the read-side boundary checks (tz-aware, non-empty
symbol, sane window).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from market_analyser.annotations.types import Annotation, AnnotationKind
from market_analyser.persistence.models import AnnotationRow


class AnnotationsRepository:
    """CRUD façade for the `annotations` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def insert(self, annotation: Annotation) -> None:
        row = AnnotationRow(
            id=annotation.id,
            symbol=annotation.symbol,
            timeframe=annotation.timeframe,
            event_ts=annotation.event_ts,
            kind=annotation.kind.value,
            label=annotation.label,
            agent_id=annotation.agent_id,
            created_at=annotation.created_at,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()

    def list_for(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Annotation]:
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")
        if start > end:
            raise ValueError(f"start ({start}) must be <= end ({end})")

        stmt = (
            select(AnnotationRow)
            .where(AnnotationRow.symbol == symbol.upper())
            .where(AnnotationRow.timeframe == timeframe)
            .where(AnnotationRow.event_ts >= start)
            .where(AnnotationRow.event_ts <= end)
            .order_by(AnnotationRow.event_ts)
        )
        with self._session_factory() as session:
            return [_row_to_annotation(row) for row in session.scalars(stmt)]


def _row_to_annotation(row: AnnotationRow) -> Annotation:
    event_ts = row.event_ts if row.event_ts.tzinfo is not None else row.event_ts.replace(tzinfo=UTC)
    created_at = (
        row.created_at if row.created_at.tzinfo is not None else row.created_at.replace(tzinfo=UTC)
    )
    return Annotation(
        id=row.id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        event_ts=event_ts,
        kind=AnnotationKind(row.kind),
        label=row.label,
        agent_id=row.agent_id,
        created_at=created_at,
    )
