"""`get_metric_series` MCP tool — Plan 0055 phase 4 (ADR-0051 + ADR-0046).

The one generic read surface over every stored metric series: any registered
`series_id` (Fear & Greed history, dominance/total-mcap accrual, and every
series Plans 0056/0057 add) pages out through this single tool — no per-series
tool proliferation.

Delivery follows ADR-0046: the inline payload is capped at
`MAX_METRIC_POINTS`; a larger result returns the first page with the typed
`partial_reason="too_large"` and a message that names the total and the next
offset. Paging is deterministic offset/limit over the ts-ascending stored
series — purely response-shaping, never a different read.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.metric_series import registered_series
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

# Maximum points returned inline in one page (ADR-0046). A serialized point is
# ~35 chars ({"ts": 1716544000, "value": 52.3}), so a worst-case full page is
# ~70k chars (~18k tokens) — the same budget MAX_OHLCV_BARS targets (400 bars at
# ~178 chars/bar = ~72k chars), comfortably under the harness per-result cap.
MAX_METRIC_POINTS = 2000

# Upper read bound when `end` is omitted: 9999-12-31T23:59:59Z. A fixed constant
# (not a wall-clock read) so the same call always reads the same window.
_MAX_TS = 253_402_300_799

GET_METRIC_SERIES_DESCRIPTION = (
    "Read a stored metric time series (ADR-0051): points of one registered "
    "series_id over an inclusive [start, end] epoch-second window, sorted by ts "
    "ascending. Returns {series_id, points: [{ts, value}], partial_reason, "
    "message, total_available, offset, returned}. The inline result is bounded "
    f"to {MAX_METRIC_POINTS} points per page: when the window holds more, "
    "partial_reason='too_large' and total_available/offset/returned tell you how "
    "to page (call again with offset=offset+returned) — paging never changes "
    "what is stored, only the reply slice. Unknown series ids are rejected with "
    "the registered list. Registered series: " + ", ".join(registered_series()) + "."
)


class MetricPointOut(BaseModel):
    """One serialized point: UTC epoch seconds + the scalar value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: int
    value: float


class GetMetricSeriesResponse(BaseModel):
    """One page of a stored series plus the honest paging envelope (ADR-0046)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    points: list[MetricPointOut]
    partial_reason: Literal["too_large"] | None
    message: str | None
    total_available: int
    offset: int
    returned: int


def _get_metric_series_response(
    *,
    store: MetricPointsRepository,
    series_id: str,
    start: int,
    end: int | None,
    offset: int,
    max_points: int | None,
) -> GetMetricSeriesResponse:
    """Body of the tool, factored out so the paging contract is unit-testable
    without a live MCP server. Raises `UnknownMetricSeriesError` (a ValueError)
    for an unregistered id — the repository boundary check, surfaced as-is."""
    if start < 0:
        raise ValueError(f"start must be >= 0, got {start}")
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if max_points is not None and max_points < 1:
        raise ValueError(f"max_points must be >= 1, got {max_points}")
    effective_end = _MAX_TS if end is None else end
    series = store.range(series_id, start, effective_end)

    page_size = MAX_METRIC_POINTS if max_points is None else min(max_points, MAX_METRIC_POINTS)
    total = len(series)
    page = series[offset : offset + page_size]
    returned = len(page)
    more_remain = offset + returned < total

    reason: Literal["too_large"] | None
    message: str | None
    if more_remain:
        reason = "too_large"
        message = (
            f"returned points[{offset}:{offset + returned}] of {total} total — more "
            f"remain; page on with offset={offset + returned} "
            f"(page size {page_size}, max {MAX_METRIC_POINTS}), or narrow the window."
        )
    else:
        reason = None
        message = None

    return GetMetricSeriesResponse(
        series_id=series_id,
        points=[MetricPointOut(ts=p.ts, value=p.value) for p in page],
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


def register_get_metric_series(
    server: FastMCP,
    *,
    metric_points_repository: MetricPointsRepository,
) -> None:
    """Bind the `get_metric_series` tool to `server`. The repository is captured
    by closure so the tool body keeps the declared parameters FastMCP
    introspects to build the input schema."""

    @server.tool(description=GET_METRIC_SERIES_DESCRIPTION)
    async def get_metric_series(
        series_id: str,
        start: int = 0,
        end: int | None = None,
        offset: int = 0,
        max_points: int | None = None,
    ) -> GetMetricSeriesResponse:
        # The repository read is a fast indexed SQLite query; still offloaded
        # so the event loop never blocks on disk I/O.
        return await asyncio.to_thread(
            lambda: _get_metric_series_response(
                store=metric_points_repository,
                series_id=series_id,
                start=start,
                end=end,
                offset=offset,
                max_points=max_points,
            )
        )


__all__ = [
    "GET_METRIC_SERIES_DESCRIPTION",
    "MAX_METRIC_POINTS",
    "GetMetricSeriesResponse",
    "MetricPointOut",
    "_get_metric_series_response",
    "register_get_metric_series",
]
