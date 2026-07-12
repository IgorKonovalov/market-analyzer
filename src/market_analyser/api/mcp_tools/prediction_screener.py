"""`find_convergence_opportunities` MCP tool — Plan 0078 phase 2 (ADR-0041/0029).

A read-only tool over the `PredictionMarketSource` registry (ADR-0031): it searches
the selected source for a query, runs the convergence screener
(`prediction/convergence.py`) over the returned markets, and returns the ranked
near-decided opportunities — each carrying its gross `implied_return_if_right`
**and** its full risk context (resolution risk, liquidity caution, capital-lockup
note, time to resolution).

Charter-safe (ADR-0029 / ADR-0041 / ADR-0072): it reports opportunities **with their
risks attached, as facts**, never a buy call. It signs nothing, holds no key, moves
no funds — the buying is the deferred ADR-0072 execution pillar. Every opportunity's
`implied_return_if_right` is **gross of the resolution tail** (no blended EV is
computed — the edge and the tail are surfaced separately, never fused).

The flow mirrors the closed-bar / publish-after-result discipline of the other
event-publishing tools:

    validate inputs (query / filter knobs — at the FastMCP boundary)
        -> now = datetime.now(UTC)          (the only wall-clock read)
        -> markets = source.search_markets(query)
        -> screen_convergence(markets, params, now)
        -> bound to one page (ADR-0046)
        -> bus.publish("prediction.screen_completed v1", {page})   [only if non-empty]

Results are bounded per ADR-0046: one page of at most `MAX_CONVERGENCE_OPPORTUNITIES`,
with `total_available` / `offset` / `returned` and `partial_reason="too_large"` when
more remain. On failure the opportunities list is null and `error` is a typed reason
(`rate_limited` / `upstream_unavailable` / `malformed_response`).

The `prediction.screen_completed v1` envelope publishes **exactly once**, strictly
after the page is built, and **only when the screen yields at least one opportunity**
— any raise/return above the publish leaves the bus untouched, and an empty screen
(no markets, or none passing the filters) has nothing to show, so it publishes
nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.polymarket import PolymarketError
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.sources import PredictionMarketSource
from market_analyser.data.types import PredictionMarket
from market_analyser.events import EventBus, PredictionScreenCompletedPayloadV1
from market_analyser.prediction import (
    ConvergenceOpportunity,
    ConvergenceParams,
    screen_convergence,
)

# The default prediction-market source for this plan (ADR-0041). The registry seam
# keeps it swappable — a later config could choose another source by name.
_DEFAULT_SOURCE = "polymarket"

# Maximum opportunities returned inline in one page (ADR-0046); pinned by a test.
MAX_CONVERGENCE_OPPORTUNITIES = 50

# Upper bound on markets pulled from the source for one screen (a courtesy cap on
# the upstream fan-out; the screener then filters this set down).
_MAX_SEARCH_LIMIT = 100

FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION = (
    "Screen prediction markets matching a query for CONVERGENCE opportunities — "
    "markets nearing resolution whose top outcome is near-certain, where a price "
    "converging to 1.00 leaves a few percent of implied upside. Returns ranked "
    "opportunities {market_id, question, outcome_label, implied_probability, "
    "implied_return_if_right, time_to_resolution, capital_lockup_note, "
    "liquidity_caution, resolution_risk {level, reasons}, volume_usd, closes_at, "
    "queried_at, source, market_url}. market_url is the canonical Polymarket page for "
    "the market (provenance/citation — where the public fact lives, never a trade "
    "control), null when the source gives no usable slug. implied_return_if_right = "
    "(1 - price) / price is GROSS of "
    "the resolution tail — it is NOT expected value; the tail lives in "
    "resolution_risk (a LABELED HEURISTIC over multi-outcome wording, thin/unknown "
    "book, and dispute-prone question terms — never a guarantee), liquidity_caution, "
    "and capital_lockup_note (market close is not settlement — UMA resolution can lag "
    "or be disputed, locking capital). IMPORTANT: these are facts with their risks "
    "attached, never a call — this reports conditions and never tells you to take a "
    "position; it signs nothing and moves no funds. Filter knobs: max_days_to_close "
    "(window, default 7), min_confidence (probability floor, default 0.90), "
    "thin_book_volume_usd (thin-book threshold, default 50000). Results are bounded "
    f"to {MAX_CONVERGENCE_OPPORTUNITIES} per page: when more remain "
    "partial_reason='too_large' and total_available/offset/returned tell you how to "
    "page (call again with offset=returned). On failure opportunities is null and "
    "error is a typed reason (rate_limited / upstream_unavailable / "
    "malformed_response). Data from Polymarket public endpoints (no account, no funds)."
)


class FindConvergenceOpportunitiesInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected. The filter ranges mirror
    `ConvergenceParams` so a violation is caught here, before any fetch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, description="Free-text market search, e.g. 'election'")
    max_days_to_close: float = Field(
        default=7.0,
        gt=0.0,
        le=365.0,
        description="Only markets closing within this many days (default 7)",
    )
    min_confidence: float = Field(
        default=0.90,
        ge=0.5,
        le=1.0,
        description="Top-outcome implied-probability floor (default 0.90)",
    )
    thin_book_volume_usd: float = Field(
        default=50_000.0,
        ge=0.0,
        description="Reported volume below which a book is flagged thin (default 50000)",
    )
    search_limit: int = Field(
        default=50,
        ge=1,
        le=_MAX_SEARCH_LIMIT,
        description="Markets to pull from the source before screening (default 50)",
    )
    offset: int = Field(default=0, ge=0, description="Page offset into the ranked opportunities")
    max_results: int | None = Field(
        default=None,
        ge=1,
        description=f"Page size (default and cap {MAX_CONVERGENCE_OPPORTUNITIES})",
    )


async def _screen_response(
    *,
    prediction_market_sources: Mapping[str, PredictionMarketSource],
    event_bus: EventBus,
    params: FindConvergenceOpportunitiesInput,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Body of the tool, factored out so it is unit-testable without a live MCP
    server. `now` is injectable so tests run on a fixed instant (the `technical_read`
    precedent). Publishes the `prediction.screen_completed v1` envelope exactly once,
    after the page is built, and only when the screen yields ≥1 opportunity."""
    source = prediction_market_sources[_DEFAULT_SOURCE]
    try:
        markets: Sequence[PredictionMarket] = await asyncio.to_thread(
            source.search_markets, params.query, limit=params.search_limit
        )
    except UpstreamDataError as err:
        return _error(params, failure_reason(err), err)
    except (PolymarketError, ValidationError) as err:
        return _error(params, "malformed_response", err)

    resolved_now = now if now is not None else datetime.now(UTC)
    opportunities = screen_convergence(
        markets,
        params=ConvergenceParams(
            max_time_to_close=timedelta(days=params.max_days_to_close),
            min_confidence=params.min_confidence,
            thin_book_volume_usd=params.thin_book_volume_usd,
        ),
        now=resolved_now,
    )

    page, response = _paginate(params, opportunities, queried_at=resolved_now)

    # Publish AFTER the page is built, and only when there is something to show — an
    # empty screen leaves the bus untouched (every return above this line already has).
    if page:
        event_bus.publish(
            "prediction.screen_completed",
            PredictionScreenCompletedPayloadV1(
                query=params.query,
                opportunities=page,
                queried_at=resolved_now,
                source=_DEFAULT_SOURCE,
            ),
        )
    return response


