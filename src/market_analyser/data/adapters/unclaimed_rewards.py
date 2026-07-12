"""Unclaimed-reward on-chain reader — RPC `eth_call` (Plan 0084 phase 5, ADR-0079).

Reads a gauge-staked position's **currently owed-but-unclaimed** emissions via the
gauge's `earned()` — the value transaction replay is structurally blind to (no
claim tx exists yet). Implements the read-only `UnclaimedRewardsSource` Protocol
(`data/sources.py`, ADR-0031) and is reached only through that seam.

The read reuses the LP-detail machinery (Plan 0034/0048): the same read-only
transport (`rpc_eth_call`), the same per-chain RPC-URL secret (`rpc_url_for`,
ADR-0038), the same ABI codec, and the shape-aware `resolve_univ3_token_id`
(injected `LpPositionDetailSource`) that already resolves a staked-CL position's
`tokenId`. For a staked concentrated-liquidity position Zerion returns the **gauge**
as `pool_address`, so the read chain is: `gauge.rewardToken()` (also the "is this a
gauge?" probe — a revert means no), `gauge.earned(owner, tokenId)` (Slipstream CL)
or `gauge.earned(owner)` (vAMM), then the reward token's `symbol()`/`decimals()` to
label and scale it.

`usd_value` is priced best-effort at the **current** time through the injected
`HistoricalPriceSource` (provenance: current price, not block-time — this is a "now"
read, outside the P&L determinism guarantee); it is `None` when no price source is
wired or the token cannot be priced. Read-only and precision-first: a non-gauge
address, a zero `earned()`, or any unreadable leg yields `[]` (or an unpriced but
honest amount), never a guess. Transport failures surface as the shared typed
taxonomy for the caller (`augment_with_unclaimed`) to swallow best-effort.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.adapters.lp_detail import (
    LpDetailError,
    _addr_arg,
    _call,
    _decode_address,
    _decode_string,
    _decode_uint,
    _uint_arg,
    rpc_eth_call,
    rpc_url_for,
)
from market_analyser.data.errors import UpstreamDataError
from market_analyser.data.sources import HistoricalPriceSource, LpPositionDetailSource
from market_analyser.defi.models import Chain, DefiPosition, RewardAmount
from market_analyser.persistence.secrets import SecretsStore

_SOURCE = "unclaimed-rewards-rpc"
_CACHE_TTL_SECONDS = 0.0
_INTER_REQUEST_SECONDS = 0.5

# Function selectors (keccak256(signature)[:4]); pinned by the selector self-check
# in the test, exactly as `lp_detail.py` pins its own.
_SEL_REWARD_TOKEN = "0xf7c618c1"  # rewardToken() -> address
_SEL_EARNED_CL = "0x3e491d47"  # earned(address,uint256) -> uint256 (Slipstream CL gauge)
_SEL_EARNED_V2 = "0x008cc262"  # earned(address) -> uint256 (vAMM gauge)
_SEL_SYMBOL = "0x95d89b41"  # symbol() -> string
_SEL_DECIMALS = "0x313ce567"  # decimals() -> uint8


class RpcUnclaimedRewardsAdapter:
    """Reads a gauge-staked position's owed-but-unclaimed rewards over JSON-RPC."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        lp_detail: LpPositionDetailSource,
        price_source: HistoricalPriceSource | None = None,
        http_client: ResilientHttpClient | None = None,
        now: Callable[[], int] = lambda: int(time.time()),
        inter_request_seconds: float = _INTER_REQUEST_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secrets = secrets_store
        self._lp_detail = lp_detail
        self._price_source = price_source
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        self._now = now
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep

    def fetch_unclaimed(self, *, position: DefiPosition, owner: str) -> list[RewardAmount]:
        """The rewards `owner` is currently owed on `position`'s gauge, or `[]` when
        the position is not gauge-staked or owes nothing. Best-effort per leg; the
        caller swallows a transport failure (an owed-reward gap never fails the
        P&L)."""
        gauge = position.pool_address
        if gauge is None:
            return []
        rpc_url = rpc_url_for(self._secrets, position.chain)
        reward_token = self._reward_token(rpc_url, gauge)
        if reward_token is None:
            return []  # not a gauge (rewardToken() reverted) — nothing to read
        token_id = self._lp_detail.resolve_univ3_token_id(
            chain=position.chain, pool_address=gauge, owner=owner
        )
        earned_raw = self._earned(rpc_url, gauge, owner, token_id)
        if earned_raw <= 0:
            return []  # nothing owed
        symbol = _decode_string(self._call(rpc_url, reward_token, _SEL_SYMBOL))
        decimals = _decode_uint(self._call(rpc_url, reward_token, _SEL_DECIMALS), word=0)
        amount = earned_raw / (10**decimals)
        unit_price = self._price(position.chain, reward_token)
        return [
            RewardAmount(
                symbol=symbol,
                amount=amount,
                usd_value=amount * unit_price if unit_price is not None else None,
            )
        ]

    def _reward_token(self, rpc_url: str, gauge: str) -> str | None:
        """The gauge's reward-token address, or `None` when `gauge` is not a gauge
        (the getter reverts) or returns the zero address. A revert is routing
        signal, not an error (mirrors the LP-detail shape probe)."""
        try:
            token = _decode_address(self._call(rpc_url, gauge, _SEL_REWARD_TOKEN))
        except LpDetailError:
            return None
        return token if int(token, 16) != 0 else None

    def _earned(self, rpc_url: str, gauge: str, owner: str, token_id: int | None) -> int:
        """`gauge.earned(owner, tokenId)` (staked CL) or `gauge.earned(owner)` (vAMM,
        when no NFT id resolved). `0` when the read reverts — an unreadable owed
        amount is honestly nothing, never a guess."""
        if token_id is not None:
            data = _call(_SEL_EARNED_CL, _addr_arg(owner), _uint_arg(token_id))
        else:
            data = _call(_SEL_EARNED_V2, _addr_arg(owner))
        try:
            return _decode_uint(self._call(rpc_url, gauge, data), word=0)
        except LpDetailError:
            return 0

    def _price(self, chain: Chain, token: str) -> float | None:
        """Best-effort current USD price PER TOKEN of the reward token (provenance:
        now, not block-time); the caller multiplies by the amount. `None` when no
        price source is wired or it has no coverage."""
        if self._price_source is None:
            return None
        try:
            return self._price_source.fetch_price(chain=chain, address=token, ts=self._now())
        except (ValueError, UpstreamDataError):
            return None

    def _call(self, rpc_url: str, to: str, data: str) -> bytes:
        """One paced read-only `eth_call` (pacing mirrors the LP-detail cadence)."""
        self._sleep(self._inter_request_seconds)
        return rpc_eth_call(self._http, rpc_url, to, data)


__all__ = ["RpcUnclaimedRewardsAdapter"]
