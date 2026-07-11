"""Cross-pool discrepancy screener core (Plan 0079 phase 2, ADR-0072 BA-7).

A pure function over the per-pool `PoolQuote`s a `PoolPriceSource` reads: for each
pair with two or more pools it finds the cheapest and dearest venue and computes
the **net-of-cost** spread of the round-trip arbitrage — buy the base where it is
cheap, sell where it is dear — after subtracting an estimated gas cost, the
per-pool price-impact (slippage) implied by the trade size, and the pool swap fees.

**The honest number is `net_spread`, never `gross_spread`.** Every cost is
subtracted before a discrepancy is called an opportunity, and each cost is carried
on the `ArbObservation` so the assumption is visible (an optimistic cost model
would fabricate opportunities — Plan 0079 risk). Sub-threshold observations are
**flagged not-capturable, not dropped** — the caller sees that a discrepancy
existed but did not clear its cost.

**`capturability_note` is load-bearing.** An RPC poller sees prices later than a
colocated searcher, so a discrepancy that looks present to this scanner is an
**upper bound on capturability, not a capture guarantee**. The note says so on
every observation; the scanner measures observability, never guaranteed capture.

**Determinism.** No wall-clock, no set iteration, stable sort. `queried_at` is the
newest `as_of` among the pair's quotes (read provenance), not the current time, so
re-running over the same quotes is byte-identical (`model_dump` equal).

**Cost model (v1, constant-product).** For a trade of `trade_size` base tokens,
everything is expressed in quote-token units:

- buy leg (remove Δ base from a pool with reserves ``(R_b, R_q)``):
  ``quote_in = R_q·Δ / (R_b - Δ)``; ``slippage_buy = quote_in - P_buy·Δ``;
- sell leg (add Δ base to a pool ``(R_b', R_q')``):
  ``quote_out = R_q'·Δ / (R_b' + Δ)``; ``slippage_sell = P_sell·Δ - quote_out``;
- fees: ``fee_bps/1e4`` of each leg's notional, both legs;
- gas: a flat, conservative, caller-supplied estimate in quote-token units.

If the buy pool cannot source the size (``Δ ≥ R_b``) the observation is marked
depth-exceeded and not-capturable rather than emitting a fabricated finite number.
Concentrated-liquidity (Uniswap-v3) depth is a followup — the v1 sources are all
constant-product (see `data/adapters/onchain_pools.py`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.defi.models import PoolQuote

# The fixed honesty caveat every observation carries (Plan 0079 / ADR-0072). RPC
# observability is an upper bound on capturability — never mistake it for capture.
CAPTURABILITY_NOTE = (
    "RPC-observed spread — an UPPER BOUND on capturability, not a capture "
    "guarantee. A colocated searcher sees and executes faster than an RPC poller; "
    "this net-of-cost figure excludes MEV/searcher competition, block-inclusion "
    "risk, and inventory risk. Persistence measured here overstates what is "
    "actually capturable."
)

_DEPTH_EXCEEDED_NOTE = (
    " Trade size exceeds the buy pool's base depth — not executable at this size."
)


class DiscrepancyParams(BaseModel):
    """Screener knobs. `est_gas_cost` is a flat, deliberately conservative estimate
    of the round-trip gas cost **in quote-token units** (e.g. USD for a USD-quoted
    pair) — surfaced on every observation so the assumption is auditable, and set by
    the caller per chain / gas price. `min_net_spread` is the threshold (quote-token
    units) a net spread must clear to be flagged capturable; the default `0.0` means
    "any strictly positive net-of-cost edge"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    est_gas_cost: float = Field(default=1.0, ge=0)
    min_net_spread: float = Field(default=0.0, ge=0)

    @field_validator("est_gas_cost", "min_net_spread")
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("discrepancy param must be finite (no NaN/Inf)")
        return v


class ArbObservation(BaseModel):
    """One pair's cheapest-vs-dearest cross-pool spread, net of cost. Boundary-
    validated; `net_spread` may be negative (costs exceed the gross spread) — that
    is a legitimate, informative result, not an error."""

    model_config = ConfigDict(frozen=True)

    pair: str = Field(min_length=1)
    trade_size: float = Field(gt=0)
    buy_pool: str = Field(min_length=1)  # cheapest executable venue (address)
    buy_dex: str = Field(min_length=1)
    sell_pool: str = Field(min_length=1)  # dearest executable venue (address)
    sell_dex: str = Field(min_length=1)
    buy_price: float = Field(gt=0)  # quote-per-base at the buy venue
    sell_price: float = Field(gt=0)  # quote-per-base at the sell venue
    gross_spread: float = Field(ge=0)  # (sell_price - buy_price) · trade_size
    est_gas_cost: float = Field(ge=0)  # subtracted
    est_slippage: float = Field(ge=0)  # per-pool price impact for the size — subtracted
    est_fees: float = Field(ge=0)  # both legs' swap fees — subtracted
    net_spread: float  # gross - gas - slippage - fees — THE honest number
    capturable_at_threshold: bool
    capturability_note: str = Field(min_length=1)
    queried_at: datetime  # newest as_of among the pair's quotes (provenance)

    @field_validator(
        "trade_size",
        "buy_price",
        "sell_price",
        "gross_spread",
        "est_gas_cost",
        "est_slippage",
        "est_fees",
        "net_spread",
    )
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("observation measurement must be finite (no NaN/Inf)")
        return v


