"""`get_ohlcv` MCP tool (Plans 0006/0013; extracted Plan 0017).

Reads cached OHLCV bars through the `MarketDataProvider` Protocol and fetches any
missing bars from the upstream on a cache miss before returning. `as_of` is fixed
to `None` (live mode) at this boundary so the anti-lookahead guarantee from
ADR-0007 is preserved at the MCP seam.

The body is factored out as `_get_ohlcv_response` so the backfill paths are
unit-testable on a single event loop (no live MCP server needed for the event
assertions).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP

from market_analyser.api.backfill_response import GetOhlcvResponse
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

# Maximum bars returned inline in one page (ADR-0046). The harness caps each MCP
# tool result's token budget; the 2026-06-08 incident overflowed at 611 bars
# (~109k chars ≈ ~27k tokens, ~178 chars/bar). 400 keeps a worst-case full page
# (~72k chars ≈ ~18k tokens) comfortably below that overflow point while staying
# a single round-trip for most recent-window reads. Centralized + pinned by a
# test against a realistic per-bar char size so a harness change is a one-line
# retune (ADR-0046 §Negative).
MAX_OHLCV_BARS = 400

# The fetch-failure reasons that take precedence over the paging `too_large`
# label when both apply (incomplete data is the more important signal; the
# paging hint then rides in `message`).
_FetchFailureReason = Literal[
    "rate_limited",
    "upstream_unavailable",
    "unknown_symbol",
    "history_exceeded",
    "backfill_async_pending",
]

# The tool docstrings are agent UX (ADR-0015): the agent reads these to decide
# whether get_ohlcv can populate the cache. Plan 0013 fixes the old "from the
# local cache" wording that made the agent treat get_ohlcv as cache-only.
GET_OHLCV_DESCRIPTION = (
    "Read OHLCV bars for one symbol over a [start, end] window. Reads the local "
    "cache and fetches any missing bars from the upstream (Yahoo) on a cache "
    "miss before returning, so this tool populates the cache itself — no separate "
    "step is needed. Returns {bars, partial_reason, message}: partial_reason is "
    "null on full success, or a typed reason (rate_limited | upstream_unavailable "
    "| unknown_symbol | history_exceeded) when only some gaps could be filled or the "
    "window reaches past the timeframe's available history. Set backfill_async="
    "true to return whatever is already cached immediately and run the fetch in "
    "the background (partial_reason='backfill_async_pending'); progress then "
    "arrives on the event stream as ohlcv.backfilled / ohlcv.backfill_failed. "
    f"The inline result is bounded to {MAX_OHLCV_BARS} bars per page: when the "
    "window holds more, partial_reason='too_large' and total_available/offset/"
    "returned tell you how to page (call again with offset=returned) — the cache "
    "still holds the whole window, only the reply is sliced. "
    f"Live-mode only; supported timeframes: {supported_timeframes_label()}."
)


def _paginate(
    bars: Sequence[Bar],
    *,
    offset: int,
    max_bars: int | None,
    base_reason: _FetchFailureReason | None,
    base_message: str | None,
) -> GetOhlcvResponse:
    """Slice `bars` to one inline page and build the honest `GetOhlcvResponse`.

    `total_available` is always the FULL series length (paging never shrinks the
    cache — ADR-0046). The page is `bars[offset : offset + page_size]` where
    `page_size = min(max_bars or MAX_OHLCV_BARS, MAX_OHLCV_BARS)`. When more bars
    remain past this page, `partial_reason` becomes `"too_large"` unless a
    fetch-failure reason already occupies it (that signal wins; the paging hint
    then rides in `message` so neither is lost)."""
    page_size = MAX_OHLCV_BARS if max_bars is None else min(max_bars, MAX_OHLCV_BARS)
    total = len(bars)
    page = list(bars[offset : offset + page_size])
    returned = len(page)
    more_remain = offset + returned < total

    reason: _FetchFailureReason | Literal["too_large"] | None
    if base_reason is not None:
        reason = base_reason
        message = base_message
        if more_remain:
            hint = (
                f"returned bars[{offset}:{offset + returned}] of {total}; "
                f"page on with offset={offset + returned}"
            )
            message = f"{base_message} ({hint})" if base_message else hint
    elif more_remain:
        reason = "too_large"
        message = (
            f"returned bars[{offset}:{offset + returned}] of {total} total — more "
            f"remain; page on with offset={offset + returned} "
            f"(page size {page_size}, max {MAX_OHLCV_BARS}), or narrow the window."
        )
    else:
        reason = None
        message = base_message

    return GetOhlcvResponse(
        bars=page,
        partial_reason=reason,
        message=message,
        total_available=total,
        offset=offset,
        returned=returned,
    )


async def _get_ohlcv_response(
    *,
    provider: MarketDataProvider,
    coordinator: BackfillCoordinator | None,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    backfill_async: bool,
    offset: int = 0,
    max_bars: int | None = None,
) -> GetOhlcvResponse:
    """Body of the `get_ohlcv` tool, factored out so the backfill paths are unit-
    testable on a single event loop (no live MCP server needed for the event
    assertions). Sync mode preserves today's fetch-on-miss behaviour; the returned
    payload is bounded to one page (ADR-0046) while the cache keeps the full
    window."""
    # Validate at the MCP boundary like backfill_ohlcv does — bad input must
    # raise here, not slip into the async path where it would publish a
    # `started` event and then die without a `failed` (leaving the spinner stuck).
    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(start, end)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if max_bars is not None and max_bars < 1:
        raise ValueError(f"max_bars must be >= 1, got {max_bars}")
    if backfill_async:
        if coordinator is None:
            raise ValueError("backfill_async=true requires a cache-coverage-capable provider")
        cov = coordinator.coverage(symbol, timeframe, start, end)
        if not cov.gaps:
            # Cache already complete — return it, schedule nothing, publish nothing.
            return _paginate(
                cov.cached, offset=offset, max_bars=max_bars, base_reason=None, base_message=None
            )
        coordinator.schedule(symbol, timeframe, start, end)
        return _paginate(
            cov.cached,
            offset=offset,
            max_bars=max_bars,
            base_reason="backfill_async_pending",
            base_message=(
                "returned cached bars; a background backfill was scheduled — watch "
                "ohlcv.backfilled / ohlcv.backfill_failed on the event stream"
            ),
        )
    # Sync mode (default): fetch-on-miss, offloaded so it never blocks the loop.
    # With a coverage-capable provider, surface partial failures (some gaps
    # fetched, some failed) instead of failing loud; else fall back to the plain
    # fetch (legacy / coverage-less stub providers).
    if coordinator is not None:
        result = await asyncio.to_thread(
            coordinator.get_ohlcv_with_status, symbol, timeframe, start, end
        )
        return _paginate(
            result.bars,
            offset=offset,
            max_bars=max_bars,
            base_reason=result.partial_reason,
            base_message=result.message,
        )
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end)
    return _paginate(bars, offset=offset, max_bars=max_bars, base_reason=None, base_message=None)


def register_get_ohlcv(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    backfill_coordinator: BackfillCoordinator | None,
) -> None:
    """Bind the `get_ohlcv` tool to `server`. The provider and coordinator are
    captured by closure so the tool body keeps the declared parameters FastMCP
    introspects to build the input schema."""

    @server.tool(description=GET_OHLCV_DESCRIPTION)
    async def get_ohlcv(
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        backfill_async: bool = False,
        offset: int = 0,
        max_bars: int | None = None,
    ) -> GetOhlcvResponse:
        return await _get_ohlcv_response(
            provider=provider,
            coordinator=backfill_coordinator,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            backfill_async=backfill_async,
            offset=offset,
            max_bars=max_bars,
        )


__all__ = [
    "GET_OHLCV_DESCRIPTION",
    "MAX_OHLCV_BARS",
    "_get_ohlcv_response",
    "_paginate",
    "register_get_ohlcv",
]
