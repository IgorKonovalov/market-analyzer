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
   (both lowercased) — the survey-confirmed discovery→deep join key; else
2. exactly **one** position's token-address set contains every token the
   transaction moved (the token fallback). Zero or multiple candidates join
   nothing — an ambiguous join would produce plausible-looking wrong P&L,
   which is worse than an honest gap.
A transaction that joins no discovered position produces no event: this
engine reconstructs *per-position* P&L (ADR-0036), not whole-wallet token
accounting.

**Classification** derives from `operation_type` first (deposit/withdraw
resolve LP-vs-lending off the joined position's `kind`), then from act
hints — method names and act types, matched lowercased against small named
vocabularies — then a directional claim heuristic: an all-inbound transfer
set whose tokens all belong to the pool is a `fee_claim`; all-inbound tokens
from outside the pool are a `reward_claim`. Failed / pending transactions
move no assets and are skipped. The mapping is a pure function of
`(transactions, positions)` — no I/O, no wall clock, no set iteration — so
the same inputs always yield the same events in the same order.
"""

from __future__ import annotations

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
) -> list[PositionEvent]:
    """Classify every position-joined transaction, preserving the incoming
    (block, in-block) order. Pure and deterministic; `unclassified` is a
    first-class output, never a drop."""
    by_pool: dict[str, DefiPosition] = {}
    for candidate in positions:
        if candidate.pool_address is not None:
            by_pool.setdefault(candidate.pool_address.lower(), candidate)
    events: list[PositionEvent] = []
    for tx in transactions:
        if tx.status != "confirmed":
            continue  # failed/pending transactions move no assets
        position = _join(tx, by_pool, positions)
        if position is None:
            continue  # not part of any discovered position's history
        events.append(
            PositionEvent(
                kind=_classify(tx, position),
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
) -> DefiPosition | None:
    # 1. Act-contract match — the precise key.
    for act in tx.acts:
        if act.contract_address is not None:
            position = by_pool.get(act.contract_address.lower())
            if position is not None and position.chain == tx.chain:
                return position
    # 2. Token fallback — only on an unambiguous single candidate.
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
    return candidates[0] if len(candidates) == 1 else None


def _classify(tx: DecodedTx, position: DefiPosition) -> EventKind:
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


def _act_hints(tx: DecodedTx) -> frozenset[str]:
    tokens: list[str] = []
    for act in tx.acts:
        tokens.append(act.type.lower())
        if act.method_name is not None:
            tokens.append(act.method_name.lower())
    return frozenset(tokens)


__all__ = ["EventKind", "PositionEvent", "map_events"]
