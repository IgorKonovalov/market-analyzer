"""P&L replay engine — average-cost lots, block-time valuation, vs-HODL
(Plan 0035 phase 6, implementing ADR-0036).

Replays each position's ordered `PositionEvent`s (block, then in-block index)
and values **every leg at its own block timestamp** via the
`HistoricalPriceSource` — never at "now", never at Zerion's inline figures
(the no-lookahead corollary). With the phase-4 snapshot cache behind the
source, a re-run on the same cached inputs is **byte-identical** — there is
deliberately no wall-clock read anywhere in this module; run provenance is the
phase-7 job's concern.

**Accounting (average-cost pot, documented here because future-you will ask
"why is my P&L this number"):**

- *Contribution* (`add_liquidity` / `supply`; out-legs): the position's cost
  basis grows by the legs' block-time USD value; per-token contributed
  amounts accrue.
- *Extraction* (`remove_liquidity` / `withdraw_supply`; in-legs): the
  extracted fraction is value-weighted — `f = V_extracted / V_holdings`,
  both priced at the extraction block, where holdings are the net contributed
  amounts before the event. It realizes `V_extracted - f * basis` and
  releases `f * basis` (capped at 1.0 — an extraction exceeding holdings
  realizes against the whole remaining basis).
- *Income* (`fee_claim` / `reward_claim`; in-legs): realized income at
  claim-time price, exactly as ADR-0036 books it.
- *Swap* (`swap`; average-cost lot conversion — Plan 0084 / ADR-0079): a swap
  reshuffles value **within** the position rather than adding or removing
  capital, so it leaves the aggregate `basis` untouched (the invariant that
  keeps a mis-booked swap from corrupting cost basis). It drains the sold
  (out-leg) token amounts and accrues the bought (in-leg) token amounts, and
  realizes the swap's own execution delta `V_in - V_out` at block-time prices —
  for a fair atomic swap that is ≈ 0 (the fee/slippage cost), so a swap-inclusive
  lifecycle reconciles instead of failing loud.
- *Custody move* (`custody_move`; Plan 0084): staking / unstaking the LP token or
  NFT into or out of the gauge moves the position's *receipt*, not its underlying
  tokens, so it is a no-op — no basis, realized, or contributed change.
- *Debt* (`borrow` / `repay`, lending_borrow positions): a borrow draws
  `V_in` onto a debt pot; a repay releases `min(V_out, debt)` and realizes
  `released - V_out` (<= 0 — the interest cost surfaces as the debt closes).
  Unrealized is sign-flipped for debt: remaining pot minus current debt value.
- *Unrealized* (supply side): the position's current `usd_value` (discovery's
  figure, per the plan's "consumes it as-is") minus the remaining basis.
- *vs-HODL* (LP only): `(current value + claimed income) - (net contributed
  amounts valued at `as_of`)` — impermanent loss plus earned fees as one
  signed fact. `as_of` is caller-supplied (the phase-7 job passes the newest
  cached transaction's timestamp), keeping the benchmark deterministic.

**Loud failure (ADR-0036):** a missing price for any required leg, or an
event kind the pot cannot book (`liquidation` and `unclassified` — `swap` is
now booked as of Plan 0084), fails *that position*: every numeric field is
`None`, `incomplete=True`, and `notes` names the offending leg or event.
Nothing is coerced to zero. A wallet with any incomplete position reports
`None` totals — an incomplete total must not look like a real one.
"""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.defi.discovery import mask_wallet
from market_analyser.defi.models import DefiPosition
from market_analyser.defi.pnl_events import PositionEvent

# Event kinds the pot books. `swap` (average-cost conversion) and `custody_move`
# (a no-op) join the booked set as of Plan 0084; `liquidation` and `unclassified`
# remain unbooked and mark the position incomplete — inventing arithmetic for them
# would produce plausible wrong numbers (the honest gap, ADR-0036 fallback posture).
_CONTRIBUTION_KINDS = frozenset({"add_liquidity", "supply"})
_EXTRACTION_KINDS = frozenset({"remove_liquidity", "withdraw_supply"})
_INCOME_KINDS = frozenset({"fee_claim", "reward_claim"})
_DEBT_KINDS = frozenset({"borrow", "repay"})


