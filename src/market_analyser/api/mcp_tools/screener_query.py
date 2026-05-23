"""`screener_query` MCP tool — Plan 0009 phase 2.

Exposes the TradingView screener to the agent. Validates the request at the MCP
boundary with a `extra="forbid"` Pydantic model (so a misspelled filter or stray
key fails loudly), then dispatches through the `MarketDataProvider` Protocol —
the tool never imports the screener adapter directly (ADR-0007).

`ResilientHttpClient` is synchronous and `urllib`-based; the MCP transport is
async. The provider call is offloaded with `asyncio.to_thread` so a slow upstream
cannot stall the event loop (Plan 0009 phase 2 done-when).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.provider import MarketDataProvider


class ScreenerQueryInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filters: dict[str, Any]
    market: Literal["america", "crypto", "forex", "egypt"] = "america"
    exchange: str | None = None
    limit: int = Field(50, ge=1, le=200)


def register_screener_query(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `screener_query` tool to `server`. The provider is captured by
    closure so the tool body keeps its single declared parameter (FastMCP
    introspects it to build the input schema)."""

    @server.tool(
        description=(
            "Screen a market universe for symbols matching indicator/price "
            "filters (e.g. RSI < 30 on US large-caps). Returns the matching rows "
            "with their indicator columns plus `queried_at`, the wall-clock time "
            "the screen ran. Results are wall-clock-sensitive — there is no "
            "historical replay (no as_of). `filters` is a dict keyed by column "
            'with operator sub-dicts, e.g. {"RSI": {"lt": 30}, '
            '"market_cap_basic": {"gte": 1e10}}; operators are '
            "lt/lte/gt/gte/eq/ne (a bare scalar means equality). Data comes from "
            "TradingView's public scanner (reverse-engineered; may change "
            "without notice)."
        ),
    )
    async def screener_query(params: ScreenerQueryInput) -> dict[str, Any]:
        rows = await asyncio.to_thread(
            provider.get_screener,
            params.filters,
            market=params.market,
            exchange=params.exchange,
            limit=params.limit,
        )
        return {
            "rows": [row.model_dump() for row in rows],
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = ["ScreenerQueryInput", "register_screener_query"]
