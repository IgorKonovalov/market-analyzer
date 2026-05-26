"""MCP-tool response shapes for the OHLCV/backfill tools (Plan 0013 phase 2).

`get_ohlcv` changes from a bare `list[Bar]` to `GetOhlcvResponse` so partial
failures and async-backfill state are observable to the agent without raising.
`backfill_ohlcv` returns `BackfillOhlcvResponse`. Both are validated at the MCP
boundary like every other tool payload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.api.events import GapWindow
from market_analyser.data.types import Bar


class GetOhlcvResponse(BaseModel):
    """`get_ohlcv` result. `partial_reason` is `None` on full success; a typed
    failure reason when some gaps could not be fetched; or
    `"backfill_async_pending"` when `backfill_async=true` scheduled a background
    fetch and returned whatever was already cached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bars: list[Bar]
    partial_reason: (
        Literal[
            "rate_limited",
            "upstream_unavailable",
            "unknown_symbol",
            "backfill_async_pending",
        ]
        | None
    ) = None
    message: str | None = None


class BackfillOhlcvResponse(BaseModel):
    """`backfill_ohlcv` result. `started` is `True` with the gap windows when a
    background fetch was scheduled, or `False` with an empty `gaps` list when the
    cache already covers the requested window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    started: bool
    gaps: list[GapWindow]
    message: str


__all__ = ["BackfillOhlcvResponse", "GetOhlcvResponse"]
