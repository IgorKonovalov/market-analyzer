"""`bitcoin_market_pulse` MCP tool — Plan 0022.

Exposes a single-call crypto macro read (CoinGecko's free public API) to the
agent: BTC price + 24h change, BTC dominance, total market cap + 24h change, plus
a neutral structural `regime` descriptor. Dispatches through the
`MarketDataProvider` Protocol (`get_macro_context`); the tool never imports the
adapter directly (ADR-0007).

The reading is wall-clock-current (not historical) and market-level (not
per-symbol). `regime` is a structural *condition* (where capital is sitting), not
a buy/sell recommendation — the description says so, and the closed vocabulary is
enforced at the type level in the data layer (ADR-0027). The provider call is a
blocking HTTP fetch, so it is offloaded with `asyncio.to_thread` to keep the event
loop free (mirrors `crypto_fear_greed` / `quote_for`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.provider import MarketDataProvider

BITCOIN_MARKET_PULSE_DESCRIPTION = (
    "Get the current crypto macro picture in one call (CoinGecko, free public "
    "API): BTC price and 24h change, BTC dominance %, total crypto market cap and "
    "its 24h change, plus a neutral `regime` label describing market STRUCTURE "
    "(btc_led / alt_structure / risk_off_structure / neutral). Market defaults to "
    "crypto (the only value in v1). Returns {macro, queried_at}: macro holds the "
    "measurements above; queried_at is when this call ran. `regime` is a "
    "structural condition (where capital is concentrating), NOT a buy/sell or "
    "risk recommendation. The figures are a point-in-time read — there is no "
    "as_of/historical replay — and are cached briefly, so asking again within a "
    "minute may return the same values."
)


class BitcoinMarketPulseInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`). `market`
    is crypto-only in v1; it is accepted (defaulted) for forward-compat but no
    other value is valid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["crypto"] = "crypto"


def register_bitcoin_market_pulse(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `bitcoin_market_pulse` tool to `server`. The provider is captured
    by closure so the tool body keeps its single declared parameter (FastMCP
    introspects it to build the input schema)."""

    @server.tool(description=BITCOIN_MARKET_PULSE_DESCRIPTION)
    async def bitcoin_market_pulse(params: BitcoinMarketPulseInput) -> dict[str, Any]:
        macro = await asyncio.to_thread(provider.get_macro_context, params.market)
        return {
            "macro": macro.model_dump(mode="json"),
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = [
    "BITCOIN_MARKET_PULSE_DESCRIPTION",
    "BitcoinMarketPulseInput",
    "register_bitcoin_market_pulse",
]
