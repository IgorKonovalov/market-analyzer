"""`stocktwits_sentiment` MCP tool — Plan 0012 phase 3.

Returns labeled-sentiment counts for a symbol over a window, sourced from
StockTwits' explicit Bullish/Bearish post labels (no NLP model). Separate from
`sentiment_for_news` (Plan 0010) so the agent picks a sentiment source
explicitly. Validates the request at the MCP boundary with an `extra="forbid"`
model: a char-class `pattern` rejects punctuation like `AAPL$` (length checks
alone would not), and a `field_validator` upper-cases the symbol, echoed back in
the response. There is no `as_of` parameter — sentiment is wall-clock-sensitive.

Symbol is **pass-through**: the exact StockTwits ticker (`AAPL` for the stock,
`BTC.X` for crypto Bitcoin). A symbol StockTwits doesn't track surfaces as a
clear tool error, not an unhandled 500. The provider call is synchronous and
offloaded with `asyncio.to_thread` so a slow upstream cannot stall the loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.data import UnknownSymbolError
from market_analyser.data.provider import MarketDataProvider


class StockTwitsSentimentInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # pattern rejects punctuation like "AAPL$"; allows letters, "." (BRK.B / BTC.X) and "-".
    symbol: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z.\-]+$")
    window: Literal["1h", "4h", "24h", "7d"] = "24h"

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:  # echoed upper-cased in the response
        return v.upper()


def register_stocktwits_sentiment(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `stocktwits_sentiment` tool to `server`. The provider is captured by
    closure so the tool body keeps its single declared parameter."""

    @server.tool(
        description=(
            "Summarise StockTwits crowd sentiment for a symbol over a window by "
            "counting users' explicit Bullish/Bearish post labels (no NLP model). "
            "Returns `symbol` (upper-cased), `score` ((bullish - bearish) / labeled "
            "count, in [-1, 1]), `window`, `source` ('stocktwits'), a `breakdown` "
            "of positive/negative/neutral post counts, and `queried_at`. Pass the "
            "exact StockTwits ticker: a plain symbol for stocks (AAPL) and the "
            "'.X' suffix for crypto (BTC.X, ETH.X). Patchy coverage on small-caps "
            "returns an all-zero breakdown (neutral, not unknown); a symbol "
            "StockTwits does not track is an error. `window` is one of "
            "1h/4h/24h/7d. Wall-clock-sensitive — no historical replay."
        ),
    )
    async def stocktwits_sentiment(params: StockTwitsSentimentInput) -> dict[str, Any]:
        try:
            sample = await asyncio.to_thread(
                provider.get_sentiment,
                symbol=params.symbol,
                window=params.window,
                source="stocktwits",
            )
        except UnknownSymbolError as err:
            raise ValueError(
                f"symbol {params.symbol!r} is not tracked by StockTwits",
            ) from err
        return {
            "symbol": params.symbol,
            "score": sample.score,
            "window": sample.window,
            "source": sample.source,
            "breakdown": sample.breakdown,
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }


__all__ = ["StockTwitsSentimentInput", "register_stocktwits_sentiment"]
