"""On-chain LP deep-state adapter — RPC `eth_call` over JSON-RPC (Plan 0034/0048).

The *depth* half of the DeFi program (ADR-0034): discovery (Zerion, `zerion.py`)
tells us *which* LP positions a wallet holds and a `pool_address`; this adapter
reads the precise on-chain state of one such position — its tick range, where the
pool's current tick sits relative to it (in-range status), and the fees accrued
but not yet collected. It implements the source-agnostic `LpPositionDetailSource`
Protocol (`data/sources.py`, ADR-0031) and is reached only through that seam.

**Three LP shapes, keyed differently (Plan 0048 — the gauge-indirection fix).**
Zerion's `pool_address` can point at three different contracts, so the adapter
*probes* to discover the shape rather than trusting the discovery class (which
cannot separate a v2 farm from a staked-CL farm) or the human pool name:

- **Staked Slipstream CL (gauge indirection).** For a gauge-staked concentrated
  position Zerion returns the **CL gauge** as `pool_address`. The gauge exposes
  neither `slot0()` nor a wallet-owned NFT; the position's tick state lives on an
  NFT *held by the gauge*. The read is a chain: `gauge.pool()` → the CLPool
  (`slot0` source), `gauge.nft()` → the `NonfungiblePositionManager`,
  `gauge.stakedValues(owner)` → the staked `tokenId`, `NPM.positions(tokenId)` →
  bounds + owed + token addresses. This is the case the 2026-06-05 live smoke
  decoded end-to-end (gauge `0x9564…88f1`, CLPool `0x4e50…ce51`, NPM `0xe1f8…
  8b53`, tokenId `232923`: ticks `84000..86200`, current `85198`, in range).
- **Unstaked CL (Uniswap-v3 / unstaked Slipstream).** `pool_address` is the pool
  and the wallet owns the position NFT, so the read keys on the NFT `tokenId`
  resolved by enumerating the owner's positions on the canonical
  `NonfungiblePositionManager` (two positions can share a pool with different
  ranges, so the pool alone is ambiguous).
- **v2 constant-product (Aerodrome/Velodrome volatile/stable).** No ticks exist;
  the resolver returns `None` so the enrichment step leaves the deep fields blank.

All reads use real Uniswap-v3-style ABIs (Slipstream is a Uni-v3 fork): `slot0()`
for the current tick, `positions(tokenId)` for the tick bounds + owed token
amounts + token addresses, and ERC-20 `symbol()`/`decimals()` to label and scale
the owed fees. Calls go through the shared `ResilientHttpClient` (ADR-0019); the
per-chain RPC URL is read **lazily** from the `SecretsStore` (ADR-0038 server-side
injection), so the adapter constructs before a URL is set and a read without one
fails typed (`LpDetailConfigError`), not at construction.

Errors are typed (done-when): a missing RPC URL or an unsupported chain raises
`LpDetailConfigError`; a 429 / 5xx / transport exhaustion raises the shared
`RateLimitedError` / `UpstreamUnavailableError`; a JSON-RPC error object (a
revert) or a shape-broken result raises `LpDetailError`. An out-of-range decoded
measurement surfaces as `pydantic.ValidationError` from the `LpPositionDetail`
boundary. The shape probe treats a revert as "this getter is absent" — it routes,
it never surfaces — so a misclassified address degrades to discovery depth rather
than corrupting the read (the Plan 0034 fail-safe property, preserved).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain, LpPositionDetail, PositionToken
from market_analyser.persistence.secrets import SecretKey, SecretsStore

_SOURCE = "lp-detail-rpc"

# Deep state is live/volatile like discovery (ADR-0034) and a read is request-
# triggered, so the adapter does not cache — each enrichment is a fresh read.
_CACHE_TTL_SECONDS = 0.0

# A deliberate pause before each RPC request. The configured Base provider enforces
# a strict per-second limit that JSON-RPC batching alone does not satisfy (2026-06-07
# smoke: the staked gauge chain 429s when bursted, clears at ~0.5s spacing). Batching
# cuts the request *count*; this paces the *rate*. Injectable so tests don't wait,
# and it composes with enrichment's separate inter-position spacing.
_INTER_REQUEST_SECONDS = 0.5

# The per-chain RPC-URL secret each target chain reads from (ADR-0038). Only the
# two chains the secrets schema reserves a URL for are deep-readable; a position
# on another target chain fails typed rather than silently empty.
_RPC_URL_KEYS: dict[Chain, SecretKey] = {
    "base": "base_rpc_url",
    "ethereum": "eth_rpc_url",
}

# Function selectors (first 4 bytes of keccak256(signature)). Slipstream is a
# Uniswap-v3 fork, so the pool/ERC-20 selectors are shared with Uni-v3. Every
# selector here is recomputed from its signature and pinned in a keccak self-check
# test (Plan 0048 risk: trust the selector only after the unit test agrees).
_SEL_SLOT0 = "0x3850c7bd"  # slot0() -> (sqrtPriceX96, tick, ...)
_SEL_TOKEN0 = "0x0dfe1681"  # token0() -> address
_SEL_TOKEN1 = "0xd21220a7"  # token1() -> address
_SEL_SYMBOL = "0x95d89b41"  # symbol() -> string
_SEL_DECIMALS = "0x313ce567"  # decimals() -> uint8
# positions(uint256 tokenId) on the NonfungiblePositionManager -> (nonce,
# operator, token0, token1, fee/tickSpacing, tickLower, tickUpper, liquidity,
# feeGrowthInside0, feeGrowthInside1, tokensOwed0, tokensOwed1).
_SEL_POSITIONS = "0x99fbab88"
# ERC-721 enumeration on the NonfungiblePositionManager, for resolving a wallet's
# unstaked-CL position token ids.
_SEL_BALANCE_OF = "0x70a08231"  # balanceOf(address) -> uint256
_SEL_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"  # tokenOfOwnerByIndex(address,uint256) -> uint256
# CL-gauge indirection (Plan 0048). `pool()`/`nft()` resolve the CLPool and the
# position manager; `stakedValues`/`stakedLength`/`stakedByIndex` enumerate the
# owner's staked NFTs held by the gauge (the wallet owns none directly).
_SEL_POOL = "0x16f0115b"  # pool() -> address (the CLPool, slot0 source)
_SEL_NFT = "0x47ccca02"  # nft() -> address (the NonfungiblePositionManager)
_SEL_STAKED_VALUES = "0x4b937763"  # stakedValues(address) -> uint256[]
_SEL_STAKED_BY_INDEX = "0x38463937"  # stakedByIndex(address,uint256) -> uint256
_SEL_STAKED_LENGTH = "0xae775c32"  # stakedLength(address) -> uint256

# Word index of each field in the `positions(tokenId)` return (12 words). The
# token addresses, tick bounds and owed amounts are what the detail needs; the
# rest (nonce/operator/fee/liquidity/feeGrowth) is skipped.
_POS_TOKEN0_WORD = 2
_POS_TOKEN1_WORD = 3
_POS_TICK_LOWER_WORD = 5
_POS_TICK_UPPER_WORD = 6
_POS_OWED0_WORD = 10
_POS_OWED1_WORD = 11

# Canonical Uniswap-v3 NonfungiblePositionManager per target chain. Ethereum /
# Arbitrum / Optimism share the original deployment address; Base has its own.
# (Only chains with a reserved RPC-URL secret are deep-readable — see
# `_RPC_URL_KEYS` — so resolution covers base / ethereum in practice.) Used only
# by the unstaked-CL path; the staked-CL path reads its NPM from `gauge.nft()`.
_NPM_ADDRESSES: dict[Chain, str] = {
    "ethereum": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "arbitrum": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "optimism": "0xc36442b4a4522e871399cd717abdd847ab11fe88",
    "base": "0x03a520b32c04bf3beef7beb72e919cf822ed34f1",
}

_WORD_BYTES = 32

# The LP shapes the probe distinguishes (Plan 0048). `staked_cl` reads the gauge
# chain; `bare_cl` reads the wallet-owned NFT; `v2_amm` has no ticks (skipped).
_LpShape = Literal["staked_cl", "bare_cl", "v2_amm"]


@dataclass(frozen=True)
class _Classification:
    """A pool_address's resolved LP shape, carried from `resolve_univ3_token_id`
    to the immediately-following `fetch_lp_detail` for the same address so the
    shape probe runs **once** per position, not twice (Plan 0048 burst fix).
    `clpool` is the CLPool from `gauge.pool()` and `npm` the position manager from
    `gauge.nft()` — both populated on the staked path, both `None` for bare CL
    (which reads the canonical NPM and the pool directly)."""

    shape: _LpShape
    clpool: str | None
    npm: str | None


class LpDetailError(ValueError):
    """A JSON-RPC error object (e.g. a revert), or a 2xx result whose shape/length
    the decode required but the payload lacked — raised at the adapter boundary
    before model construction (caught by the scan path as a malformed-response
    reason)."""


class LpDetailConfigError(UpstreamDataError):
    """No RPC URL is configured for the position's chain, or the chain has no
    reserved RPC-URL secret (deep reads cover only chains the secrets schema keys
    a URL for). A configuration failure surfaced through the typed upstream
    taxonomy so callers branch on a reason rather than a bare exception."""


class RpcLpDetailAdapter:
    """Reads a concentrated-liquidity LP position's on-chain detail over JSON-RPC."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        http_client: ResilientHttpClient | None = None,
        inter_request_seconds: float = _INTER_REQUEST_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        # A pause before each RPC request to stay under the provider's per-second
        # limit (batching cut the count, not the rate). Injected as a no-op by tests
        # so they don't actually wait.
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep
        # Per-position classification carried from `resolve_univ3_token_id` to the
        # next `fetch_lp_detail` for the same `pool_address` (lowercased), so the
        # shape probe runs once, not twice. Populated only when resolve yields a
        # token_id (the cases fetch follows); consumed + evicted by fetch.
        self._classified: dict[str, _Classification] = {}

    def fetch_lp_detail(
        self,
        *,
        chain: Chain,
        pool_address: str,
        token_id: int | None = None,
    ) -> LpPositionDetail:
        """Return the deep on-chain state of the LP position identified by
        `pool_address` + the resolved `token_id`.

        `token_id` is required (the enrichment step resolves it first via
        `resolve_univ3_token_id`, which is shape-aware). The read reuses the shape
        that resolve cached for this `pool_address`, so the gauge/pool probe runs
        **once** per position; a direct call with no prior resolve re-probes. The
        **staked-CL gauge chain** and the **unstaked-CL** NFT read are routed by the
        cached/probed shape.

        Raises `LpDetailConfigError` on a missing RPC URL / unsupported chain, the
        shared `RateLimitedError` / `UpstreamUnavailableError` on throttle / outage,
        and `LpDetailError` (or `pydantic.ValidationError`) on a shape-broken read —
        including a `token_id` of `None`, which has no read path since the one-hop
        `pool_address` read was removed (Plan 0048).
        """
        rpc_url = self._rpc_url_for(chain)
        if token_id is None:
            raise LpDetailError(
                "lp-detail: token_id is required — the one-hop pool_address read was "
                "replaced by the shape-aware gauge/NFT chain (Plan 0048)",
            )
        # Reuse the classification the preceding `resolve_univ3_token_id` cached for
        # this address (enrichment calls resolve→fetch back-to-back), so `gauge.pool()`
        # is probed once per position, not twice. A direct fetch with no prior resolve
        # falls back to a fresh probe.
        cls = self._classified.pop(pool_address.lower(), None)
        if cls is None:
            shape, clpool = self._classify_shape(rpc_url, pool_address)
            cls = _Classification(shape, clpool, None)
        if cls.shape == "staked_cl":
            assert cls.clpool is not None  # staked classification always resolves the CLPool
            return self._fetch_staked_cl(
                rpc_url, gauge=pool_address, clpool=cls.clpool, npm=cls.npm, token_id=token_id
            )
        if cls.shape == "bare_cl":
            return self._fetch_univ3(rpc_url, chain, pool_address, token_id)
        raise LpDetailError(
            "lp-detail: a v2 constant-product pool has no concentrated-liquidity detail",
        )

    def resolve_univ3_token_id(
        self,
        *,
        chain: Chain,
        pool_address: str,
        owner: str,
    ) -> int | None:
        """Resolve the position NFT `token_id` for `pool_address`, shape-aware
        (Plan 0048 generalizes the original Uni-v3-only resolver behind the
        unchanged seam name). Probes the shape and routes:

        - **staked CL** (`pool_address` is a CL gauge): `gauge.stakedValues(owner)`
          (falling back to `stakedLength` / `stakedByIndex`) → the staked NFT id.
        - **unstaked CL** (`pool_address` is a bare CL pool): enumerate the owner's
          NFTs on the canonical `NonfungiblePositionManager` and match the pool pair.
        - **v2 AMM** (neither a gauge nor a CL pool): `None` — no NFT, no ticks.

        Returns `None` when the wallet holds no matching position (or the shape has
        none). The enrichment step calls this first, then passes the id to
        `fetch_lp_detail`.

        Raises `LpDetailConfigError` on a missing RPC URL / a chain with no known
        position manager; transport / shape errors are typed as in `_eth_call`."""
        rpc_url = self._rpc_url_for(chain)
        key = pool_address.lower()
        shape, clpool = self._classify_shape(rpc_url, pool_address)
        if shape == "staked_cl":
            token_id, npm = self._resolve_staked_cl(rpc_url, gauge=pool_address, owner=owner)
            if token_id is None:
                return None
            self._classified[key] = _Classification("staked_cl", clpool, npm)
            return token_id
        if shape == "bare_cl":
            token_id = self._resolve_bare_cl_token_id(rpc_url, chain, pool_address, owner)
            if token_id is None:
                return None
            self._classified[key] = _Classification("bare_cl", None, None)
            return token_id
        return None  # v2 AMM: no NFT / no ticks

    # -- shape discrimination (the probe) -----------------------------------

    def _classify_shape(self, rpc_url: str, pool_address: str) -> tuple[_LpShape, str | None]:
        """Probe `pool_address` to discover its LP shape (Plan 0048 discriminator).

        `gauge.pool()` returning a non-zero address ⇒ the address is a CL gauge
        (staked CL); else a successful `slot0()` ⇒ a bare CL pool (unstaked CL);
        else (both revert) ⇒ a v2 constant-product pool with no ticks. Returns the
        shape and, for the staked case, the resolved CLPool address (so the caller
        need not re-read `gauge.pool()`). A revert is routing signal, not an error."""
        clpool = self._gauge_clpool_or_none(rpc_url, pool_address)
        if clpool is not None:
            return "staked_cl", clpool
        if self._is_cl_pool(rpc_url, pool_address):
            return "bare_cl", None
        return "v2_amm", None

    def _gauge_clpool_or_none(self, rpc_url: str, address: str) -> str | None:
        """The CLPool address from `gauge.pool()` if `address` is a CL gauge, else
        `None` (the getter reverted, or returned the zero address — `address` is
        not a gauge). The probe never raises on a revert: it routes."""
        try:
            pool = _decode_address(self._eth_call(rpc_url, address, _SEL_POOL))
        except LpDetailError:
            return None
        return pool if int(pool, 16) != 0 else None

    def _is_cl_pool(self, rpc_url: str, address: str) -> bool:
        """Whether `address` is a concentrated-liquidity pool — `slot0()` resolves
        to a tuple (a v2 constant-product pool has no `slot0` and reverts)."""
        try:
            _word(self._eth_call(rpc_url, address, _SEL_SLOT0), 1)  # needs ≥2 words
        except LpDetailError:
            return False
        return True

    # -- per-shape token-id resolution --------------------------------------

    def _resolve_staked_cl(
        self, rpc_url: str, *, gauge: str, owner: str
    ) -> tuple[int | None, str | None]:
        """The staked NFT `token_id` **and** the position manager (`gauge.nft()`) for
        a CL gauge: `gauge.stakedValues(owner)` gives the staked NFT ids (falling back
        to `stakedLength` + `stakedByIndex` on a gauge variant without `stakedValues`),
        and `gauge.nft()` gives the NPM the fetch step reads `positions(token_id)`
        from. Returning the NPM here lets `fetch_lp_detail` skip a separate
        `gauge.nft()` read. Returns `(None, None)` when the owner has no staked
        position (so no `nft()` is read). A gauge with several staked positions in the
        same pool enriches only the first (a known, accepted simplification)."""
        try:
            values = self._eth_call(rpc_url, gauge, _call(_SEL_STAKED_VALUES, _addr_arg(owner)))
            ids = _decode_uint_array(values)
            token_id = ids[0] if ids else None
        except LpDetailError:
            token_id = self._resolve_staked_via_index(rpc_url, gauge=gauge, owner=owner)
        if token_id is None:
            return None, None
        npm = _decode_address(self._eth_call(rpc_url, gauge, _SEL_NFT))
        return token_id, npm

    def _resolve_staked_via_index(self, rpc_url: str, *, gauge: str, owner: str) -> int | None:
        """Fallback for a gauge without `stakedValues`: `stakedLength(owner)` then
        `stakedByIndex(owner, 0)`. `None` when the owner has no staked position."""
        try:
            length = _decode_uint(
                self._eth_call(rpc_url, gauge, _call(_SEL_STAKED_LENGTH, _addr_arg(owner))),
                word=0,
            )
        except LpDetailError:
            return None
        if length <= 0:
            return None
        call = _call(_SEL_STAKED_BY_INDEX, _addr_arg(owner), _uint_arg(0))
        return _decode_uint(self._eth_call(rpc_url, gauge, call), word=0)

    def _resolve_bare_cl_token_id(
        self, rpc_url: str, chain: Chain, pool_address: str, owner: str
    ) -> int | None:
        """The unstaked-CL NFT `token_id`: enumerate the owner's positions on the
        canonical `NonfungiblePositionManager` (ERC-721 `balanceOf` +
        `tokenOfOwnerByIndex`) and return the first whose `positions(tokenId)` token
        pair matches the pool's `token0`/`token1`. `None` when the wallet holds no
        matching position."""
        npm = self._npm_for(chain)
        pool = pool_address.lower()
        want0 = _decode_address(self._eth_call(rpc_url, pool, _SEL_TOKEN0))
        want1 = _decode_address(self._eth_call(rpc_url, pool, _SEL_TOKEN1))
        count = _decode_uint(
            self._eth_call(rpc_url, npm, _call(_SEL_BALANCE_OF, _addr_arg(owner))), word=0
        )
        for index in range(count):
            token_id = _decode_uint(
                self._eth_call(
                    rpc_url,
                    npm,
                    _call(_SEL_TOKEN_OF_OWNER_BY_INDEX, _addr_arg(owner), _uint_arg(index)),
                ),
                word=0,
            )
            position = self._eth_call(rpc_url, npm, _call(_SEL_POSITIONS, _uint_arg(token_id)))
            if (
                _decode_address_word(position, _POS_TOKEN0_WORD) == want0
                and _decode_address_word(position, _POS_TOKEN1_WORD) == want1
            ):
                return token_id
        return None

    # -- per-shape reads ----------------------------------------------------

    def _fetch_staked_cl(
        self, rpc_url: str, *, gauge: str, clpool: str, npm: str | None, token_id: int
    ) -> LpPositionDetail:
        """The staked-CL gauge chain (Plan 0048 core fix). `clpool` (from
        `gauge.pool()`) gives the current tick via `slot0()`; `npm` (from
        `gauge.nft()`, carried from the resolve step — or read here on a direct
        fetch) is the `NonfungiblePositionManager`, whose `positions(token_id)` gives
        the tick bounds, owed amounts and the two token addresses. Each read is a
        paced single `eth_call` (this provider per-call-throttles batched sub-calls —
        2026-06-07 smoke). Verified end-to-end in the 2026-06-05 live smoke."""
        if npm is None:  # direct fetch with no prior resolve — read the NPM now
            npm = _decode_address(self._eth_call(rpc_url, gauge, _SEL_NFT))
        position = self._eth_call(rpc_url, npm, _call(_SEL_POSITIONS, _uint_arg(token_id)))
        current_tick = _decode_int(self._eth_call(rpc_url, clpool, _SEL_SLOT0), word=1)
        addr0 = _decode_address_word(position, _POS_TOKEN0_WORD)
        addr1 = _decode_address_word(position, _POS_TOKEN1_WORD)
        return self._assemble_detail(rpc_url, current_tick, position, addr0, addr1)

    def _fetch_univ3(
        self, rpc_url: str, chain: Chain, pool_address: str, token_id: int
    ) -> LpPositionDetail:
        """The unstaked-CL read keyed on the position NFT `token_id`:
        `positions(tokenId)` on the canonical `NonfungiblePositionManager` gives the
        tick bounds, owed amounts and the two token addresses directly; `slot0()` on
        the pool gives the current tick. (Two positions can share a pool with
        different ranges, so the pool alone is ambiguous — hence the tokenId key.)
        Each read is a paced single `eth_call`."""
        npm = self._npm_for(chain)
        position = self._eth_call(rpc_url, npm, _call(_SEL_POSITIONS, _uint_arg(token_id)))
        current_tick = _decode_int(self._eth_call(rpc_url, pool_address, _SEL_SLOT0), word=1)
        addr0 = _decode_address_word(position, _POS_TOKEN0_WORD)
        addr1 = _decode_address_word(position, _POS_TOKEN1_WORD)
        return self._assemble_detail(rpc_url, current_tick, position, addr0, addr1)

    def _assemble_detail(
        self,
        rpc_url: str,
        current_tick: int,
        position: bytes,
        addr0: str,
        addr1: str,
    ) -> LpPositionDetail:
        """Decode a `positions`-shaped return into tick bounds + owed amounts, label
        and scale the owed fees by reading each token's `symbol()`/`decimals()`, and
        fold everything into an `LpPositionDetail`. Shared by both CL reads;
        `addr0`/`addr1` come from the position struct (words 2/3)."""
        tick_lower = _decode_int(position, word=_POS_TICK_LOWER_WORD)
        tick_upper = _decode_int(position, word=_POS_TICK_UPPER_WORD)
        owed0_raw = _decode_uint(position, word=_POS_OWED0_WORD)
        owed1_raw = _decode_uint(position, word=_POS_OWED1_WORD)

        # `symbol()`/`decimals()` are read only for a leg that owes something — a
        # zero-owed leg carries no fee to label, and the staked-CL case reads
        # `tokensOwed = 0` until a poke/collect (the live-smoke observation), so
        # skipping the reads spares RPC round-trips per such position under the rate
        # limit the plan flags.
        uncollected = [
            self._owed_token(rpc_url, addr, raw)
            for addr, raw in ((addr0, owed0_raw), (addr1, owed1_raw))
            if raw > 0
        ]
        return LpPositionDetail(
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
            in_range=tick_lower <= current_tick < tick_upper,
            uncollected_fees=uncollected,
        )

    def _owed_token(self, rpc_url: str, address: str, raw_owed: int) -> PositionToken:
        """Label and scale one owed-fee leg: read the token's `symbol()`/`decimals()`
        and divide the raw `tokensOwed` word by `10**decimals`. `uncollected_fees`
        is the position struct's owed words as-is (Plan 0048 fee definition: swap
        fees only, under-reports until a poke — see `LpPositionDetail`)."""
        symbol = _decode_string(self._eth_call(rpc_url, address, _SEL_SYMBOL))
        decimals = _decode_uint(self._eth_call(rpc_url, address, _SEL_DECIMALS), word=0)
        return PositionToken(symbol=symbol, address=address, amount=raw_owed / (10**decimals))

    # -- transport ----------------------------------------------------------

    def _npm_for(self, chain: Chain) -> str:
        npm = _NPM_ADDRESSES.get(chain)
        if npm is None:
            raise LpDetailConfigError(
                f"lp-detail: no NonfungiblePositionManager known for chain {chain!r}",
            )
        return npm

    def _rpc_url_for(self, chain: Chain) -> str:
        return rpc_url_for(self._secrets, chain)

    def _eth_call(self, rpc_url: str, to: str, data: str) -> bytes:
        """Perform one paced `eth_call` and return the decoded result bytes.

        Raises `LpDetailError` on a JSON-RPC error object / non-hex result and the
        shared rate-limit / unavailable errors on transport failure."""
        self._sleep(self._inter_request_seconds)
        return rpc_eth_call(self._http, rpc_url, to, data)


