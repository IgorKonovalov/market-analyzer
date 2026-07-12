"""Economic-event taxonomy mapping — `DecodedTx` → `PositionEvent`
(Plan 0035 phase 5, the classification layer ADR-0036 calls "the heaviest
piece and the correctness risk").

Maps each decoded transaction onto the **fixed** ADR-0036 taxonomy, per
discovered position: `add_liquidity` / `remove_liquidity` (LP), `supply` /
`withdraw_supply` / `borrow` / `repay` (lending), `swap`, `fee_claim` (LP
trading fees), `reward_claim` (emissions), `liquidation`. A transaction that
joins a position but fits no kind is surfaced as **`unclassified` — never
silently dropped** (the engine flags the position's P&L; a gap must be
visible, not absorbed).

**Join rule (deterministic, precision-first).** A transaction belongs to a
position when:
1. any of its acts' `contract_address` equals the position's `pool_address`
   — or, translated through the caller-supplied **gauge→pool map**, equals a
   gauge that distributes for that pool (Plan 0084 / ADR-0079: an Aerodrome
   `getReward` tx carries the gauge, not the pool, as its contract, so without
   the map its emissions cannot be attributed); both lowercased; else
2. exactly **one** position's token-address set contains every token the
   transaction moved (the token fallback). Zero or multiple candidates join
   nothing — an ambiguous join would produce plausible-looking wrong P&L,
   which is worse than an honest gap.
A transaction that joins no discovered position produces no event: this
engine reconstructs *per-position* P&L (ADR-0036), not whole-wallet token
accounting.

**Gauge-joined classification.** A transaction that joined *via* the gauge map
is an emissions claim (`getReward` → `reward_claim`), a fee route, or a custody
move (staking/unstaking the LP into/out of the gauge → `custody_move`, no basis
change); method hints are reliable in that context.

**Bare custody move (non-gauge).** A plain outbound transfer of a position's own
token — `operation_type` `send`, a single leg, and no lifecycle hint (Plan 0087 /
ADR-0081) — moves the position's units out to another of the owner's wallets with
no market event, so it books as the same no-op `custody_move` (basis-neutral).
Scoped to `send` (outbound), not `receive`: an inbound single-leg transfer already
classifies below (pool tokens → `fee_claim`, outside tokens → `reward_claim`,
Plan 0035), and the wallet's residual *unclassified* transfers are outbound sends —
a single inbound receive is never unclassified — so this narrowing (the plan-Risks
"the fixture narrows it" steer) matches the evidence without reversing Plan 0035.
This is the ADR-0081 accepted-limitation seam: a genuine *external* send is assumed
a custody move until a known-own-address registry can tell them apart (a followup).

The `gauge_map` is a **pure input** (`{gauge_address: pool_address}`, lowercased)
resolved and snapshotted in the job layer, so `map_events` stays a pure function
of `(transactions, positions, gauge_map)` — no I/O, no wall clock, no set
iteration — and the same inputs always yield the same events in the same order.

**Classification** derives from `operation_type` first (deposit/withdraw
resolve LP-vs-lending off the joined position's `kind`), then from act
hints — method names and act types, matched lowercased against small named
vocabularies — then a directional claim heuristic: an all-inbound transfer
set whose tokens all belong to the pool is a `fee_claim`; all-inbound tokens
from outside the pool are a `reward_claim`. Failed / pending transactions
move no assets and are skipped (purity is covered above).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.defi.models import Chain, DefiPosition
from market_analyser.defi.tx_models import DecodedTx, TxTransfer

EventKind = Literal[
    "add_liquidity",
    "remove_liquidity",
    "supply",
    "withdraw_supply",
    "borrow",
    "repay",
    "swap",
    "fee_claim",
    "reward_claim",
    "custody_move",
    "liquidation",
    "unclassified",
]

# Act-hint vocabularies (matched lowercased, whole-token). Named constants so a
# reviewer can audit the classification surface in one place (ADR-0036 risk
# note). Method names come from Zerion's decoded `application_metadata.method`.
_LIQUIDATION_TOKENS = frozenset({"liquidation", "liquidationcall", "liquidate"})
_FEE_CLAIM_METHODS = frozenset({"collect", "collectfees", "claimfees"})
_REWARD_CLAIM_METHODS = frozenset({"getreward", "getrewards", "claimrewards", "claimreward"})
_ADD_HINTS = frozenset({"deposit", "stake", "addliquidity", "mint"})
_REMOVE_HINTS = frozenset({"withdraw", "unstake", "removeliquidity", "burn"})
# Gauge stake/unstake methods (Plan 0084): a gauge-joined transaction bearing one
# of these moves the LP token/NFT into or out of the gauge — a custody move with
# no basis change, distinct from the emissions claim (`getReward`) handled above.
_GAUGE_CUSTODY_METHODS = frozenset({"deposit", "stake", "withdraw", "unstake"})
# Act hints that signal a swap even when Zerion's `operation_type` is not "trade" —
# an aggregator/router routes the swap as an "execute", but the decode still carries
# a "trade" act (Plan 0087 smoke follow-up; refines ADR-0079's swap booking).
_SWAP_ACT_HINTS = frozenset({"trade", "swap"})
# Any add/remove/fee/reward/liquidation lifecycle hint. It BLOCKS two shortcuts that
# must never swallow a compound operation: the bare-transfer custody move (Plan 0087
# / ADR-0081) and the aggregator-swap recognition below — a `send`/`receive` or an
# aggregator "execute" carrying one of these is a lifecycle event, not a plain
# custody move or a pure swap, so it stays honest (precision-first).
_LIFECYCLE_HINTS = (
    _LIQUIDATION_TOKENS | _FEE_CLAIM_METHODS | _REWARD_CLAIM_METHODS | _ADD_HINTS | _REMOVE_HINTS
)


class PositionEvent(BaseModel):
    """One economic event in a position's history, in replay order. `legs` are
    the transaction's boundary-validated transfers; the engine prices each at
    `mined_at` (the block-time rule), never at Zerion's inline figures."""

    model_config = ConfigDict(frozen=True)

    kind: EventKind
    position_id: str = Field(min_length=1)
    chain: Chain
    tx_hash: str = Field(min_length=1)
    mined_at: datetime
    block: int = Field(ge=0)
    in_block_index: int = Field(ge=0)
    legs: list[TxTransfer] = Field(default_factory=list)