class PositionPnl(BaseModel):
    """One position's reconstructed P&L. `None` numerics always travel with
    `incomplete=True` and a naming note — never silently-zeroed data."""

    model_config = ConfigDict(frozen=True)

    position_id: str = Field(min_length=1)
    realized_usd: float | None
    unrealized_usd: float | None
    cost_basis_usd: float | None
    vs_hodl_usd: float | None  # LP only; None for other kinds
    incomplete: bool
    notes: list[str] = Field(default_factory=list)


class WalletPnl(BaseModel):
    """The wallet's reconstructed P&L. Totals are `None` whenever any position
    is incomplete — a partial total must not masquerade as a real one."""

    model_config = ConfigDict(frozen=True)

    wallet: str  # masked (0x1234…abcd)
    positions: list[PositionPnl]
    realized_usd: float | None
    unrealized_usd: float | None
    incomplete: bool
    crosscheck_zerion_total: float | None = None  # advisory (phase 7 wires it)
    crosscheck_warning: bool = False


class _MissingPrice(Exception):
    """Internal control flow: a leg's block-time price is unavailable."""

    def __init__(self, token: str, ts: int) -> None:
        super().__init__(token)
        self.token = token
        self.ts = ts


def compute_wallet_pnl(
    *,
    wallet: str,
    positions: list[DefiPosition],
    events: list[PositionEvent],
    price_source: HistoricalPriceSource,
    as_of: datetime,
) -> WalletPnl:
    """Replay every position's events into per-position and wallet P&L.

    `events` must already be in replay order (the phase-5 mapper preserves the
    adapter/cache order); they are grouped per position without re-sorting.
    `as_of` anchors the LP HODL benchmark — pass a timestamp derived from the
    cached inputs (not the wall clock) to keep re-runs byte-identical."""
    per_position: dict[str, list[PositionEvent]] = {}
    for event in events:
        per_position.setdefault(event.position_id, []).append(event)
    results = [
        _replay_position(
            position,
            per_position.get(position.position_id, []),
            price_source,
            as_of,
        )
        for position in positions
    ]
    incomplete = any(p.incomplete for p in results)
    realized: float | None = None
    unrealized: float | None = None
    if not incomplete:
        realized = sum(p.realized_usd for p in results if p.realized_usd is not None)
        unrealized = sum(p.unrealized_usd for p in results if p.unrealized_usd is not None)
    return WalletPnl(
        wallet=mask_wallet(wallet),
        positions=results,
        realized_usd=realized,
        unrealized_usd=unrealized,
        incomplete=incomplete,
    )


def _replay_position(
    position: DefiPosition,
    events: list[PositionEvent],
    price_source: HistoricalPriceSource,
    as_of: datetime,
) -> PositionPnl:
    notes: list[str] = []
    basis = 0.0  # supply-side average-cost pot (USD)
    debt = 0.0  # drawn-debt pot (USD), lending_borrow only
    realized = 0.0
    contributed: dict[str, float] = {}  # net contributed amount per token key
    try:
        for event in events:
            kind = event.kind
            if kind in _CONTRIBUTION_KINDS:
                value = _legs_value(event, "out", price_source)
                basis += value
                _accrue(contributed, event, "out")
            elif kind in _EXTRACTION_KINDS:
                extracted = _legs_value(event, "in", price_source)
                holdings_value = _holdings_value(contributed, event, price_source)
                fraction = 1.0 if holdings_value <= 0 else min(1.0, extracted / holdings_value)
                released = fraction * basis
                realized += extracted - released
                basis -= released
                _drain(contributed, event, "in")
            elif kind in _INCOME_KINDS:
                realized += _legs_value(event, "in", price_source)
            elif kind in _DEBT_KINDS:
                if kind == "borrow":
                    debt += _legs_value(event, "in", price_source)
                else:
                    repaid = _legs_value(event, "out", price_source)
                    released = min(repaid, debt)
                    realized += released - repaid
                    debt -= released
            elif kind == "swap":
                # Average-cost conversion (Plan 0084): reshuffle value within the
                # position — realize the block-time execution delta, drain the sold
                # token, accrue the bought token — but leave `basis` untouched (no
                # capital entered or left), so a swap can't corrupt cost basis.
                value_out = _legs_value(event, "out", price_source)
                value_in = _legs_value(event, "in", price_source)
                realized += value_in - value_out
                _drain(contributed, event, "out")
                _accrue(contributed, event, "in")
            elif kind == "custody_move":
                # Staking/unstaking the LP receipt in/out of the gauge moves no
                # underlying value — a no-op (Plan 0084 / ADR-0079).
                pass
            else:
                # liquidation / unclassified: classified, not booked — an honest
                # incomplete beats invented arithmetic (ADR-0036).
                notes.append(f"unbooked {kind} event {event.tx_hash}")
        if notes:
            return _incomplete(position, notes)
        if position.kind == "lending_borrow":
            unrealized = debt - position.usd_value
            remaining: float | None = debt
        else:
            unrealized = position.usd_value - basis
            remaining = basis
        vs_hodl: float | None = None
        if position.kind == "lp":
            income = _income_total(events, price_source)
            hodl = _amounts_value(contributed, position, as_of, price_source)
            vs_hodl = (position.usd_value + income) - hodl
        for figure in (realized, unrealized, remaining, vs_hodl):
            if figure is not None and not math.isfinite(figure):
                return _incomplete(position, ["non-finite figure in replay arithmetic"])
        return PositionPnl(
            position_id=position.position_id,
            realized_usd=realized,
            unrealized_usd=unrealized,
            cost_basis_usd=remaining,
            vs_hodl_usd=vs_hodl,
            incomplete=False,
            notes=notes,
        )
    except _MissingPrice as gap:
        return _incomplete(
            position,
            [f"no block-time price for {gap.token} at ts={gap.ts}"],
        )


