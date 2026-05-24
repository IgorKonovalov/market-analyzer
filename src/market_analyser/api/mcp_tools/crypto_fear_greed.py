"""`crypto_fear_greed` MCP tool — Plan 0011.

Exposes the current crypto Fear & Greed reading (Alternative.me) to the agent as
a single-call macro-context check. Takes no arguments — the boundary input model
is empty with `extra="forbid"`, so any supplied argument is rejected before the
tool body runs. Dispatches through the `MarketDataProvider` Protocol; the tool
never imports the adapter directly (ADR-0007).

The reading is wall-clock-current (not historical), market-wide (not per-symbol),
and one-day-cadence — asking twice in an hour returns the same value. The
provider call is synchronous (a blocking HTTP fetch), so it is offloaded with
`asyncio.to_thread` to keep the event loop free (Plan 0009 phase 2 pattern).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.provider import MarketDataProvider


class CryptoFearGreedInput(BaseModel):
    """MCP-boundary input. Empty by design — the tool takes no parameters in v1,
    and `extra="forbid"` rejects any argument an agent supplies by mistake."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def register_crypto_fear_greed(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `crypto_fear_greed` tool to `server`. The provider is captured by
    closure; the empty `params` model is what FastMCP introspects to build the
    (argument-rejecting) input schema."""

    @server.tool(
        description=(
            "Get the current crypto Fear & Greed index (Alternative.me): a single "
            "0-100 value with a label (Extreme Fear / Fear / Neutral / Greed / "
            "Extreme Greed). Takes no arguments. Returns `value`, `classification`, "
            "`published_at` (when the index was published upstream), `queried_at`, "
            "and `source`. The reading is market-wide (not per-symbol), wall-clock-"
            "current (no historical replay), and updates roughly once a day — "
            "asking again within the hour returns the same value."
        ),
    )
    async def crypto_fear_greed(params: CryptoFearGreedInput) -> dict[str, Any]:
        sample = await asyncio.to_thread(provider.get_market_sentiment, market="crypto")
        return {
            "value": sample.value,
            "classification": sample.classification,
            "published_at": sample.published_at.isoformat(),
            "queried_at": datetime.now(tz=UTC).isoformat(),
            "source": sample.source,
        }


__all__ = ["CryptoFearGreedInput", "register_crypto_fear_greed"]
