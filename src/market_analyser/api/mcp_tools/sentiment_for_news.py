"""`sentiment_for_news` MCP tool — Plan 0010 phase 3.

Returns an aggregated news-sentiment summary for a symbol over a window. Validates
the request at the MCP boundary with an `extra="forbid"` Pydantic model, then
dispatches through the `MarketDataProvider` Protocol (ADR-0007). There is no
`as_of` parameter — sentiment is wall-clock-sensitive, so historical replay is not
offered at this boundary at all.

The provider call (RSS fetch + VADER scoring) is synchronous and offloaded with
`asyncio.to_thread` so a slow feed cannot stall the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.provider import MarketDataProvider


class SentimentForNewsInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    window: Literal["1h", "4h", "24h", "7d"] = "24h"


def register_sentiment_for_news(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `sentiment_for_news` tool to `server`. The provider is captured by
    closure so the tool body keeps its single declared parameter."""

    @server.tool(
        description=(
            "Summarise news sentiment for a symbol over a window by running VADER "
            "over each recent headline and aggregating. Returns `score` (mean "
            "compound in [-1, 1]), `window`, `source` ('rss-vader'), a `breakdown` "
            "of positive/negative/neutral headline counts, and `queried_at`. No "
            "news in the window returns score 0.0 with an all-zero breakdown "
            "(zero, not unknown). `window` is one of 1h/4h/24h/7d. "
            "Wall-clock-sensitive — no historical replay."
        ),
    )
    async def sentiment_for_news(params: SentimentForNewsInput) -> dict[str, Any]:
        sample = await asyncio.to_thread(
            provider.get_sentiment,
            symbol=params.symbol,
            window=params.window,
        )
        return {
            "score": sample.score,
            "window": sample.window,
            "source": sample.source,
            "breakdown": sample.breakdown,
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = ["SentimentForNewsInput", "register_sentiment_for_news"]