def rpc_url_for(secrets: SecretsStore, chain: Chain) -> str:
    """The read-only JSON-RPC URL configured for `chain` (ADR-0038), or a typed
    `LpDetailConfigError` when the chain has no reserved secret or none is set.
    Shared by the LP-detail adapter and the Plan 0084 gauge resolver so both read
    the same per-chain URL from the same secret key."""
    key = _RPC_URL_KEYS.get(chain)
    if key is None:
        raise LpDetailConfigError(
            f"lp-detail: chain {chain!r} has no reserved RPC-URL secret — "
            "deep reads cover base / ethereum only",
        )
    url = secrets.get(key)
    if not url:
        raise LpDetailConfigError(
            f"lp-detail: no RPC URL configured — set `{key}` before enriching",
        )
    return url


def rpc_eth_call(http: ResilientHttpClient, rpc_url: str, to: str, data: str) -> bytes:
    """Perform one `eth_call` over JSON-RPC and return the decoded result bytes
    (pacing is the caller's concern). Read-only by construction — the only method
    is `eth_call`, a staticcall. Raises `LpDetailError` on a JSON-RPC error object
    / non-hex result and the shared rate-limit / unavailable errors on transport
    failure. Extracted so the Plan 0084 gauge resolver reuses the exact transport
    the LP-detail adapter already proves read-only."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
    }
    try:
        response = http.post(rpc_url, json=payload, expect_json=True)
    except ResilientHttpError as err:
        raise _classify_error(err) from err
    return _result_bytes(response.json())


def gauge_pool_via_rpc(http: ResilientHttpClient, rpc_url: str, gauge_address: str) -> str | None:
    """The pool address a gauge distributes for, via `gauge.pool()` (Plan 0084) —
    or `None` when `gauge_address` is not a gauge (the getter reverts) or returns
    the zero address. A revert is routing signal, not an error (mirrors the
    LP-detail shape probe's `_gauge_clpool_or_none`). Never raises on a revert."""
    try:
        pool = _decode_address(rpc_eth_call(http, rpc_url, gauge_address, _SEL_POOL))
    except LpDetailError:
        return None
    return pool if int(pool, 16) != 0 else None


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 429 → rate-limited, anything else → unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("lp-detail: rate limited (HTTP 429) on eth_call")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(f"lp-detail: RPC unavailable ({detail}) on eth_call")


def _result_bytes(payload: Any) -> bytes:
    """Extract the `result` hex from a JSON-RPC response and decode it to bytes.

    A JSON-RPC `error` object or a missing/non-hex `result` is a typed
    `LpDetailError` (the read can't proceed)."""
    if not isinstance(payload, dict):
        raise LpDetailError("lp-detail: JSON-RPC response was not an object")
    if "error" in payload:
        raise LpDetailError(f"lp-detail: JSON-RPC error {payload['error']!r}")
    result = payload.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise LpDetailError("lp-detail: JSON-RPC result missing or not 0x-hex")
    try:
        return bytes.fromhex(result[2:])
    except ValueError as err:
        raise LpDetailError("lp-detail: JSON-RPC result is not valid hex") from err


def _word(data: bytes, index: int) -> bytes:
    start = index * _WORD_BYTES
    word = data[start : start + _WORD_BYTES]
    if len(word) != _WORD_BYTES:
        raise LpDetailError(f"lp-detail: result too short for word {index}")
    return word


def _decode_int(data: bytes, *, word: int) -> int:
    """A signed integer (e.g. an int24 tick) sign-extended into a 32-byte word."""
    return int.from_bytes(_word(data, word), "big", signed=True)


def _decode_uint(data: bytes, *, word: int) -> int:
    return int.from_bytes(_word(data, word), "big", signed=False)


def _decode_uint_array(data: bytes) -> list[int]:
    """ABI-decode a dynamic `uint256[]` return: word0 is the data offset, the word
    at that offset is the element count, then the elements. Used for
    `gauge.stakedValues(owner)`."""
    offset = _decode_uint(data, word=0)
    if offset % _WORD_BYTES != 0:
        raise LpDetailError("lp-detail: array offset not word-aligned")
    length_index = offset // _WORD_BYTES
    length = _decode_uint(data, word=length_index)
    return [_decode_uint(data, word=length_index + 1 + i) for i in range(length)]


def _decode_address(data: bytes) -> str:
    """The low 20 bytes of the first word, as a lowercase `0x…` address."""
    return _decode_address_word(data, 0)


def _decode_address_word(data: bytes, index: int) -> str:
    """The low 20 bytes of word `index`, as a lowercase `0x…` address."""
    return "0x" + _word(data, index)[12:].hex()


def _call(selector: str, *args: str) -> str:
    """Build `eth_call` data: the 4-byte selector followed by each 32-byte-encoded
    argument (the args come pre-encoded from `_addr_arg` / `_uint_arg`)."""
    return selector + "".join(args)


def _addr_arg(address: str) -> str:
    """A 20-byte address left-padded to a 32-byte ABI word (hex, no `0x`)."""
    return bytes.fromhex(address[2:]).rjust(_WORD_BYTES, b"\x00").hex()


def _uint_arg(value: int) -> str:
    """A non-negative integer as a 32-byte ABI word (hex, no `0x`)."""
    return value.to_bytes(_WORD_BYTES, "big").hex()


def _decode_string(data: bytes) -> str:
    """ABI-decode a dynamic `string` return: word0 is the data offset, the word at
    that offset is the byte length, then the UTF-8 bytes."""
    offset = _decode_uint(data, word=0)
    if offset % _WORD_BYTES != 0:
        raise LpDetailError("lp-detail: string offset not word-aligned")
    length_index = offset // _WORD_BYTES
    length = _decode_uint(data, word=length_index)
    start = offset + _WORD_BYTES
    raw = data[start : start + length]
    if len(raw) != length:
        raise LpDetailError("lp-detail: string payload shorter than declared length")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise LpDetailError("lp-detail: string is not valid UTF-8") from err
    if not text:
        raise LpDetailError("lp-detail: decoded token symbol is empty")
    return text


__all__ = [
    "LpDetailConfigError",
    "LpDetailError",
    "RpcLpDetailAdapter",
    "gauge_pool_via_rpc",
    "rpc_eth_call",
    "rpc_url_for",
]
