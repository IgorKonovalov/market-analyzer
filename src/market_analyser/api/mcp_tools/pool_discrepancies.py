"""`scan_pool_discrepancies` MCP tool — Plan 0079 phase 3 (ADR-0072 BA-7).

A read-only tool over the `PoolPriceSource` registry (ADR-0031): for the requested
pairs it reads each configured pool's price, runs the cross-pool discrepancy
screener (`defi/discrepancy.py`), and returns the ranked **net-of-cost**
observations — gross spread minus gas, per-pool slippage for the trade size, and
pool fees — each carrying the capturability caveat.

Charter-safe (ADR-0072 / ADR-0029 / ADR-0015): it reports discrepancies as
**facts**, never a trade instruction. It signs nothing, holds no key, moves no
funds — it is the *evidence* layer that answers whether cross-pool discrepancies
ever survive net-of-cost at RPC observability, **before** any arbitrage-execution
build is scoped. Every observation states plainly that RPC-observed persistence is
an **upper bound on capturability, not a capture guarantee** (a colocated searcher
sees and executes faster than an RPC poller).

Results are bounded per ADR-0046: one page of at most `MAX_DISCREPANCY_OBSERVATIONS`
observations, with `total_available` / `offset` / `returned` and a
`partial_reason="too_large"` when more remain. On failure the observations list is
null and `error` is a typed reason (`unconfigured` — no pool-price source wired;
`config_error` — missing RPC URL / unsupported chain; `rate_limited` /
`upstream_unavailable` — throttle / outage; `malformed_response` — on-chain shape
drift). The registry keeps the source swappable — a later config could select
another pool-price source by name without touching this tool.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.onchain_pools import PoolPriceConfigError, PoolPriceError
from market_analyser.data.errors import RateLimitedError, UpstreamDataError, failure_reason
from market_analyser.data.sources import PoolPriceSource
from market_analyser.defi.discrepancy import (
    CAPTURABILITY_NOTE,
    ArbObservation,
    DiscrepancyParams,
    scan_discrepancies,
)
from market_analyser.defi.models import PoolQuote

# The default pool-price source for this plan (ADR-0072). The registry seam keeps
# it swappable — a later config could choose another source by name.
_DEFAULT_SOURCE = "onchain"

# Maximum observations returned inline in one page (ADR-0046). One observation per
# pair, so this bounds the pair fan-out of a single scan; 50 sits far under the
# harness token cap for the observation row size and is pinned by a test.
MAX_DISCREPANCY_OBSERVATIONS = 50

# Upper bound on pairs a single call may request (each pair is several paced RPC
# reads per pool — a courtesy cap on the on-chain fan-out).
_MAX_PAIRS = 50

SCAN_POOL_DISCREPANCIES_DESCRIPTION = (
    "Screen configured DEX pools for cross-pool price discrepancies, NET OF COST, "
    "for one or more canonical pairs (e.g. 'WETH/USDC') at a given trade_size. "
    "Reads each configured pool's on-chain price and returns ranked observations "
    "{pair, trade_size, buy_pool, buy_dex, sell_pool, sell_dex, buy_price, "
    "sell_price, gross_spread, est_gas_cost, est_slippage, est_fees, net_spread, "
    "capturable_at_threshold, capturability_note, queried_at}, where net_spread = "
    "gross - gas - slippage - fees is the honest number (a gross spread is never "
    "reported as the opportunity). A sub-threshold discrepancy is flagged "
    "capturable_at_threshold=false, not dropped. IMPORTANT: net_spread is an UPPER "
    "BOUND on capturability, not a capture guarantee - an RPC poller sees prices "
    "later than a colocated searcher, so a discrepancy visible here may not be "
    "capturable in practice (see capturability_note). Facts only - this reports "
    "conditions, never a buy/sell/execute call, and it signs nothing and moves no "
    "funds. est_gas_cost (quote-token units) and min_net_spread tune the cost "
    "model and the capturable threshold. Results are bounded to "
    f"{MAX_DISCREPANCY_OBSERVATIONS} per page: when more remain "
    "partial_reason='too_large' and total_available/offset/returned tell you how "
    "to page (call again with offset=returned). On failure observations is null "
    "and error is a typed reason (unconfigured / config_error / rate_limited / "
    "upstream_unavailable / malformed_response)."
)


class ScanPoolDiscrepanciesInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: list[str] = Field(
        min_length=1,
        max_length=_MAX_PAIRS,
        description="Canonical pairs to scan, e.g. ['WETH/USDC']",
    )
    trade_size: float = Field(
        gt=0, description="Base-token trade size to price the discrepancy for"
    )
    est_gas_cost: float = Field(
        default=1.0,
        ge=0,
        description="Flat round-trip gas estimate in quote-token units (conservative)",
    )
    min_net_spread: float = Field(
        default=0.0,
        ge=0,
        description="Net-of-cost threshold (quote-token units) a spread must clear",
    )
    offset: int = Field(default=0, ge=0, description="Page offset into the ranked observations")
    max_results: int | None = Field(
        default=None,
        ge=1,
        description=f"Page size (default and cap {MAX_DISCREPANCY_OBSERVATIONS})",
    )


async def _scan_response(
    *,
    pool_price_sources: Mapping[str, PoolPriceSource],
    params: ScanPoolDiscrepanciesInput,
) -> dict[str, Any]:
    """Body of the tool, factored out so it is unit-testable without a live MCP
    server. Reads quotes for each requested pair, screens them, and returns one
    bounded page of ranked net-of-cost observations."""
    source = pool_price_sources.get(_DEFAULT_SOURCE)
    if source is None:
        return _error(
            params,
            "unconfigured",
            "no pool-price source is wired — set an RPC URL and configure pools",
        )
    try:
        quotes: list[PoolQuote] = []
        for pair in params.pairs:
            quotes.extend(
                await asyncio.to_thread(
                    source.fetch_pool_quotes, pair, trade_size=params.trade_size
                )
            )
    except PoolPriceConfigError as err:
        return _error(params, "config_error", str(err))
    except RateLimitedError as err:
        return _error(params, "rate_limited", str(err))
    except UpstreamDataError as err:
        return _error(params, failure_reason(err), str(err))
    except (PoolPriceError, ValidationError) as err:
        return _error(params, "malformed_response", str(err))

    observations = scan_discrepancies(
        quotes,
        params=DiscrepancyParams(
            est_gas_cost=params.est_gas_cost, min_net_spread=params.min_net_spread
        ),
    )
    return _paginate(params, observations)


def _paginate(
    params: ScanPoolDiscrepanciesInput,
    observations: Sequence[ArbObservation],
) -> dict[str, Any]:
    page_size = (
        MAX_DISCREPANCY_OBSERVATIONS
        if params.max_results is None
        else min(params.max_results, MAX_DISCREPANCY_OBSERVATIONS)
    )
    total = len(observations)
    page = list(observations[params.offset : params.offset + page_size])
    returned = len(page)
    more_remain = params.offset + returned < total

    partial_reason = "too_large" if more_remain else None
    message = (
        (
            f"returned observations[{params.offset}:{params.offset + returned}] of "
            f"{total} total — more remain; page on with offset={params.offset + returned} "
            f"(page size {page_size}, max {MAX_DISCREPANCY_OBSERVATIONS})"
        )
        if more_remain
        else None
    )
    return {
        "pairs": list(params.pairs),
        "trade_size": params.trade_size,
        "observations": [o.model_dump(mode="json") for o in page],
        "total_available": total,
        "offset": params.offset,
        "returned": returned,
        "partial_reason": partial_reason,
        "capturability_note": CAPTURABILITY_NOTE,
        "queried_at": _now_iso(),
        "source": _DEFAULT_SOURCE,
        "error": None,
        "message": message,
    }


def _error(params: ScanPoolDiscrepanciesInput, reason: str, message: str) -> dict[str, Any]:
    return {
        "pairs": list(params.pairs),
        "trade_size": params.trade_size,
        "observations": None,
        "total_available": None,
        "offset": params.offset,
        "returned": None,
        "partial_reason": None,
        "capturability_note": CAPTURABILITY_NOTE,
        "queried_at": _now_iso(),
        "source": _DEFAULT_SOURCE,
        "error": reason,
        "message": message,
    }


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def register_pool_discrepancies(
    server: FastMCP,
    *,
    pool_price_sources: Mapping[str, PoolPriceSource],
) -> None:
    """Bind `scan_pool_discrepancies` to `server`. The registry is captured by
    closure and the default source selected by name at call time (ADR-0031), so the
    tool registers even when no source is wired (it then returns `unconfigured`)."""

    @server.tool(description=SCAN_POOL_DISCREPANCIES_DESCRIPTION)
    async def scan_pool_discrepancies(params: ScanPoolDiscrepanciesInput) -> dict[str, Any]:
        return await _scan_response(pool_price_sources=pool_price_sources, params=params)


__all__ = [
    "MAX_DISCREPANCY_OBSERVATIONS",
    "SCAN_POOL_DISCREPANCIES_DESCRIPTION",
    "ScanPoolDiscrepanciesInput",
    "_scan_response",
    "register_pool_discrepancies",
]
