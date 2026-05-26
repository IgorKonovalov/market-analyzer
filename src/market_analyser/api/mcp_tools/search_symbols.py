"""`search_symbols` MCP tool — Plan 0024 phase 2.

Resolves a loose, free-text name or ticker to fetchable Yahoo-native symbols for
the agent. Validates the request at the MCP boundary with an `extra="forbid"`
Pydantic model (so a stray key fails loudly), then dispatches through the
`MarketDataProvider` Protocol — the tool never imports the adapter directly
(ADR-0007).

This is the recovery path for `get_ohlcv`'s `unknown_symbol` failure (Plan 0013):
when a name fails to fetch, call this, then retry `get_ohlcv` with a returned
`symbol`. Every returned symbol is in the OHLCV namespace by construction
(ADR-0026), so a returned symbol is directly chartable.

`ResilientHttpClient` is synchronous and `urllib`-based; the MCP transport is
async. The provider call is offloaded with `asyncio.to_thread` so a slow upstream
cannot stall the event loop (mirrors `screener_query`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.provider import MarketDataProvider


class SearchSymbolsInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str


def register_search_symbols(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `search_symbols` tool to `server`. The provider is captured by
    closure so the tool body keeps its single declared parameter (FastMCP
    introspects it to build the input schema)."""

    @server.tool(
        description=(
            "Resolve a loose or free-text name/ticker to fetchable symbols "
            "(e.g. 'bitcoin' or 'BTC' -> BTC-USD, Bitcoin USD, Cryptocurrency). "
            "Returns {results, queried_at}: results is a list of {symbol, name, "
            "exchange, quote_type} in upstream relevance order, where every "
            "`symbol` is directly fetchable by get_ohlcv. Use this as the "
            "recovery path when get_ohlcv reports unknown_symbol — call "
            "search_symbols, then retry get_ohlcv with a returned `symbol`. A "
            "zero-match query returns an empty results list (not an error). Data "
            "comes from Yahoo Finance's search endpoint (live; no as_of)."
        ),
    )
    async def search_symbols(params: SearchSymbolsInput) -> dict[str, Any]:
        results = await asyncio.to_thread(provider.search_symbols, params.query)
        return {
            "results": [info.model_dump() for info in results],
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = ["SearchSymbolsInput", "register_search_symbols"]