def map_events(
    transactions: list[DecodedTx],
    positions: list[DefiPosition],
    gauge_map: Mapping[str, str] | None = None,
) -> list[PositionEvent]:
    """Classify every position-joined transaction, preserving the incoming
    (block, in-block) order. Pure and deterministic; `unclassified` is a
    first-class output, never a drop.

    `gauge_map` maps a gauge contract address to the pool address it distributes
    for (Plan 0084 / ADR-0079), both lowercased. It is resolved and snapshotted in
    the job layer so this function stays pure; an empty/absent map reproduces the
    pre-0084 behavior exactly (no gauge translation)."""
    resolved_gauges: dict[str, str] = (
        {} if gauge_map is None else {g.lower(): p.lower() for g, p in gauge_map.items()}
    )
    by_pool: dict[str, DefiPosition] = {}
    for candidate in positions:
        if candidate.pool_address is not None:
            by_pool.setdefault(candidate.pool_address.lower(), candidate)
    events: list[PositionEvent] = []
    for tx in transactions:
        if tx.status != "confirmed":
            continue  # failed/pending transactions move no assets
        if not tx.transfers:
            # A transaction that moves no assets carries no economic event for
            # per-position P&L — an ERC-20 `approve` is the dominant case (Plan
            # 0084 phase-6 smoke: 261 approvals, all zero-transfer). Without this
            # such a tx joins a position by its contract and surfaces as a spurious
            # `unclassified`, nulling the whole position. Skip like a failed tx.
            continue
        joined = _join(tx, by_pool, positions, resolved_gauges)
        if joined is None:
            continue  # not part of any discovered position's history
        position, via_gauge = joined
        events.append(
            PositionEvent(
                kind=_classify(tx, position, via_gauge),
                position_id=position.position_id,
                chain=tx.chain,
                tx_hash=tx.hash,
                mined_at=tx.mined_at,
                block=tx.mined_at_block,
                in_block_index=tx.in_block_index,
                legs=list(tx.transfers),
            )
        )
    return events


