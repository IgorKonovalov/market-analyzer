"""Cross-venue portfolio aggregation (Plan 0041 phase 3; ADR-0042).

`aggregate_portfolio` folds the three holdings legs — a Binance account
snapshot, DeFi discovery positions (with optional ADR-0036 replay basis), and
manual-file holdings — into one `PortfolioSummary`: unified holdings with
average-cost basis, unrealized P&L, and USD exposure by asset and by venue.

**Pure and deterministic.** The function reads no clock, touches no network,
and iterates no set: same snapshot inputs → byte-identical output (the
ADR-0018 posture applied to aggregation; `queried_at` is injected provenance,
never computed). Holdings order is fixed — Binance spot, Binance futures,
DeFi, manual — each leg in its input order; exposure dicts key in
first-encounter holding order.

**Cost basis (average-cost venue-wide, ADR-0042).** Futures carry the venue's
entry price as basis; DeFi positions join their remaining average-cost basis
from the ADR-0036 replay by `position_id` (the caller runs the replay over the
cached history and passes the map — this module never re-implements the
engine); manual rows carry the file's user-stated basis; spot balances have
none (`None` — Binance does not record one; honestly unknown, never zero).

**Unrealized P&L** is `usd_value - avg_cost * quantity`, summed over the
holdings that carry both a valuation and a basis; when none does, the total is
`None`, never a confident `0.0`. (For DeFi legs, quantity is ±1 position-unit,
so the formula reduces to ADR-0036's `current value - remaining basis`.)

**Valuation provenance.** Prices arrive as an injected `(venue, symbol) →
PricePoint` map — the aggregator never fetches. Each priced holding names its
`pricing_source`; an unpriced holding stays in the holdings list with
`usd_value=None` and is excluded from the exposure sums (visible, not
silently zero-valued). Sign conventions: a short futures position and a
`lending_borrow` DeFi position carry negative quantity and negative
`usd_value` — exposure sums are net.

Facts only: no recommendation is computed or carried (ADR-0029).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.data.types import AccountHoldings
from market_analyser.defi.models import DefiPosition
from market_analyser.portfolio.models import Holding, PortfolioSummary

# The venue's own mark price is the pricing reference for futures positions —
# it arrives inside the account snapshot, not through the price map.
BINANCE_MARK_PRICING_SOURCE = "binance-mark"

# DeFi discovery values positions with the source's interpreted USD figure.
DEFI_PRICING_SOURCE = "zerion"


class PricePoint(BaseModel):
    """One resolved USD reference price with its provenance: which source
    produced it and the price's own upstream timestamp (not "now")."""

    model_config = ConfigDict(frozen=True)

    price: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return v.astimezone(UTC)


def aggregate_portfolio(
    *,
    binance: AccountHoldings | None = None,
    defi_positions: Sequence[DefiPosition] | None = None,
    defi_as_of: datetime | None = None,
    defi_basis: Mapping[str, float] | None = None,
    manual: Sequence[Holding] = (),
    prices: Mapping[tuple[str, str], PricePoint] | None = None,
    queried_at: datetime,
) -> PortfolioSummary:
    """Fold the supplied legs into one `PortfolioSummary`.

    A leg passed as `None` was not read (not configured / failed upstream —
    the caller reports why); it contributes nothing and gets no `legs_as_of`
    entry. `defi_positions` requires `defi_as_of` (the scan instant); the
    manual leg's stamp is its **oldest** row (`min`), the conservative
    freshness read for user-maintained state.
    """
    if defi_positions is not None and defi_as_of is None:
        raise ValueError("defi_positions requires defi_as_of (the scan instant)")
    price_map = prices if prices is not None else {}
    basis_map = defi_basis if defi_basis is not None else {}

    holdings: list[Holding] = []
    if binance is not None:
        holdings.extend(_binance_holdings(binance, price_map))
    if defi_positions is not None and defi_as_of is not None:
        holdings.extend(_defi_holdings(defi_positions, basis_map, defi_as_of))
    holdings.extend(_priced_manual(manual, price_map))

    exposure_by_asset: dict[str, float] = {}
    exposure_by_venue: dict[str, float] = {}
    pnl_total = 0.0
    pnl_contributors = 0
    for holding in holdings:
        if holding.usd_value is None:
            continue
        exposure_by_asset[holding.symbol] = (
            exposure_by_asset.get(holding.symbol, 0.0) + holding.usd_value
        )
        exposure_by_venue[holding.venue] = (
            exposure_by_venue.get(holding.venue, 0.0) + holding.usd_value
        )
        if holding.avg_cost is not None:
            pnl_total += holding.usd_value - holding.avg_cost * holding.quantity
            pnl_contributors += 1

    legs_as_of: dict[str, datetime] = {}
    if binance is not None:
        legs_as_of["binance"] = binance.as_of
    if defi_positions is not None and defi_as_of is not None:
        legs_as_of["defi"] = defi_as_of
    manual_holdings = [h for h in holdings if h.venue == "manual"]
    if manual_holdings:
        legs_as_of["manual"] = min(h.as_of for h in manual_holdings)

    return PortfolioSummary(
        holdings=holdings,
        unrealized_pnl_usd=pnl_total if pnl_contributors else None,
        exposure_by_asset=exposure_by_asset,
        exposure_by_venue=exposure_by_venue,
        legs_as_of=legs_as_of,
        queried_at=queried_at,
    )


