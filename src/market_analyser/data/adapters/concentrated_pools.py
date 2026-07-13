"""Concentrated-liquidity DEX quote adapter — RPC `eth_call` over JSON-RPC
(Plan 0086 / ADR-0080).

The concentrated-liquidity half of the cross-pool discrepancy scanner v2: it prices
Uniswap-v3 and Aerodrome-Slipstream pools as **executable quotes** by asking the
DEX **Quoter** to simulate the real swap across ticks, so a size's slippage is
*measured* (the Quoter's tick-walk) rather than estimated from a single depth
number a concentrated pool does not have. It implements the source-agnostic
`ExecutableQuoteSource` Protocol (`data/sources.py`, ADR-0031), the same seam the
constant-product `OnchainPoolPriceAdapter` implements — a venue is one config entry.

**Read-only by construction (ADR-0080 / ADR-0072 / ADR-0041 proof pattern).** The
only JSON-RPC method this adapter ever issues is `eth_call`. The Quoter is *non-view
but staticcall-designed*: reached by `eth_call` it sends no transaction, signs
nothing, changes no state — read-only exactly like `getReserves()` / `slot0()`. The
adapter carries no key material, no signing path, and no state-changing RPC; its
only credential is the per-chain read-only RPC URL from the `SecretsStore` (ADR-0038
— a read URL, not a trade key). A source scan pins the property
(`tests/defi/test_concentrated_pool_adapter.py`): the module's JSON-RPC method set
is exactly `{"eth_call"}`.

**Fee-tier-aware discovery.** For each configured venue+pair the adapter enumerates
the venue's tiers (Uniswap-v3 fee tiers in PPM — 500/3000/10000 — or the Slipstream
tick spacings) and asks `factory.getPool(base, quote, tier)` for the pool at each;
a zero-address answer means no pool at that tier and is skipped (not an error). An
existing pool whose **quote leg** reverts (a thin tier with no routable liquidity at
the size) is **omitted** — one dust tier no longer aborts the whole scan (ADR-0086) —
mirroring how the constant-product adapter omits a depth-exceeded pool; the pool is
dropped, never coerced to a zero quote.

**Per pool, per size**, the reads are: `slot0()` (the `sqrtPriceX96` marginal
reference), `quoteExactInputSingle` (the sell leg → `sell_proceeds`),
`quoteExactOutputSingle` (the buy leg → `buy_cost`), plus each token's `decimals()`
(shared across a venue's tiers) and — for Slipstream, whose `getPool`/quote key is a
tick spacing rather than a fee — the pool's `fee()` for the reported tier. Uniswap-v3
carries the fee in the tier itself (PPM), so no extra `fee()` read is needed there.

**Configurable User-Agent.** Public RPCs 403 the default `market-analyser/…` UA (the
Plan 0079 live run hit this), so the adapter defaults to a browser-like UA and lets
the caller override it — the resilient-client seam the risks section calls for.

Errors are typed and classified by **which call reverted**, not by the revert string
(ADR-0086): a missing RPC URL / unsupported chain raises `ConcentratedPoolConfigError`;
a 429 / 5xx / transport exhaustion raises the shared `RateLimitedError` /
`UpstreamUnavailableError`; a **structural-read** revert (`getPool` / `slot0` /
`decimals` / `fee`) or a shape-broken result raises `ConcentratedPoolError`; a
**quote-leg** revert (`quoteExactInputSingle` / `quoteExactOutputSingle`) omits the
pool (thin liquidity) rather than raising. A revert is never coerced to a zero quote,
and an out-of-range decoded measurement on a *successful* response surfaces as
`pydantic.ValidationError` from the `ExecutableQuote` boundary (e.g. a zero Quoter
output → `gt=0` rejection, not a fabricated quote — never silently omitted).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain, ExecutableQuote
from market_analyser.persistence.secrets import SecretKey, SecretsStore

_SOURCE = "concentrated-pool-price"

# Quotes are live/volatile and request-triggered — no cache (mirrors onchain_pools).
_CACHE_TTL_SECONDS = 0.0

# A deliberate pause before each RPC request to stay under a provider's per-second
# limit; injectable so tests don't wait. CL pricing is ~4-6 eth_calls per pool per
# size (slot0 + two Quoter legs + decimals + optional fee), so pacing matters more.
_INTER_REQUEST_SECONDS = 0.5

# A browser-like default UA — public RPCs 403 the `market-analyser/…` default the
# resilient client ships (observed in the Plan 0079 live run). Caller-overridable.
_DEFAULT_ADAPTER_USER_AGENT = "Mozilla/5.0 (compatible; market-analyser-defi/1.0)"

# The per-chain RPC-URL secret each target chain reads from (ADR-0038). Only chains
# the secrets schema reserves a URL for are readable. Mirrors onchain_pools.
_RPC_URL_KEYS: dict[Chain, SecretKey] = {
    "base": "base_rpc_url",
    "ethereum": "eth_rpc_url",
}

_QuoterKind = Literal["uniswap-v3", "slipstream"]

# Function selectors (first 4 bytes of keccak256(signature)), pinned by a keccak
# self-check in the test. slot0/decimals/fee are shared with the Uni-v3 ABI that
# `lp_detail.py` already uses; the getPool/quote selectors differ per fork because
# the tier argument is `uint24 fee` (Uni-v3) vs `int24 tickSpacing` (Slipstream).
_SEL_SLOT0 = "0x3850c7bd"  # slot0() -> (sqrtPriceX96, tick, ...)
_SEL_DECIMALS = "0x313ce567"  # decimals() -> uint8
_SEL_FEE = "0xddca3f43"  # fee() -> uint24 (PPM), for Slipstream's reported tier

# getPool(tokenA, tokenB, <tier>) -> pool address, per fork's tier type.
_SEL_GET_POOL: dict[_QuoterKind, str] = {
    "uniswap-v3": "0x1698ee82",  # getPool(address,address,uint24)
    "slipstream": "0x28af8d0b",  # getPool(address,address,int24)
}
# QuoterV2.quoteExactInputSingle((tokenIn,tokenOut,amountIn,<tier>,sqrtPriceLimitX96)).
_SEL_QUOTE_EXACT_IN: dict[_QuoterKind, str] = {
    "uniswap-v3": "0xc6a5026a",  # ((address,address,uint256,uint24,uint160))
    "slipstream": "0x9e7defe6",  # ((address,address,uint256,int24,uint160))
}
# QuoterV2.quoteExactOutputSingle((tokenIn,tokenOut,amount,<tier>,sqrtPriceLimitX96)).
_SEL_QUOTE_EXACT_OUT: dict[_QuoterKind, str] = {
    "uniswap-v3": "0xbd21704a",  # ((address,address,uint256,uint24,uint160))
    "slipstream": "0xfa6af908",  # ((address,address,uint256,int24,uint160))
}

_WORD_BYTES = 32
_Q96 = 2**96
# A Uni-v3 fee / Slipstream `fee()` is in PPM (1e-6): 3000 -> 0.30% -> 30 bps.
_PPM_PER_BPS = 100


class ConcentratedPoolError(ValueError):
    """A structural on-chain read failed in a way the scan cannot proceed past: an
    execution-revert on a *structural* read (`getPool` / `slot0` / `decimals` /
    `fee` — an operator-visible misconfig), or a 2xx result whose shape/length the
    decode required but the payload lacked. Raised at the adapter boundary before
    model construction, never a silently zeroed quote. A revert on a *quote leg*
    omits the pool instead (ADR-0086) — see `_ExecutionReverted`."""


class _ExecutionReverted(ConcentratedPoolError):
    """Internal signal: the node reported a JSON-RPC execution-revert (`error`
    object) on an `eth_call`. Which call reverted decides the handling (ADR-0086):
    a **quote-leg** revert (`quoteExactInputSingle` / `quoteExactOutputSingle`) is
    caught at the call site and omits the pool — no executable price at the size,
    mirroring the CP adapter's depth-exceeded omit; a **structural-read** revert
    (`getPool` / `slot0` / `decimals` / `fee`) is not caught and propagates as the
    public `ConcentratedPoolError` base (an operator-visible misconfig). Classified
    by call site, never by the revert string — robust across Quoter forks."""


class ConcentratedPoolConfigError(UpstreamDataError):
    """No RPC URL is configured for a venue's chain, or the chain has no reserved
    RPC-URL secret. A configuration failure surfaced through the typed upstream
    taxonomy so callers branch on a reason rather than a bare exception."""


class ConcentratedVenueConfig(BaseModel):
    """One concentrated-liquidity DEX venue + canonical pair to price across its fee
    tiers (Plan 0086).

    `factory` / `quoter` are the venue's on-chain factory and Quoter addresses;
    `quoter_kind` selects the fork's `getPool`/quote ABI (Uni-v3 `uint24 fee` vs
    Slipstream `int24 tickSpacing`). `tiers` are the raw protocol tier integers
    enumerated over `factory.getPool` — Uni-v3 **fee tiers in PPM** (500/3000/10000)
    or Slipstream **tick spacings** — one pool per tier that exists. `base_token` /
    `quote_token` orient the price (quote-per-base) and identify the pool's token
    ordering (v3 orders token0 < token1 by address, derived without a read).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dex: str = Field(min_length=1)  # "uniswap-v3" | "aerodrome-slipstream" | …
    chain: Chain
    pair: str = Field(min_length=1)  # canonical "BASE/QUOTE"
    base_token: str = Field(min_length=1)  # base token contract address
    quote_token: str = Field(min_length=1)  # quote token contract address
    factory: str = Field(min_length=1)  # v3Factory / CLFactory address
    quoter: str = Field(min_length=1)  # QuoterV2 / Slipstream quoter address
    quoter_kind: _QuoterKind
    tiers: tuple[int, ...] = Field(min_length=1)  # PPM fees (Uni-v3) or tick spacings