def scan_discrepancies(
    quotes: Sequence[PoolQuote],
    *,
    params: DiscrepancyParams,
) -> list[ArbObservation]:
    """Screen `quotes` for cross-pool discrepancies, net of cost.

    Quotes are grouped by pair; each pair with two or more pools yields one
    `ArbObservation` (cheapest-buy vs dearest-sell). Results are sorted by
    `net_spread` descending, then by pair / buy_pool / sell_pool for a stable,
    deterministic order. Every observation — capturable or not — is returned, so a
    sub-threshold discrepancy is surfaced (flagged), never silently dropped.

    Pure and deterministic: no wall-clock read, no set iteration; `queried_at`
    comes from the quotes' `as_of`. All quotes for a pair must share the same
    `trade_size` (the adapter quotes one size per scan); a mismatch is a caller bug.
    """
    by_pair: dict[str, list[PoolQuote]] = {}
    for quote in quotes:
        by_pair.setdefault(quote.pair, []).append(quote)

    observations: list[ArbObservation] = []
    for pair in sorted(by_pair):
        group = by_pair[pair]
        if len(group) < 2:
            continue
        observations.append(_observe_pair(pair, group, params=params))

    observations.sort(
        key=lambda o: (-o.net_spread, o.pair, o.buy_pool, o.sell_pool),
    )
    return observations


def _observe_pair(
    pair: str,
    group: Sequence[PoolQuote],
    *,
    params: DiscrepancyParams,
) -> ArbObservation:
    trade_sizes = {q.trade_size for q in group}
    if len(trade_sizes) != 1:
        raise ValueError(f"pair {pair!r} quotes mix trade sizes {sorted(trade_sizes)}")
    size = group[0].trade_size

    # Deterministic cheapest/dearest with a (price, pool_id) tie-break.
    ordered = sorted(group, key=lambda q: (q.price, q.pool_id))
    buy = ordered[0]  # cheapest quote-per-base — buy the base here
    sell = ordered[-1]  # dearest — sell the base here

    gross_spread = (sell.price - buy.price) * size
    est_fees = (buy.fee_bps / 1e4) * buy.price * size + (sell.fee_bps / 1e4) * sell.price * size

    depth_exceeded = size >= buy.liquidity_base
    if depth_exceeded:
        # The buy pool cannot source the size — do not fabricate a finite impact.
        # A conservative finite sentinel (the pool's whole quote depth) keeps the
        # model valid and the observation strongly not-capturable.
        est_slippage = buy.liquidity_quote + sell.liquidity_quote
    else:
        quote_in = buy.liquidity_quote * size / (buy.liquidity_base - size)
        slippage_buy = quote_in - buy.price * size
        quote_out = sell.liquidity_quote * size / (sell.liquidity_base + size)
        slippage_sell = sell.price * size - quote_out
        est_slippage = slippage_buy + slippage_sell

    net_spread = gross_spread - params.est_gas_cost - est_slippage - est_fees
    capturable = (not depth_exceeded) and net_spread >= params.min_net_spread
    note = CAPTURABILITY_NOTE + (_DEPTH_EXCEEDED_NOTE if depth_exceeded else "")
    queried_at = max(q.as_of for q in group)

    return ArbObservation(
        pair=pair,
        trade_size=size,
        buy_pool=buy.pool_id,
        buy_dex=buy.dex,
        sell_pool=sell.pool_id,
        sell_dex=sell.dex,
        buy_price=buy.price,
        sell_price=sell.price,
        gross_spread=gross_spread,
        est_gas_cost=params.est_gas_cost,
        est_slippage=est_slippage,
        est_fees=est_fees,
        net_spread=net_spread,
        capturable_at_threshold=capturable,
        capturability_note=note,
        queried_at=queried_at,
    )


__all__ = [
    "CAPTURABILITY_NOTE",
    "ArbObservation",
    "DiscrepancyParams",
    "scan_discrepancies",
]
