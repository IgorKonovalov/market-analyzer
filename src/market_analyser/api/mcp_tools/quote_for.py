"""`quote_for` MCP tool — Plan 0019 phase 2.

Returns a live quote for one symbol (price, change %, previous close, day range,
52-week range, currency, market state, volume) for the agent. Validates the
request at the MCP boundary with an `extra="forbid"` Pydantic model — a stray key
fails loudly, and `as_of` is deliberately absent (a live quote has no replayable
history; the provider rejects an `as_of` argument anyway). Dispatches through the
`MarketDataProvider` Protocol; the tool never imports the adapter (ADR-0007).

A symbol the upstream does not carry comes back as a structured
`{quote: null, error: "unknown_symbol", message}` rather than a 500 — the same
typed-error courtesy the OHLCV path adopted in Plan 0013, extended here to every
`UpstreamDataError` (rate limits, outages) via the shared `failure_reason` map.

`ResilientHttpClient` is synchronous and `urllib`-based; the MCP transport is
async. The provider call is offloaded with `asyncio.to_thread` so a slow upstream
cannot stall the event loop (mirrors `search_symbols` / `screener_query`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.provider import MarketDataProvider

QUOTE_FOR_DESCRIPTION = (
    "Get a live quote for one symbol: price, change_pct, previous_close, day "
    "high/low, 52-week high/low, currency, market_state (REGULAR/PRE/POST/CLOSED) "
    "and volume. Returns {quote, error, message, queried_at}: quote is an object "
    "with those fields on success; on failure quote is null and error is a typed "
    "reason (e.g. 'unknown_symbol' for a symbol the source doesn't carry — recover "
    "via search_symbols, then retry — or 'rate_limited'/'upstream_unavailable'), "
    "with a human message. change_pct is derived from previous_close. Live and "
    "wall-clock-current: there is no as_of/historical replay (use get_ohlcv for "
    "historical price). Data from Yahoo Finance."
)


class QuoteForInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`); `as_of`
    is intentionally not a field — a live quote is not replayable (Plan 0019)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)


def register_quote_for(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `quote_for` tool to `server`. The provider is captured by closure
    so the tool body keeps its single declared parameter (FastMCP introspects it to
    build the input schema)."""

    @server.tool(description=QUOTE_FOR_DESCRIPTION)
    async def quote_for(params: QuoteForInput) -> dict[str, Any]:
        queried_at = datetime.now(tz=UTC).isoformat()
        try:
            quote = await asyncio.to_thread(provider.get_quote, params.symbol)
        except UpstreamDataError as err:
            return {
                "quote": None,
                "error": failure_reason(err),
                "message": str(err),
                "queried_at": queried_at,
            }
        return {
            "quote": quote.model_dump(mode="json"),
            "error": None,
            "message": None,
            "queried_at": queried_at,
        }


__all__ = ["QUOTE_FOR_DESCRIPTION", "QuoteForInput", "register_quote_for"]
