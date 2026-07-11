"""Prediction-market MCP tools — Plan 0040 phase 2 (ADR-0041, ADR-0031).

Two read-only tools over the `PredictionMarketSource` registry (ADR-0031),
selecting the default source by name:

- `search_prediction_markets` — free-text search returning matching markets with
  their current outcome odds (implied probabilities);
- `prediction_market_odds` — one market's outcomes + implied probabilities by id.

Charter-safe (ADR-0041 / ADR-0015): odds are **conditions/facts** — a
money-weighted probability of a discrete event, which the analyst and forecaster
may consume and the advisor may weigh — never a recommendation. These tools report
odds; they place no order, hold no key, and phrase no buy/sell/exit call (a test
pins the output free of advice language). Every output carries `queried_at` + the
`source` identity as provenance.

On failure both tools return a structured `{… , error, message}` — the typed-error
courtesy `scan_wallet` / `quote_for` adopted — with a precise `error` reason:
`not_found` (unknown market id), `rate_limited` / `upstream_unavailable` (throttle /
outage), or `malformed_response` (upstream shape drift). The registry keeps the
source swappable — a later config could select another prediction-market source by
name without touching these tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.polymarket import PolymarketError, UnknownMarketError
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.sources import PredictionMarketSource

# The default prediction-market source for this plan (ADR-0041). The registry seam
# keeps it swappable — a later config could choose another source by name.
_DEFAULT_SOURCE = "polymarket"

_MAX_SEARCH_LIMIT = 100

SEARCH_PREDICTION_MARKETS_DESCRIPTION = (
    "Search prediction markets by free text and get each match with its current "
    "odds. Returns {query, markets, count, queried_at, source, error, message}: "
    "markets is a list of {market_id, question, outcomes, closed, closes_at, "
    "volume_usd, liquidity_usd, queried_at, source}, where outcomes is a list of "
    "{label, implied_probability}. implied_probability is the market-implied "
    "probability of that outcome in [0, 1] (a prediction market trades each "
    "outcome between 0 and 1, and the price IS the money-weighted probability) - "
    "the outcomes of a binary market sum to about 1. volume_usd / liquidity_usd "
    "are honest-uncertainty hints: a thin-book market's probability is noisier and "
    "must not be read as ground truth. Facts only (a market-implied probability is "
    "a condition, never a call - no buy/sell/hold advice). limit caps the results "
    "(default 20). On failure markets is null and error is a typed reason "
    "(rate_limited / upstream_unavailable / malformed_response). Data from "
    "Polymarket public endpoints (no account, no funds)."
)

PREDICTION_MARKET_ODDS_DESCRIPTION = (
    "Get one prediction market's current outcomes and implied probabilities by "
    "market_id (from search_prediction_markets). Returns {market, queried_at, "
    "source, error, message}: market is {market_id, question, outcomes, closed, "
    "closes_at, volume_usd, liquidity_usd, queried_at, source} with outcomes a "
    "list of {label, implied_probability in [0, 1]}. The price IS the "
    "money-weighted probability of the outcome; a binary market's outcomes sum to "
    "about 1. Facts only - a market-implied probability is a condition, never a "
    "buy/sell/hold call. On failure market is null and error is a typed reason: "
    "not_found (no such market_id), rate_limited, upstream_unavailable, or "
    "malformed_response. Data from Polymarket public endpoints (no account, no "
    "funds)."
)


class SearchPredictionMarketsInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected; `query` is free text, `limit`
    bounds the result count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, description="Free-text market search, e.g. 'bitcoin'")
    limit: int = Field(
        default=20,
        ge=1,
        le=_MAX_SEARCH_LIMIT,
        description="Maximum number of markets to return (default 20)",
    )


class PredictionMarketOddsInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected; `market_id` names one market."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_id: str = Field(min_length=1, description="Market id from search_prediction_markets")


def register_prediction_market_tools(
    server: FastMCP,
    *,
    prediction_market_sources: Mapping[str, PredictionMarketSource],
) -> None:
    """Bind `search_prediction_markets` + `prediction_market_odds` to `server`.
    The registry is captured by closure and the default source selected by name
    (ADR-0031); the tools never import a concrete adapter for *fetching* — only
    the adapter's typed error classes, to map them to a precise reason."""
    source = prediction_market_sources[_DEFAULT_SOURCE]

    @server.tool(description=SEARCH_PREDICTION_MARKETS_DESCRIPTION)
    async def search_prediction_markets(params: SearchPredictionMarketsInput) -> dict[str, Any]:
        try:
            markets = await asyncio.to_thread(
                source.search_markets, params.query, limit=params.limit
            )
        except UpstreamDataError as err:
            return _search_error(failure_reason(err), err)
        except (PolymarketError, ValidationError) as err:
            return _search_error("malformed_response", err)
        return {
            "query": params.query,
            "markets": [market.model_dump(mode="json") for market in markets],
            "count": len(markets),
            "queried_at": _now_iso(),
            "source": _DEFAULT_SOURCE,
            "error": None,
            "message": None,
        }

    @server.tool(description=PREDICTION_MARKET_ODDS_DESCRIPTION)
    async def prediction_market_odds(params: PredictionMarketOddsInput) -> dict[str, Any]:
        try:
            market = await asyncio.to_thread(source.fetch_market, params.market_id)
        except UnknownMarketError as err:
            return _odds_error("not_found", err)
        except UpstreamDataError as err:
            return _odds_error(failure_reason(err), err)
        except (PolymarketError, ValidationError) as err:
            return _odds_error("malformed_response", err)
        return {
            "market": market.model_dump(mode="json"),
            "queried_at": _now_iso(),
            "source": _DEFAULT_SOURCE,
            "error": None,
            "message": None,
        }


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _search_error(reason: str, err: Exception) -> dict[str, Any]:
    return {
        "query": None,
        "markets": None,
        "count": None,
        "queried_at": _now_iso(),
        "source": _DEFAULT_SOURCE,
        "error": reason,
        "message": str(err),
    }


def _odds_error(reason: str, err: Exception) -> dict[str, Any]:
    return {
        "market": None,
        "queried_at": _now_iso(),
        "source": _DEFAULT_SOURCE,
        "error": reason,
        "message": str(err),
    }


__all__ = [
    "PREDICTION_MARKET_ODDS_DESCRIPTION",
    "SEARCH_PREDICTION_MARKETS_DESCRIPTION",
    "PredictionMarketOddsInput",
    "SearchPredictionMarketsInput",
    "register_prediction_market_tools",
]