def _join(
    tx: DecodedTx,
    by_pool: dict[str, DefiPosition],
    positions: list[DefiPosition],
    gauge_map: Mapping[str, str],
) -> tuple[DefiPosition, bool] | None:
    """Join `tx` to a position; the bool is `via_gauge` — True when the match came
    through the gauge→pool translation (so the classifier treats it as a gauge
    interaction). `None` when nothing joins."""
    # 1. Act-contract match — the precise key. A direct pool match wins; else a
    #    gauge address translated to its pool (Plan 0084).
    for act in tx.acts:
        if act.contract_address is None:
            continue
        addr = act.contract_address.lower()
        position = by_pool.get(addr)
        if position is not None and position.chain == tx.chain:
            return position, False
        pool_addr = gauge_map.get(addr)
        if pool_addr is not None:
            gauged = by_pool.get(pool_addr)
            if gauged is not None and gauged.chain == tx.chain:
                return gauged, True
    # 2. Token fallback — only on an unambiguous single candidate; never a gauge.
    moved = {t.address.lower() for t in tx.transfers if t.address is not None}
    if not moved:
        return None
    candidates: list[DefiPosition] = []
    for position in positions:
        if position.chain != tx.chain:
            continue
        held = {t.address.lower() for t in position.tokens}
        if moved <= held:
            candidates.append(position)
    return (candidates[0], False) if len(candidates) == 1 else None


def _classify(tx: DecodedTx, position: DefiPosition, via_gauge: bool = False) -> EventKind:
    if via_gauge:
        return _classify_gauge(tx)
    is_lp = position.kind == "lp"
    if tx.operation_type == "borrow":
        return "borrow"
    if tx.operation_type == "repay":
        return "repay"
    if tx.operation_type == "deposit":
        return "add_liquidity" if is_lp else "supply"
    if tx.operation_type == "withdraw":
        return "remove_liquidity" if is_lp else "withdraw_supply"
    if tx.operation_type == "trade":
        return "swap"

    hints = _act_hints(tx)
    if hints & _LIQUIDATION_TOKENS:
        return "liquidation"
    if hints & _FEE_CLAIM_METHODS:
        return "fee_claim"
    if hints & _REWARD_CLAIM_METHODS:
        return "reward_claim"
    directions = {t.direction for t in tx.transfers}
    if hints & _ADD_HINTS and directions == {"out"}:
        return "add_liquidity" if is_lp else "supply"
    if hints & _REMOVE_HINTS and directions == {"in"}:
        return "remove_liquidity" if is_lp else "withdraw_supply"

    # Aggregator/router swap (Plan 0087 smoke follow-up; refines ADR-0079's swap
    # booking). An aggregator-routed swap arrives with operation_type "execute"
    # (not "trade"), so the op_type swap branch above misses it, yet Zerion still
    # decodes a "trade" act and the transfer set is a clean two-sided conversion.
    # Recognize a `swap` ONLY under tight guards so a zap (a swap that also adds or
    # removes liquidity) is never mis-booked as a pure swap and corrupt basis
    # (ADR-0079 names this risk): a trade/swap act present, exactly one out-leg and
    # one in-leg of DIFFERENT tokens, and no lifecycle hint — else it is a compound
    # op and stays honest `unclassified`. (Placed after the add/remove-hint checks
    # so a real zap-in still books as add_liquidity, not swap.)
    out_legs = [t for t in tx.transfers if t.direction == "out"]
    in_legs = [t for t in tx.transfers if t.direction == "in"]
    if (
        hints & _SWAP_ACT_HINTS
        and len(out_legs) == 1
        and len(in_legs) == 1
        and _leg_token(out_legs[0]) != _leg_token(in_legs[0])
        and not (hints & _LIFECYCLE_HINTS)
    ):
        return "swap"

    # Bare custody move (Plan 0087 / ADR-0081): a plain outbound transfer of the
    # position's own token — `operation_type` `send`, a single leg, and no
    # lifecycle hint — moves the position's units out to another of the owner's
    # wallets with no market event, so it books as a no-op `custody_move`
    # (basis-neutral), the same treatment gauge stake/unstake gets. Scoped to
    # `send` (outbound), NOT `receive`: an inbound single-leg transfer already has
    # an established interpretation below (a pool-token receipt is a `fee_claim`,
    # an outside-token receipt a `reward_claim`, Plan 0035), and the wallet's two
    # residual *unclassified* transfers are outbound sends — an inbound receive is
    # never unclassified, so narrowing here matches the real evidence (the ADR-0081
    # / plan-Risks "the fixture narrows it" steer) without reversing Plan 0035. The
    # single-leg requirement keeps it distinct from a two-token withdrawal; any
    # lifecycle hint blocks the shortcut (precision-first).
    if tx.operation_type == "send" and len(tx.transfers) == 1 and not (hints & _LIFECYCLE_HINTS):
        return "custody_move"

    # Directional claim heuristic: an all-inbound transfer set is a claim —
    # from the pool's own tokens it's trading fees, from outside it's an
    # emissions reward (e.g. AERO streamed to a WETH/USDC gauge position).
    if tx.transfers and directions == {"in"}:
        pool_tokens = {t.address.lower() for t in position.tokens}
        incoming = {t.address.lower() for t in tx.transfers if t.address is not None}
        if incoming and incoming <= pool_tokens:
            return "fee_claim"
        return "reward_claim"
    return "unclassified"