# No venues are configured by default — a live evidence run (Plan 0086 phase 4/5)
# supplies a verified set of real factory/quoter/pool addresses via the `venues=`
# constructor arg (fabricated addresses would revert on-chain). Mirrors the Plan
# 0079 `DEFAULT_POOLS = ()` precedent.
DEFAULT_CONCENTRATED_VENUES: tuple[ConcentratedVenueConfig, ...] = ()


class ConcentratedPoolPriceAdapter:
    """Prices configured concentrated-liquidity pools as executable quotes via the
    DEX Quoter over JSON-RPC (`eth_call` only)."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        venues: Sequence[ConcentratedVenueConfig] = DEFAULT_CONCENTRATED_VENUES,
        http_client: ResilientHttpClient | None = None,
        inter_request_seconds: float = _INTER_REQUEST_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        user_agent: str = _DEFAULT_ADAPTER_USER_AGENT,
    ) -> None:
        self._secrets = secrets_store
        self._venues = tuple(venues)
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name=_SOURCE,
                cache_ttl_seconds=_CACHE_TTL_SECONDS,
                user_agent=user_agent,
            )
        )
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep
        self._now = now if now is not None else lambda: datetime.now(tz=UTC)

    def fetch_executable_quotes(self, pair: str, *, trade_size: float) -> Sequence[ExecutableQuote]:
        """Return an `ExecutableQuote` for every configured concentrated-liquidity
        pool matching `pair` at `trade_size` — one per venue tier that has a pool.

        For each pool the Quoter simulates both legs: `quoteExactInputSingle`
        (sell → `sell_proceeds`) and `quoteExactOutputSingle` (buy → `buy_cost`),
        both already net of the pool's fee and its measured tick-crossing slippage;
        `slot0()` supplies the marginal reference. A tier with no pool is skipped; a
        tier whose **quote leg** reverts (thin pool, no executable price at the size)
        is omitted, so one dust tier does not abort the scan (ADR-0086); a revert is
        never coerced to a zeroed quote.

        Raises `ValueError` on a non-positive `trade_size`, `ConcentratedPoolConfigError`
        on a missing RPC URL / unsupported chain, the shared `RateLimitedError` /
        `UpstreamUnavailableError` on throttle / outage, and `ConcentratedPoolError`
        (or `pydantic.ValidationError`) on a **structural-read** revert / shape-broken
        read. An unconfigured pair returns `[]`.
        """
        if trade_size <= 0:
            raise ValueError("trade_size must be positive")
        wanted = pair.strip().upper()
        as_of = self._now()
        quotes: list[ExecutableQuote] = []
        for venue in self._venues:
            if venue.pair.strip().upper() != wanted:
                continue
            quotes.extend(self._quote_venue(venue, trade_size=trade_size, as_of=as_of))
        return quotes

    def _quote_venue(
        self, venue: ConcentratedVenueConfig, *, trade_size: float, as_of: datetime
    ) -> list[ExecutableQuote]:
        rpc_url = self._rpc_url_for(venue.chain)
        base = venue.base_token.lower()
        quote = venue.quote_token.lower()
        base_decimals = _decode_uint(self._eth_call(rpc_url, base, _SEL_DECIMALS), word=0)
        quote_decimals = _decode_uint(self._eth_call(rpc_url, quote, _SEL_DECIMALS), word=0)
        is_base_token0 = int(base, 16) < int(quote, 16)

        quotes: list[ExecutableQuote] = []
        for tier in venue.tiers:
            pool = self._get_pool(rpc_url, venue, tier)
            if pool is None:
                continue  # no pool at this tier — skip, not an error
            priced = self._quote_pool(
                rpc_url,
                venue=venue,
                pool=pool,
                tier=tier,
                base=base,
                quote=quote,
                base_decimals=base_decimals,
                quote_decimals=quote_decimals,
                is_base_token0=is_base_token0,
                trade_size=trade_size,
                as_of=as_of,
            )
            if priced is not None:
                quotes.append(priced)  # None = a quote-leg revert omitted this pool
        return quotes

    def _get_pool(self, rpc_url: str, venue: ConcentratedVenueConfig, tier: int) -> str | None:
        """`factory.getPool(base, quote, tier)` — the pool address, or `None` when
        the factory returns the zero address (no pool at this tier)."""
        data = (
            _SEL_GET_POOL[venue.quoter_kind]
            + _addr_arg(venue.base_token)
            + _addr_arg(venue.quote_token)
            + _uint_arg(tier)
        )
        pool = _decode_address(self._eth_call(rpc_url, venue.factory, data))
        return None if int(pool, 16) == 0 else pool

    def _quote_pool(
        self,
        rpc_url: str,
        *,
        venue: ConcentratedVenueConfig,
        pool: str,
        tier: int,
        base: str,
        quote: str,
        base_decimals: int,
        quote_decimals: int,
        is_base_token0: bool,
        trade_size: float,
        as_of: datetime,
    ) -> ExecutableQuote | None:
        """Price one pool as an `ExecutableQuote`, or return `None` to omit it when a
        quote leg reverts (a thin pool with no executable price at the size, ADR-0086).
        The structural reads (`slot0`, and Slipstream's `fee()`) still raise."""
        sqrt_price = _decode_uint(self._eth_call(rpc_url, pool, _SEL_SLOT0), word=0)
        if sqrt_price <= 0:
            raise ConcentratedPoolError(
                f"concentrated-pool: pool {pool} reports a non-positive sqrtPriceX96",
            )
        marginal_price = _marginal_from_sqrt(
            sqrt_price,
            base_decimals=base_decimals,
            quote_decimals=quote_decimals,
            is_base_token0=is_base_token0,
        )

        size_raw = round(trade_size * (10**base_decimals))
        if size_raw <= 0:
            raise ConcentratedPoolError(
                "concentrated-pool: trade_size rounds to zero base units at this decimals",
            )

        # Both legs price the round trip; either reverting means the pool cannot
        # source the size, so it is omitted (ADR-0086), never zeroed. A decode failure
        # or an out-of-range value on a *successful* response still raises downstream.
        sell_data = _quote_single_data(  # exact-input, base -> quote: amountOut received
            _SEL_QUOTE_EXACT_IN[venue.quoter_kind], base, quote, size_raw, tier
        )
        buy_data = _quote_single_data(  # exact-output, quote -> base: amountIn paid
            _SEL_QUOTE_EXACT_OUT[venue.quoter_kind], quote, base, size_raw, tier
        )
        try:
            out_raw = _decode_uint(self._eth_call(rpc_url, venue.quoter, sell_data), word=0)
            in_raw = _decode_uint(self._eth_call(rpc_url, venue.quoter, buy_data), word=0)
        except _ExecutionReverted:
            return None
        sell_proceeds = out_raw / (10**quote_decimals)
        buy_cost = in_raw / (10**quote_decimals)

        return ExecutableQuote(
            pool_id=pool,
            dex=venue.dex,
            chain=venue.chain,
            pair=venue.pair,
            fee_tier=self._fee_tier_bps(rpc_url, venue, pool, tier),
            trade_size=trade_size,
            buy_cost=buy_cost,
            sell_proceeds=sell_proceeds,
            marginal_price=marginal_price,
            as_of=as_of,
        )

    def _fee_tier_bps(
        self, rpc_url: str, venue: ConcentratedVenueConfig, pool: str, tier: int
    ) -> int:
        """The pool's fee in basis points for the reported tier. Uni-v3 carries the
        fee in the tier itself (PPM), so no read is needed; Slipstream's tier is a
        tick spacing, so the fee is read from the pool's `fee()` (PPM)."""
        if venue.quoter_kind == "uniswap-v3":
            return tier // _PPM_PER_BPS
        fee_ppm = _decode_uint(self._eth_call(rpc_url, pool, _SEL_FEE), word=0)
        return fee_ppm // _PPM_PER_BPS

    # -- transport ----------------------------------------------------------

    def _rpc_url_for(self, chain: Chain) -> str:
        key = _RPC_URL_KEYS.get(chain)
        if key is None:
            raise ConcentratedPoolConfigError(
                f"concentrated-pool: chain {chain!r} has no reserved RPC-URL secret — "
                "reads cover base / ethereum only",
            )
        url = self._secrets.get(key)
        if not url:
            raise ConcentratedPoolConfigError(
                f"concentrated-pool: no RPC URL configured — set `{key}` before scanning",
            )
        return url

    def _eth_call(self, rpc_url: str, to: str, data: str) -> bytes:
        """Perform one paced `eth_call` and return the decoded result bytes.

        Raises `_ExecutionReverted` (a `ConcentratedPoolError` subtype the quote-leg
        call sites catch to omit a thin pool) on a JSON-RPC error object, the base
        `ConcentratedPoolError` on a non-hex / short result, and the shared rate-limit
        / unavailable errors on transport failure."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }
        self._sleep(self._inter_request_seconds)
        try:
            response = self._http.post(rpc_url, json=payload, expect_json=True)
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        return _result_bytes(response.json())


def _marginal_from_sqrt(
    sqrt_price: int,
    *,
    base_decimals: int,
    quote_decimals: int,
    is_base_token0: bool,
) -> float:
    """Decimals-adjusted marginal price (quote-per-base) from a v3 `sqrtPriceX96`.

    `raw = (sqrtPriceX96 / 2^96)^2` is token1-per-token0 in smallest units; orient
    it to quote-per-base by the pool's token ordering, then scale by the decimals
    difference. Derived reference for the auditability breakdown, not the executable
    number — float precision is adequate."""
    raw = (sqrt_price / _Q96) ** 2
    oriented = raw if is_base_token0 else 1.0 / raw
    return oriented * (10.0 ** (base_decimals - quote_decimals))


def _quote_single_data(selector: str, token_in: str, token_out: str, amount: int, tier: int) -> str:
    """ABI-encode a QuoterV2 `quote*Single` call over its single static struct
    `(tokenIn, tokenOut, amount, tier, sqrtPriceLimitX96=0)` — five inline words
    (an all-static tuple needs no offset)."""
    return (
        selector
        + _addr_arg(token_in)
        + _addr_arg(token_out)
        + _uint_arg(amount)
        + _uint_arg(tier)
        + _uint_arg(0)
    )


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 429 → rate-limited, anything else → unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("concentrated-pool: rate limited (HTTP 429) on eth_call")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(f"concentrated-pool: RPC unavailable ({detail}) on eth_call")


def _result_bytes(payload: object) -> bytes:
    """Extract the `result` hex from a JSON-RPC response and decode it to bytes.

    A JSON-RPC `error` object (an execution-revert) raises `_ExecutionReverted` — the
    internal `ConcentratedPoolError` subtype the quote-leg call sites catch to omit a
    thin pool (ADR-0086), and every other call site lets propagate as a misconfig. A
    missing/short/non-hex `result` (a decode failure on a 2xx response) raises the
    base `ConcentratedPoolError`."""
    if not isinstance(payload, dict):
        raise ConcentratedPoolError("concentrated-pool: JSON-RPC response was not an object")
    if "error" in payload:
        raise _ExecutionReverted(f"concentrated-pool: JSON-RPC error {payload['error']!r}")
    result = payload.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ConcentratedPoolError("concentrated-pool: JSON-RPC result missing or not 0x-hex")
    try:
        return bytes.fromhex(result[2:])
    except ValueError as err:
        raise ConcentratedPoolError("concentrated-pool: JSON-RPC result is not valid hex") from err


def _word(data: bytes, index: int) -> bytes:
    start = index * _WORD_BYTES
    word = data[start : start + _WORD_BYTES]
    if len(word) != _WORD_BYTES:
        raise ConcentratedPoolError(f"concentrated-pool: result too short for word {index}")
    return word


def _decode_uint(data: bytes, *, word: int) -> int:
    return int.from_bytes(_word(data, word), "big", signed=False)


def _decode_address(data: bytes) -> str:
    """The low 20 bytes of the first word, as a lowercase `0x…` address."""
    return "0x" + _word(data, 0)[12:].hex()


def _addr_arg(address: str) -> str:
    """A 20-byte address left-padded to a 32-byte ABI word (hex, no `0x`)."""
    return bytes.fromhex(address[2:]).rjust(_WORD_BYTES, b"\x00").hex()


def _uint_arg(value: int) -> str:
    """A non-negative integer as a 32-byte ABI word (hex, no `0x`). Positive int24
    tick spacings encode identically to their uint form."""
    return value.to_bytes(_WORD_BYTES, "big").hex()


__all__ = [
    "DEFAULT_CONCENTRATED_VENUES",
    "ConcentratedPoolConfigError",
    "ConcentratedPoolError",
    "ConcentratedPoolPriceAdapter",
    "ConcentratedVenueConfig",
]
