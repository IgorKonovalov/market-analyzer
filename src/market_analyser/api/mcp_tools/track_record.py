"""`get_track_record` MCP tool — Plan 0080 phase 4 (ADR-0075, ADR-0046).

The read-only surface over the advisor's own live track record: it reads the
scored recommendation rows from the ledger, rolls them up through the pure
aggregation (hit-rate + mean R + calibration + baseline delta, honest small-n),
and returns the aggregates alongside a bounded page of the most-recent scored
calls.

Charter-safe by construction (ADR-0029): it reports the record as *fact* — how
the past calls turned out, relative to a trivial baseline, with the sample size
stated — and never turns that into advice. It says nothing about what to do now
and never implies the record is a reason to trust or act.

Delivery follows ADR-0046: the aggregation runs over up to `AGGREGATION_CAP`
most-recent scored calls; the inline `recent` list is capped at
`MAX_RECENT_CALLS` per page, and a larger set returns the first page with the
typed `partial_reason="too_large"` and the offset to page on with.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.attribution.track_record import TrackRecord, track_record
from market_analyser.persistence.advice_ledger_repository import (
    MAX_LIST_LIMIT,
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)

# The aggregation reads at most this many most-recent scored calls (the ledger's
# own list cap). A personal advisor accrues calls slowly, so this is years of
# history; when it is reached the record is over the most-recent window and the
# response says so.
AGGREGATION_CAP = MAX_LIST_LIMIT

# Maximum scored calls returned inline in one `recent` page (ADR-0046).
MAX_RECENT_CALLS = 100

GET_TRACK_RECORD_DESCRIPTION = (
    "Read the advisor's own live track record (ADR-0075): how its past "
    "recommendations turned out against realized price, scored path-dependently "
    "(did the stop or a target hit first). Returns {track_record, recent, "
    "partial_reason, message, total_available, offset, returned}. The "
    "track_record carries the directional hit-rate and mean R-multiple, a "
    "calibration read (Brier score + reliability buckets: stated probability vs "
    "realized frequency), and a baseline comparison (hit-rate vs a buy-and-hold "
    "over-horizon alternative) — each with its sample size, and marked "
    "insufficient below a stated floor so a handful of calls is never presented "
    "as a conclusion. `recent` is the most-recent scored calls (symbol, "
    "direction, outcome, realized R). Optionally filter by `symbol`. This is a "
    "factual record of past accuracy — what happened and how it compares to the "
    "trivial baseline, nothing more, and no call to act. Bounded to "
    f"{MAX_RECENT_CALLS} recent calls per page (ADR-0046); page on with "
    "offset=offset+returned."
)


class ScoredCallOut(BaseModel):
    """One scored call, as the track-record surface reports it — a fact about a
    past recommendation's outcome, not a live call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    strategy_id: str
    direction: Literal["long", "short"]
    as_of_bar_ts: datetime
    horizon_bars: int
    conviction: float
    forecast_prob: float | None
    outcome_class: str
    realized_return: float | None
    realized_r: float | None
    directional_correct: bool | None
    scored_at: datetime | None


class GetTrackRecordResponse(BaseModel):
    """The aggregated record plus one honest page of the recent scored calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_record: TrackRecord
    recent: list[ScoredCallOut]
    partial_reason: Literal["too_large"] | None
    message: str | None
    total_available: int
    offset: int
    returned: int


def _to_out(row: AdviceLedgerEntry) -> ScoredCallOut:
    # A scored directional row: direction is long/short (never flat here).
    direction: Literal["long", "short"] = "long" if row.direction == "long" else "short"
    return ScoredCallOut(
        symbol=row.symbol,
        timeframe=row.timeframe,
        strategy_id=row.strategy_id,
        direction=direction,
        as_of_bar_ts=row.as_of_bar_ts,
        horizon_bars=row.horizon_bars,
        conviction=row.conviction,
        forecast_prob=row.forecast_prob,
        outcome_class=row.outcome_class or "",
        realized_return=row.realized_return,
        realized_r=row.realized_r,
        directional_correct=row.directional_correct,
        scored_at=row.scored_at,
    )


def _get_track_record_response(
    *,
    repository: AdviceLedgerRepository,
    symbol: str | None,
    offset: int,
    max_calls: int | None,
) -> GetTrackRecordResponse:
    """Body of the tool, factored out so the aggregation + paging contract is
    unit-testable without a live MCP server."""
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if max_calls is not None and max_calls < 1:
        raise ValueError(f"max_calls must be >= 1, got {max_calls}")

    scored = repository.list(directional=True, scored=True, symbol=symbol, limit=AGGREGATION_CAP)
    record = track_record(scored)

    page_size = MAX_RECENT_CALLS if max_calls is None else min(max_calls, MAX_RECENT_CALLS)
    total = len(scored)
    page = scored[offset : offset + page_size]
    returned = len(page)
    more_remain = offset + returned < total

    reason: Literal["too_large"] | None
    message: str | None
    if more_remain:
        reason = "too_large"
        message = (
            f"returned recent[{offset}:{offset + returned}] of {total} scored calls — "
            f"more remain; page on with offset={offset + returned} (page size "
            f"{page_size}, max {MAX_RECENT_CALLS}). The aggregate covers up to "
            f"{AGGREGATION_CAP} most-recent scored calls."
        )
    else:
        reason = None
        message = None

    return GetTrackRecordResponse(
        track_record=record,
        recent=[_to_out(row) for row in page],
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


def register_get_track_record(
    server: FastMCP,
    *,
    advice_ledger_repository: AdviceLedgerRepository,
) -> None:
    """Bind the `get_track_record` tool to `server`. The repository is captured by
    closure so the tool body keeps the declared parameters FastMCP introspects to
    build the input schema."""

    @server.tool(description=GET_TRACK_RECORD_DESCRIPTION)
    async def get_track_record(
        symbol: str | None = None,
        offset: int = 0,
        max_calls: int | None = None,
    ) -> GetTrackRecordResponse:
        # The repository read is a fast indexed SQLite query; still offloaded so
        # the event loop never blocks on disk I/O.
        return await asyncio.to_thread(
            lambda: _get_track_record_response(
                repository=advice_ledger_repository,
                symbol=symbol,
                offset=offset,
                max_calls=max_calls,
            )
        )


__all__ = [
    "AGGREGATION_CAP",
    "GET_TRACK_RECORD_DESCRIPTION",
    "MAX_RECENT_CALLS",
    "GetTrackRecordResponse",
    "ScoredCallOut",
    "_get_track_record_response",
    "register_get_track_record",
]