def _classify_gauge(tx: DecodedTx) -> EventKind:
    """Classify a transaction that joined via the gauge→pool map (Plan 0084 /
    ADR-0079). In the gauge context the operation is one of: an emissions claim
    (`getReward` → `reward_claim`, the dominant real case — 35 events on the test
    wallet), a fee route (`collect` → `fee_claim`), or a custody move (staking /
    unstaking the LP into or out of the gauge → `custody_move`, no basis change).
    Method hints are reliable here, so we lead with them; an inbound-only transfer
    with no stake hint is emissions streamed from the gauge. Precision-first: a
    shape that fits nothing is an honest `unclassified`, never a guess."""
    hints = _act_hints(tx)
    if hints & _REWARD_CLAIM_METHODS:
        return "reward_claim"
    if hints & _FEE_CLAIM_METHODS:
        return "fee_claim"
    if hints & _GAUGE_CUSTODY_METHODS:
        return "custody_move"
    directions = {t.direction for t in tx.transfers}
    if tx.transfers and directions == {"in"}:
        # Inbound-only from the gauge with no stake method: emissions (e.g. AERO
        # streamed to the position) — the getReward-without-a-decoded-method case.
        return "reward_claim"
    if tx.transfers and directions == {"out"}:
        # Outbound-only into the gauge with no method: staking the LP token out —
        # a custody move, no basis change (the plan's "pure custody" assumption).
        return "custody_move"
    return "unclassified"


def _leg_token(leg: TxTransfer) -> str | None:
    """A transfer's token identity for equality checks: the lowercased contract
    address, or `None` for the native coin (which has no contract). A swap converts
    between two *different* tokens, so its out-leg and in-leg tokens must differ."""
    return leg.address.lower() if leg.address is not None else None


def _act_hints(tx: DecodedTx) -> frozenset[str]:
    tokens: list[str] = []
    for act in tx.acts:
        tokens.append(act.type.lower())
        if act.method_name is not None:
            tokens.append(act.method_name.lower())
    return frozenset(tokens)


__all__ = ["EventKind", "PositionEvent", "map_events"]
