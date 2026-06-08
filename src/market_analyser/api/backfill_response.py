"""MCP-tool response shapes for the OHLCV/backfill tools (Plan 0013 phase 2).

`get_ohlcv` changes from a bare `list[Bar]` to `GetOhlcvResponse` so partial
failures and async-backfill state are observable to the agent without raising.
`backfill_ohlcv` returns `BackfillOhlcvResponse`. Both are validated at the MCP
boundary like every other tool payload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.data.types import Bar
from market_analyser.events import GapWindow


class GetOhlcvResponse(BaseModel):
    """`get_ohlcv` result. `partial_reason` is `None` on full success; a typed
    failure reason when some gaps could not be fetched; `"backfill_async_pending"`
    when `backfill_async=true` scheduled a background fetch and returned whatever
    was already cached; or `"too_large"` (ADR-0046) when the window holds more
    bars than fit in one inline page and `bars` is a bounded slice of the whole.

    `total_available`/`offset`/`returned` describe the paging window over the
    full cached series: `total_available` is the bar count of the *whole* window
    (never shrunk by paging — the cache itself always holds the full window),
    `offset` echoes the requested page start, and `returned` is `len(bars)` in
    this page. When `offset + returned < total_available` there are more bars to
    page (`partial_reason="too_large"` unless a fetch-failure reason already
    occupies the field, in which case `message` carries the paging hint)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bars: list[Bar]
    partial_reason: (
        Literal[
            "rate_limited",
            "upstream_unavailable",
            "unknown_symbol",
            "history_exceeded",
            "backfill_async_pending",
            "too_large",
        ]
        | None
    ) = None
    message: str | None = None
    total_available: int = 0
    offset: int = 0
    returned: int = 0


class BackfillOhlcvResponse(BaseModel):
    """`backfill_ohlcv` result. `started` is `True` with the gap windows when a
    background fetch was scheduled, or `False` with an empty `gaps` list when the
    cache already covers the requested window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    started: bool
    gaps: list[GapWindow]
    message: str


__all__ = ["BackfillOhlcvResponse", "GetOhlcvResponse"]
