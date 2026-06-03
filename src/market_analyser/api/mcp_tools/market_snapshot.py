"""`market_snapshot` MCP tool — Plan 0022 phase 3.

A global market overview built by fanning `get_quote` (Plan 0019) across a fixed
basket of major symbols — pure composition, not a new data source (ADR-0007). The
tool adds no data dependency beyond the quote Protocol method it already uses.

Per-symbol resilience: each quote is fetched independently and a failing symbol
degrades to a structured `{quote: null, error, message}` entry (the same typed
`failure_reason` vocabulary `quote_for` uses) while every other symbol still
returns — one bad symbol never fails the whole snapshot.

`get_quote` is a blocking `urllib`-based call; the basket is fetched concurrently
by offloading each call with `asyncio.to_thread` and gathering them, so eight
sequential upstream round-trips do not stall the event loop one-after-another.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.provider import MarketDataProvider

# The fixed v1 basket (Plan 0022 phase 3): S&P 500, NASDAQ, VIX, BTC, ETH,
# EUR/USD, SPY, GLD — Yahoo-native symbols, directly fetchable by get_quote. A
# tuple so the order is stable and the set cannot be mutated at runtime; a
# configurable basket is a documented follow-up (Plan 0022 open question).
MARKET_SNAPSHOT_BASKET: tuple[str, ...] = (
    "^GSPC",
    "^IXIC",
    "^VIX",
    "BTC-USD",
    "ETH-USD",
    "EURUSD=X",
    "SPY",
    "GLD",
)

MARKET_SNAPSHOT_DESCRIPTION = (
    "Get a point-in-time global market snapshot: live quotes for a fixed basket — "
    "S&P 500 (^GSPC), NASDAQ (^IXIC), VIX (^VIX), Bitcoin (BTC-USD), Ethereum "
    "(ETH-USD), EUR/USD (EURUSD=X), SPY, and GLD. Takes no arguments. Returns "
    "{quotes, queried_at}: quotes maps each basket symbol to {quote, error, "
    "message} — quote is the live quote object (price, change_pct, day range, "
    "etc.) on success, or null with a typed error reason ('unknown_symbol' / "
    "'rate_limited' / 'upstream_unavailable') and a human message if that symbol "
    "failed. One failing symbol does NOT fail the snapshot; the others still "
    "return. Live and wall-clock-current — there is no as_of/historical replay "
    "(use get_ohlcv for history). Data from Yahoo Finance."
)


class MarketSnapshotInput(BaseModel):
    """MCP-boundary input. Empty by design — the snapshot takes no parameters in
    v1 (the basket is a fixed constant), and `extra="forbid"` rejects any argument
    an agent supplies by mistake."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def register_market_snapshot(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `market_snapshot` tool to `server`. The provider is captured by
    closure; the empty `params` model is what FastMCP introspects to build the
    (argument-rejecting) input schema."""

    @server.tool(description=MARKET_SNAPSHOT_DESCRIPTION)
    async def market_snapshot(params: MarketSnapshotInput) -> dict[str, Any]:
        queried_at = datetime.now(tz=UTC).isoformat()
        entries = await asyncio.gather(
            *(_fetch_one(provider, symbol) for symbol in MARKET_SNAPSHOT_BASKET)
        )
        return {
            "quotes": {symbol: entry for symbol, entry in entries},
            "queried_at": queried_at,
        }


async def _fetch_one(provider: MarketDataProvider, symbol: str) -> tuple[str, dict[str, Any]]:
    """Fetch one basket symbol's quote, degrading a typed upstream failure to a
    structured `{quote: null, error, message}` entry instead of failing the whole
    snapshot."""
    try:
        quote = await asyncio.to_thread(provider.get_quote, symbol)
    except UpstreamDataError as err:
        return symbol, {"quote": None, "error": failure_reason(err), "message": str(err)}
    return symbol, {"quote": quote.model_dump(mode="json"), "error": None, "message": None}


__all__ = [
    "MARKET_SNAPSHOT_BASKET",
    "MARKET_SNAPSHOT_DESCRIPTION",
    "MarketSnapshotInput",
    "register_market_snapshot",
]