def _incomplete(position: DefiPosition, notes: list[str]) -> PositionPnl:
    return PositionPnl(
        position_id=position.position_id,
        realized_usd=None,
        unrealized_usd=None,
        cost_basis_usd=None,
        vs_hodl_usd=None,
        incomplete=True,
        notes=notes,
    )


def _price_at(
    price_source: HistoricalPriceSource,
    event: PositionEvent,
    address: str | None,
    ts: int,
) -> float:
    price = price_source.fetch_price(chain=event.chain, address=address, ts=ts)
    if price is None:
        raise _MissingPrice(token_key(event.chain, address), ts)
    return price


def _legs_value(
    event: PositionEvent,
    direction: str,
    price_source: HistoricalPriceSource,
) -> float:
    ts = int(event.mined_at.timestamp())
    total = 0.0
    for leg in event.legs:
        if leg.direction != direction:
            continue
        total += leg.amount * _price_at(price_source, event, leg.address, ts)
    return total


def _accrue(contributed: dict[str, float], event: PositionEvent, direction: str) -> None:
    for leg in event.legs:
        if leg.direction != direction:
            continue
        key = token_key(event.chain, leg.address)
        contributed[key] = contributed.get(key, 0.0) + leg.amount


def _drain(contributed: dict[str, float], event: PositionEvent, direction: str) -> None:
    for leg in event.legs:
        if leg.direction != direction:
            continue
        key = token_key(event.chain, leg.address)
        contributed[key] = max(0.0, contributed.get(key, 0.0) - leg.amount)


def _holdings_value(
    contributed: dict[str, float],
    event: PositionEvent,
    price_source: HistoricalPriceSource,
) -> float:
    """The net contributed amounts valued at the event's block — the
    denominator of the value-weighted extraction fraction. Insertion-ordered
    dict iteration (accrual order), never set iteration."""
    ts = int(event.mined_at.timestamp())
    total = 0.0
    for key, amount in contributed.items():
        if amount <= 0:
            continue
        address = None if key.startswith("coingecko:") else key.split(":", 1)[1]
        total += amount * _price_at(price_source, event, address, ts)
    return total


def _income_total(events: list[PositionEvent], price_source: HistoricalPriceSource) -> float:
    total = 0.0
    for event in events:
        if event.kind in _INCOME_KINDS:
            total += _legs_value(event, "in", price_source)
    return total


def _amounts_value(
    contributed: dict[str, float],
    position: DefiPosition,
    as_of: datetime,
    price_source: HistoricalPriceSource,
) -> float:
    """The HODL benchmark: net contributed amounts valued at `as_of`."""
    ts = int(as_of.timestamp())
    total = 0.0
    for key, amount in contributed.items():
        if amount <= 0:
            continue
        address = None if key.startswith("coingecko:") else key.split(":", 1)[1]
        price = price_source.fetch_price(chain=position.chain, address=address, ts=ts)
        if price is None:
            raise _MissingPrice(key, ts)
        total += amount * price
    return total


__all__ = ["PositionPnl", "WalletPnl", "compute_wallet_pnl"]
