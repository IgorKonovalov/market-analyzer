"""`news_for` MCP tool — Plan 0010 phase 3.

Exposes recent RSS news headlines to the agent, optionally with a per-headline
VADER sentiment score. Validates the request at the MCP boundary with an
`extra="forbid"` Pydantic model, then dispatches through the `MarketDataProvider`
Protocol — the tool never imports the news adapter directly (ADR-0007).

`ResilientHttpClient` + `feedparser` + VADER are synchronous and can block; the
provider call is offloaded with `asyncio.to_thread` so a slow feed cannot stall
the event loop (Plan 0009 phase 2 pattern).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.data.provider import MarketDataProvider


class NewsForInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str | None = None
    window: Literal["1h", "4h", "24h", "7d"] = "24h"
    limit: int = Field(50, ge=1, le=100)
    with_sentiment: bool = False

    @field_validator("symbol")
    @classmethod
    def _symbol_not_blank(cls, value: str | None) -> str | None:
        # None = unfiltered (all feeds). An empty/blank string is a malformed
        # filter, not a request for everything — reject it loudly.
        if value is not None and not value.strip():
            raise ValueError("symbol must be non-empty when provided (use null for unfiltered)")
        return value


def register_news_for(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `news_for` tool to `server`. The provider is captured by closure
    so the tool body keeps its single declared parameter (FastMCP introspects it
    to build the input schema)."""

    @server.tool(
        description=(
            "Fetch recent news headlines for a symbol (or across all feeds when "
            "`symbol` is null) from a curated set of free RSS feeds (CoinDesk, "
            "CoinTelegraph, Yahoo Finance, MarketWatch, CNBC). Returns up to "
            "`limit` items newest-first under `items`, each with title, url, "
            "published_at, source. Set `with_sentiment=true` to attach a "
            "per-headline VADER `compound_sentiment` in [-1, 1] (slower — it "
            "scores every item). `window` is one of 1h/4h/24h/7d. Symbol "
            "filtering is a whole-word token match (BTC matches 'BTC ETF', not "
            "'BTCUSD'); long company names may be missed (no name expansion). "
            "Results are wall-clock-sensitive — no historical replay."
        ),
    )
    async def news_for(params: NewsForInput) -> dict[str, Any]:
        items = await asyncio.to_thread(
            provider.get_news,
            symbol=params.symbol,
            window=params.window,
            limit=params.limit,
            with_sentiment=params.with_sentiment,
        )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = ["NewsForInput", "register_news_for"]