def unrealized_contributor_count(summary: PortfolioSummary) -> int:
    """How many holdings carry both a valuation and a basis — the coverage of
    `unrealized_pnl_usd`, so a partial figure can be labeled as partial."""
    return sum(1 for h in summary.holdings if h.usd_value is not None and h.avg_cost is not None)


def _binance_holdings(
    account: AccountHoldings,
    prices: Mapping[tuple[str, str], PricePoint],
) -> list[Holding]:
    """Spot balances (priced via the injected map, keyed by asset code) then
    futures positions (priced by the venue's own mark; entry price as basis —
    the Plan 0041 open question resolved as proposed)."""
    out: list[Holding] = []
    for balance in account.spot:
        point = prices.get((account.venue, balance.asset))
        quantity = balance.free + balance.locked
        out.append(
            Holding(
                symbol=balance.asset,
                venue="binance",
                quantity=quantity,
                avg_cost=None,  # the venue records no spot cost basis
                as_of=account.as_of,
                usd_value=quantity * point.price if point is not None else None,
                pricing_source=point.source if point is not None else None,
                kind="spot",
            ),
        )
    for position in account.futures:
        priced = position.mark_price is not None
        out.append(
            Holding(
                symbol=position.symbol,
                venue="binance",
                quantity=position.quantity,
                avg_cost=position.entry_price,
                as_of=account.as_of,
                usd_value=(
                    position.quantity * position.mark_price
                    if position.mark_price is not None
                    else None
                ),
                pricing_source=BINANCE_MARK_PRICING_SOURCE if priced else None,
                kind="futures",
            ),
        )
    return out


def _defi_holdings(
    positions: Sequence[DefiPosition],
    basis: Mapping[str, float],
    as_of: datetime,
) -> list[Holding]:
    """One holding per position, quantity ±1 position-unit: a multi-token LP
    has no single meaningful quantity, so the position itself is the unit and
    `avg_cost` is the whole-position remaining basis (ADR-0036's currency).
    A `lending_borrow` is a liability: negative unit, negative value, and no
    average-cost basis (debt has no basis in the average-cost sense)."""
    out: list[Holding] = []
    for position in positions:
        borrow = position.kind == "lending_borrow"
        sign = -1.0 if borrow else 1.0
        out.append(
            Holding(
                symbol=_defi_symbol(position),
                venue="defi",
                quantity=sign,
                avg_cost=None if borrow else basis.get(position.position_id),
                as_of=as_of,
                usd_value=sign * position.usd_value,
                pricing_source=DEFI_PRICING_SOURCE,
                kind=f"defi:{position.kind}",
            ),
        )
    return out


def _defi_symbol(position: DefiPosition) -> str:
    """A stable display symbol: the pool name when the source exposes one,
    else the token symbols joined in position order (deterministic)."""
    if position.pool:
        return position.pool
    return "/".join(token.symbol for token in position.tokens)


def _priced_manual(
    manual: Sequence[Holding],
    prices: Mapping[tuple[str, str], PricePoint],
) -> list[Holding]:
    """Manual rows arrive as validated holdings; attach a valuation (and its
    reference) where the price map covers the symbol, leave the rest honestly
    unpriced."""
    out: list[Holding] = []
    for holding in manual:
        point = prices.get((holding.venue, holding.symbol))
        if point is None or holding.usd_value is not None:
            out.append(holding)
        else:
            out.append(
                holding.model_copy(
                    update={
                        "usd_value": holding.quantity * point.price,
                        "pricing_source": point.source,
                    },
                ),
            )
    return out


__all__ = [
    "BINANCE_MARK_PRICING_SOURCE",
    "DEFI_PRICING_SOURCE",
    "PricePoint",
    "aggregate_portfolio",
    "unrealized_contributor_count",
]
