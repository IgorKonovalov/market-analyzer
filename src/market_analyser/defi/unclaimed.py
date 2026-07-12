"""Unclaimed-reward augmentation (Plan 0084 phase 5, ADR-0079).

Transaction replay (`pnl.py`) reconstructs realized/unrealized P&L from claim
events, but is structurally blind to emissions **owed right now but not yet
claimed** — there is no claim tx to replay. Yet that owed value is the first
question a user asks of an open farming position (e.g. ~34 AERO still owed on the
test wallet's AAVE/WETH position). This step reads it on-chain via the gauge's
`earned()` through an `UnclaimedRewardsSource` (ADR-0031) and folds a labeled,
current-state `unclaimed_rewards` field onto the already-computed `WalletPnl`.

Two disciplines bound it, mirroring `enrichment.py`:

- **Outside the determinism guarantee.** This is a live "now" read, in the same
  category as discovery's current `usd_value`, so it runs *after* the pure replay
  and is attached by `model_copy`. The replay figures — and their byte-identical
  re-run guarantee — are untouched (ADR-0036); the field is excluded from that
  guarantee by construction (the engine never produces it).
- **Best-effort, never fails the P&L.** A per-position read failure (no RPC URL, an
  outage, an unresolved token id, a shape-broken read) leaves that position's
  `unclaimed_rewards` `None` and never raises — an owed-reward gap must not null a
  reconstructed P&L. Reads are serialized and spaced (the free-tier RPC cadence).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from pydantic import ValidationError

from market_analyser.data.errors import UpstreamDataError
from market_analyser.data.sources import UnclaimedRewardsSource
from market_analyser.defi.models import DefiPosition, RewardAmount
from market_analyser.defi.pnl import PositionPnl, WalletPnl

# Spacing between consecutive per-position `earned()` reads, matching enrichment's
# deliberate RPC cadence (free-tier limits trip under burst).
_INTER_POSITION_SECONDS = 1.1


def augment_with_unclaimed(
    pnl: WalletPnl,
    positions: Sequence[DefiPosition],
    source: UnclaimedRewardsSource,
    *,
    owner: str,
    sleep: Callable[[float], None] = time.sleep,
) -> WalletPnl:
    """Return `pnl` with per-position `unclaimed_rewards` folded in (and a wallet
    roll-up summed by symbol), read best-effort via `source`. Positions that are
    not gauge-staked, or that owe nothing, keep `unclaimed_rewards=None`. The
    replay-derived figures are unchanged. `owner` is the `earned()` account."""
    owed: dict[str, list[RewardAmount]] = {}  # position_id -> its unclaimed rewards
    made_a_read = False
    for position in positions:
        if not _is_gauge_stakeable(position):
            continue
        if made_a_read:
            sleep(_INTER_POSITION_SECONDS)
        made_a_read = True
        try:
            rewards = list(source.fetch_unclaimed(position=position, owner=owner))
        except (UpstreamDataError, ValidationError, ValueError):
            continue  # best-effort: an owed-reward gap never fails the P&L
        if rewards:
            owed[position.position_id] = rewards
    if not owed:
        return pnl
    new_positions = [
        position.model_copy(update={"unclaimed_rewards": owed[position.position_id]})
        if position.position_id in owed
        else position
        for position in pnl.positions
    ]
    return pnl.model_copy(
        update={"positions": new_positions, "unclaimed_rewards": _rollup(new_positions)}
    )


def _is_gauge_stakeable(position: DefiPosition) -> bool:
    """An LP position carrying a pool/gauge address is the shape that can hold a
    gauge stake (Zerion returns the gauge as `pool_address` for staked CL)."""
    return position.kind == "lp" and bool(position.pool_address)


def _rollup(positions: list[PositionPnl]) -> list[RewardAmount] | None:
    """Sum per-position unclaimed rewards by symbol (insertion-ordered — no set
    iteration). A symbol's `usd_value` total is the sum of its parts only when
    every part is priced; a single unpriced part makes the total honestly `None`."""
    amounts: dict[str, float] = {}
    usd_parts: dict[str, list[float | None]] = {}
    for position in positions:
        for reward in position.unclaimed_rewards or []:
            amounts[reward.symbol] = amounts.get(reward.symbol, 0.0) + reward.amount
            usd_parts.setdefault(reward.symbol, []).append(reward.usd_value)
    if not amounts:
        return None
    result: list[RewardAmount] = []
    for symbol, amount in amounts.items():
        parts = usd_parts[symbol]
        all_priced = all(p is not None for p in parts)
        total_usd = sum(p for p in parts if p is not None) if all_priced else None
        result.append(RewardAmount(symbol=symbol, amount=amount, usd_value=total_usd))
    return result


__all__ = ["augment_with_unclaimed"]
