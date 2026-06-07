"""LP deep-state enrichment (Plan 0034 phase 5).

Discovery (Zerion, `data/adapters/zerion.py`) returns *which* LP positions a
wallet holds with their `pool_address`, but leaves the concentrated-liquidity
detail blank (`tick_lower`/`tick_upper`/`in_range`/`current_tick`/
`uncollected_fees` stay `None`). This step folds in that detail: after discovery,
for each `kind="lp"` position carrying a `pool_address` it reads the on-chain
state through an `LpPositionDetailSource` (ADR-0031) and returns a new position
set with the LP-detail fields populated.

Shape-aware, one routing path (Plan 0048). The source's `resolve_univ3_token_id`
is the discriminator: it probes the `pool_address` and returns the position NFT
`tokenId` for a concentrated-liquidity shape (staked-CL via the gauge, or
unstaked-CL via the wallet's `NonfungiblePositionManager`), or `None` for a v2
constant-product pool (no ticks) or a wallet that holds no matching position. So
enrichment always resolves first, then reads detail by `tokenId` — it no longer
branches on the protocol display string (the old `"uniswap" in protocol`), which
could not tell a v2 farm from a staked-CL farm.

Two disciplines bound the step:

- **Best-effort.** Enrichment is additive depth, not the scan's reason for being.
  A per-position failure (no RPC URL for the chain, an upstream outage, an
  unresolved Uni-v3 tokenId, a shape-broken read) leaves *that* position at
  discovery depth and never fails the whole scan — discovery's "never silently
  zero" contract is about discovery's own numbers, which enrichment does not
  touch (`usd_value` is unchanged).
- **Serialized + spaced.** Each LP multiplies on-chain calls, and the free-tier
  RPC / Zerion limits trip under burst (the survey observed 429s cleared by
  ~1.1s spacing). Reads run one position at a time with a spacing pause between
  them (ADR-0034's "deliberate, never reactive" cadence, here a hard constraint).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from pydantic import ValidationError

from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.errors import UpstreamDataError
from market_analyser.data.sources import LpPositionDetailSource
from market_analyser.defi.models import Chain, DefiPosition, LpPositionDetail

# Spacing between consecutive per-LP detail reads. The Zerion-API survey observed
# free-tier 429s under burst cleared by ~1.1s inter-request spacing; the same
# deliberate cadence applies to the RPC reads enrichment multiplies.
_INTER_POSITION_SECONDS = 1.1


def enrich_lp_positions(
    positions: Sequence[DefiPosition],
    source: LpPositionDetailSource,
    *,
    owner: str,
    sleep: Callable[[float], None] = time.sleep,
) -> list[DefiPosition]:
    """Return `positions` with each enrichable LP position deepened by its on-chain
    detail; non-LP positions (and LPs whose detail can't be read) pass through
    unchanged. Order is preserved (deterministic). `owner` is the wallet address,
    needed to resolve Uni-v3 position NFTs. `sleep` is injectable so tests don't
    actually pause."""
    enriched: list[DefiPosition] = []
    # Chains whose detail source is unconfigured (no RPC URL): once one position
    # on a chain fails config, the rest are skipped without re-attempting. A list
    # (not a set) keeps the no-set-iteration discipline even though it is only
    # membership-tested.
    unconfigured: list[Chain] = []
    made_a_read = False
    for position in positions:
        if not _is_enrichable(position) or position.chain in unconfigured:
            enriched.append(position)
            continue
        if made_a_read:
            sleep(_INTER_POSITION_SECONDS)
        made_a_read = True
        try:
            detail = _fetch_detail(source, position, owner)
        except LpDetailConfigError:
            unconfigured.append(position.chain)
            enriched.append(position)
            continue
        except (UpstreamDataError, ValidationError, ValueError):
            enriched.append(position)  # best-effort: leave at discovery depth
            continue
        if detail is None:  # Uni-v3 tokenId unresolved — nothing to fold
            enriched.append(position)
            continue
        enriched.append(_fold_detail(position, detail))
    return enriched


def _is_enrichable(position: DefiPosition) -> bool:
    return position.kind == "lp" and bool(position.pool_address)


def _fetch_detail(
    source: LpPositionDetailSource, position: DefiPosition, owner: str
) -> LpPositionDetail | None:
    """Read one LP position's detail. The shape-aware resolver returns the position
    NFT `tokenId` (staked-CL or unstaked-CL) or `None` (v2 AMM / no matching
    position); a `None` means there is nothing to read, so the position is left at
    discovery depth."""
    pool_address = position.pool_address
    assert pool_address is not None  # guarded by `_is_enrichable`
    token_id = source.resolve_univ3_token_id(
        chain=position.chain, pool_address=pool_address, owner=owner
    )
    if token_id is None:
        return None
    return source.fetch_lp_detail(
        chain=position.chain, pool_address=pool_address, token_id=token_id
    )


def _fold_detail(position: DefiPosition, detail: LpPositionDetail) -> DefiPosition:
    """Return a copy of `position` with the LP-detail fields populated."""
    return position.model_copy(
        update={
            "tick_lower": detail.tick_lower,
            "tick_upper": detail.tick_upper,
            "current_tick": detail.current_tick,
            "in_range": detail.in_range,
            "uncollected_fees": list(detail.uncollected_fees),
        }
    )


__all__ = ["enrich_lp_positions"]