def _paginate(
    params: FindConvergenceOpportunitiesInput,
    opportunities: Sequence[ConvergenceOpportunity],
    *,
    queried_at: datetime,
) -> tuple[list[ConvergenceOpportunity], dict[str, Any]]:
    page_size = (
        MAX_CONVERGENCE_OPPORTUNITIES
        if params.max_results is None
        else min(params.max_results, MAX_CONVERGENCE_OPPORTUNITIES)
    )
    total = len(opportunities)
    page = list(opportunities[params.offset : params.offset + page_size])
    returned = len(page)
    more_remain = params.offset + returned < total

    partial_reason = "too_large" if more_remain else None
    message = (
        (
            f"returned opportunities[{params.offset}:{params.offset + returned}] of "
            f"{total} total — more remain; page on with offset={params.offset + returned} "
            f"(page size {page_size}, max {MAX_CONVERGENCE_OPPORTUNITIES})"
        )
        if more_remain
        else None
    )
    response = {
        "query": params.query,
        "opportunities": [o.model_dump(mode="json") for o in page],
        "total_available": total,
        "offset": params.offset,
        "returned": returned,
        "partial_reason": partial_reason,
        "queried_at": queried_at.isoformat(),
        "source": _DEFAULT_SOURCE,
        "error": None,
        "message": message,
    }
    return page, response


def _error(
    params: FindConvergenceOpportunitiesInput, reason: str, err: Exception
) -> dict[str, Any]:
    return {
        "query": params.query,
        "opportunities": None,
        "total_available": None,
        "offset": params.offset,
        "returned": None,
        "partial_reason": None,
        "queried_at": _now_iso(),
        "source": _DEFAULT_SOURCE,
        "error": reason,
        "message": str(err),
    }


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def register_prediction_screener(
    server: FastMCP,
    *,
    prediction_market_sources: Mapping[str, PredictionMarketSource],
    event_bus: EventBus,
) -> None:
    """Bind `find_convergence_opportunities` to `server`. The registry + event bus are
    captured by closure and the default source selected by name at call time
    (ADR-0031), so the tool registers even before a source config beyond the keyless
    default is wired."""

    @server.tool(description=FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION)
    async def find_convergence_opportunities(
        params: FindConvergenceOpportunitiesInput,
    ) -> dict[str, Any]:
        return await _screen_response(
            prediction_market_sources=prediction_market_sources,
            event_bus=event_bus,
            params=params,
        )


__all__ = [
    "FIND_CONVERGENCE_OPPORTUNITIES_DESCRIPTION",
    "MAX_CONVERGENCE_OPPORTUNITIES",
    "FindConvergenceOpportunitiesInput",
    "_screen_response",
    "register_prediction_screener",
]
