"""Cross-pool discrepancy screener v2 (Plan 0086, ADR-0080; BA-7 evidence, ADR-0072).

A pure function over the per-pool `ExecutableQuote`s an `ExecutableQuoteSource`
reads: for each pair with two or more pools it buys at the executably-cheapest venue
and sells at the executably-dearest and reports the **net-of-cost** spread of the
round trip. Because each quote already carries its net `buy_cost` (exact-output) and
`sell_proceeds` (exact-input) — the pool's fee and its measured slippage folded in by
the source (constant product from `x·y=k`, concentrated liquidity from the DEX
Quoter) — the screener no longer models cost itself. It ranks pre-costed quotes:

    net = max(sell_proceeds) - min(buy_cost) - gas

**The honest number is `net_spread`.** Gas is subtracted before a discrepancy is
called an opportunity. Slippage and fee are **measured** (inside the quotes), not
estimated; a slippage/fee **breakdown** is *reconstructed* against each quote's
`marginal_price` zero-size reference and carried on the observation for auditability
— *derived*, labeled, never a second source of truth (ADR-0080). Sub-threshold
observations are **flagged not-capturable, not dropped** — the caller sees that a
discrepancy existed but did not clear its cost.

**`capturability_note` is load-bearing.** An RPC poller sees prices later than a
colocated searcher, so a discrepancy that looks present to this scanner is an
**upper bound on capturability, not a capture guarantee**. The note says so on
every observation; the scanner measures observability, never guaranteed capture.

**Determinism.** No wall-clock, no set iteration, stable sort. `queried_at` is the
newest `as_of` among the pair's quotes (read provenance), not the current time, so
re-running over the same quotes is byte-identical (`model_dump` equal).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.defi.models import ExecutableQuote

# The fixed honesty caveat every observation carries (Plan 0079 / ADR-0072). RPC
# observability is an upper bound on capturability — never mistake it for capture.
CAPTURABILITY_NOTE = (
    "RPC-observed spread — an UPPER BOUND on capturability, not a capture "
    "guarantee. A colocated searcher sees and executes faster than an RPC poller; "
    "this net-of-cost figure excludes MEV/searcher competition, block-inclusion "
    "risk, and inventory risk. Persistence measured here overstates what is "
    "actually capturable."
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
    """One pair's executably-cheapest-buy vs executably-dearest-sell round trip, net
    of cost (Plan 0086 / ADR-0080). Boundary-validated; `net_spread` may be negative
    (costs exceed the executable spread) — a legitimate, informative result.

    The reconstructed breakdown satisfies the auditability identity
    ``sell_proceeds - buy_cost = (P_sell - P_buy)·trade_size - reconstructed_fees -
    reconstructed_slippage`` (with `P` the quotes' `marginal_price`), so the
    executable numbers decompose exactly against the zero-size reference. The
    breakdown is *derived* — the executable `buy_cost`/`sell_proceeds` are
    authoritative; `reconstructed_*` are for inspection only and may be negative for
    a source whose marginal reference disagrees with its executable quote."""

    model_config = ConfigDict(frozen=True)

    pair: str = Field(min_length=1)
    trade_size: float = Field(gt=0)
    buy_pool: str = Field(min_length=1)  # executably cheapest acquisition (address)
    buy_dex: str = Field(min_length=1)
    buy_cost: float = Field(gt=0)  # quote-in to acquire trade_size base, net
    sell_pool: str = Field(min_length=1)  # executably dearest disposal (address)
    sell_dex: str = Field(min_length=1)
    sell_proceeds: float = Field(gt=0)  # quote-out from selling trade_size base, net
    est_gas_cost: float = Field(ge=0)  # subtracted
    net_spread: float  # sell_proceeds - buy_cost - gas — THE honest number
    reconstructed_slippage: float  # vs marginal reference (derived, auditability)
    reconstructed_fees: float  # vs marginal reference (derived, auditability)
    capturable_at_threshold: bool
    capturability_note: str = Field(min_length=1)
    queried_at: datetime  # newest as_of among the pair's quotes (provenance)

    @field_validator(
        "trade_size",
        "buy_cost",
        "sell_proceeds",
        "est_gas_cost",
        "net_spread",
        "reconstructed_slippage",
        "reconstructed_fees",
    )
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("observation measurement must be finite (no NaN/Inf)")
        return v


def scan_discrepancies(
    quotes: Sequence[ExecutableQuote],
    *,
    params: DiscrepancyParams,
) -> list[ArbObservation]:
    """Screen `ExecutableQuote`s for cross-pool discrepancies, net of cost.

    Quotes are grouped by pair; each pair with two or more pools yields one
    `ArbObservation` (cheapest-buy vs dearest-sell). Results are sorted by
    `net_spread` descending, then pair / buy_pool / sell_pool for a stable,
    deterministic order. Every observation — capturable or not — is returned, so a
    sub-threshold discrepancy is surfaced (flagged), never silently dropped.

    Pure and deterministic: no wall-clock read, no set iteration; `queried_at`
    comes from the quotes' `as_of`. All quotes for a pair must share the same
    `trade_size` (the source quotes one size per scan); a mismatch is a caller bug.
    """
    by_pair: dict[str, list[ExecutableQuote]] = {}
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
    group: Sequence[ExecutableQuote],
    *,
    params: DiscrepancyParams,
) -> ArbObservation:
    trade_sizes = {q.trade_size for q in group}
    if len(trade_sizes) != 1:
        raise ValueError(f"pair {pair!r} quotes mix trade sizes {sorted(trade_sizes)}")
    size = group[0].trade_size

    # Deterministic cheapest acquisition / dearest disposal with pool_id tie-breaks.
    # Independent argmin/argmax realises the plan's `max(sell_proceeds) -
    # min(buy_cost)` verbatim; when one venue is both, net is (correctly) negative.
    buy = min(group, key=lambda q: (q.buy_cost, q.pool_id))
    sell = min(group, key=lambda q: (-q.sell_proceeds, q.pool_id))

    net_spread = sell.sell_proceeds - buy.buy_cost - params.est_gas_cost
    capturable = net_spread >= params.min_net_spread

    # Reconstruct the fee / slippage split against each leg's zero-size reference —
    # derived, for auditability only (ADR-0080). Fee is the pool's tier applied to
    # the marginal notional; slippage is the residual, so the split sums back to the
    # executable numbers exactly (see the class identity).
    slippage_buy, fees_buy = _reconstruct_buy_breakdown(buy, size)
    slippage_sell, fees_sell = _reconstruct_sell_breakdown(sell, size)
    reconstructed_slippage = slippage_buy + slippage_sell
    reconstructed_fees = fees_buy + fees_sell

    queried_at = max(q.as_of for q in group)

    return ArbObservation(
        pair=pair,
        trade_size=size,
        buy_pool=buy.pool_id,
        buy_dex=buy.dex,
        buy_cost=buy.buy_cost,
        sell_pool=sell.pool_id,
        sell_dex=sell.dex,
        sell_proceeds=sell.sell_proceeds,
        est_gas_cost=params.est_gas_cost,
        net_spread=net_spread,
        reconstructed_slippage=reconstructed_slippage,
        reconstructed_fees=reconstructed_fees,
        capturable_at_threshold=capturable,
        capturability_note=CAPTURABILITY_NOTE,
        queried_at=queried_at,
    )


def _fee_frac(quote: ExecutableQuote) -> float:
    """The quote's fee tier as a fraction (bps → fraction); 0 when unattributed."""
    return (quote.fee_tier or 0) / 1e4


def _reconstruct_buy_breakdown(quote: ExecutableQuote, size: float) -> tuple[float, float]:
    """`(slippage, fee)` for the buy leg against the marginal reference. Fee is the
    tier on the marginal notional; slippage is the residual, so
    ``buy_cost = notional + fee + slippage`` holds exactly."""
    notional = quote.marginal_price * size
    fee = _fee_frac(quote) * notional
    slippage = (quote.buy_cost - notional) - fee
    return slippage, fee


def _reconstruct_sell_breakdown(quote: ExecutableQuote, size: float) -> tuple[float, float]:
    """`(slippage, fee)` for the sell leg against the marginal reference. Fee is the
    tier on the marginal notional; slippage is the residual, so
    ``sell_proceeds = notional - fee - slippage`` holds exactly."""
    notional = quote.marginal_price * size
    fee = _fee_frac(quote) * notional
    slippage = (notional - quote.sell_proceeds) - fee
    return slippage, fee


__all__ = [
    "CAPTURABILITY_NOTE",
    "ArbObservation",
    "DiscrepancyParams",
    "scan_discrepancies",
]
