"""On-chain DEX pool-price adapter — RPC `eth_call` over JSON-RPC (Plan 0079).

The read half of the cross-pool discrepancy scanner (ADR-0072 BA-7 evidence
layer): it reads the current *marginal* price of a configured set of DEX pools for
a canonical pair, so the screener (`defi/discrepancy.py`) can compare the same
pair's price across venues net-of-cost. It implements the source-agnostic
`PoolPriceSource` Protocol (`data/sources.py`, ADR-0031) and is reached only
through that seam.

**Read-only by construction (ADR-0072 / ADR-0041 proof pattern).** The only
JSON-RPC method this adapter ever issues is `eth_call` (a view read); it carries no
key material, no transaction-signing path, and no state-changing (send-transaction)
RPC, and it moves no funds. Its only credential is the per-chain read-only RPC
endpoint URL read lazily from the `SecretsStore` (ADR-0038 server-side injection) —
a read URL, categorically not a trade key. A source scan pins the property
(`tests/defi/test_pool_price_adapter.py`).

**v1 scope: constant-product (Uniswap-v2 / Aerodrome-style) pools.** Each such
pool answers `getReserves()` → `(reserve0, reserve1, …)`, `token0()` → the address
whose reserve is `reserve0`, and each token answers ERC-20 `decimals()`. From
those the adapter computes the decimals-adjusted reserves and the marginal price
(quote-per-base) — an exact, cheaply-read number. **Concentrated-liquidity
(Uniswap-v3) pools are a documented followup** (their executable price needs a
Quoter read or tick-walk); a v3 source would add one registry entry behind the
same Protocol.

The reported `price` is the pool's marginal (spot) price at zero size — the honest
input for a *gross* cross-pool spread. The size-dependent execution cost is **not**
folded into it: the adapter also returns the pool depth (`liquidity_base` /
`liquidity_quote` — the decimals-adjusted reserves), and the screener estimates
slippage for the trade size from that depth. Keeping the two separate is what lets
the net-of-cost breakdown stay explicit and un-double-counted (Plan 0079 honesty
pin).

**Executable-quote method (Plan 0086 / ADR-0080).** `fetch_executable_quotes`
implements the `ExecutableQuoteSource` contract on top of the same reserves: it
folds the pool's fee + slippage into a real round-trip leg and returns an
`ExecutableQuote` (`buy_cost` exact-output, `sell_proceeds` exact-input, both net)
plus the marginal reference — so the scanner v2 ranks pre-costed quotes rather than
estimating cost itself. The constant-product math lives in `_cp_executable_legs`; a
pool that cannot source the size is omitted, never fabricated. `fetch_pool_quotes`
(the v1 marginal path) stays until the scanner fully cuts over.

Errors are typed (done-when): a missing RPC URL or an unsupported chain raises
`PoolPriceConfigError`; a 429 / 5xx / transport exhaustion raises the shared
`RateLimitedError` / `UpstreamUnavailableError`; a JSON-RPC error object (a revert)
or a shape-broken result raises `PoolPriceError` — never a silently zeroed price.
An out-of-range decoded measurement surfaces as `pydantic.ValidationError` from the
`PoolQuote` boundary.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain, ExecutableQuote, PoolQuote
from market_analyser.persistence.secrets import SecretKey, SecretsStore

_SOURCE = "onchain-pool-price"

# Pool prices are live/volatile and a read is request-triggered, so the adapter
# does not cache — each scan is a fresh read (mirrors `lp_detail.py`).
_CACHE_TTL_SECONDS = 0.0

# A deliberate pause before each RPC request to stay under a provider's per-second
# limit (the same pacing `lp_detail.py` needs on Base). Injectable so tests don't
# wait.
_INTER_REQUEST_SECONDS = 0.5

# The per-chain RPC-URL secret each target chain reads from (ADR-0038). Only chains
# the secrets schema reserves a URL for are readable; a pool on another chain fails
# typed rather than silently empty. Mirrors `lp_detail._RPC_URL_KEYS`.
_RPC_URL_KEYS: dict[Chain, SecretKey] = {
    "base": "base_rpc_url",
    "ethereum": "eth_rpc_url",
}

# Function selectors (first 4 bytes of keccak256(signature)). All shared with the
# Uniswap-v2 / Aerodrome constant-product ABI; `decimals`/`token0` also match the
# Uni-v3 fork selectors `lp_detail.py` pins.
_SEL_GET_RESERVES = "0x0902f1ac"  # getReserves() -> (reserve0, reserve1, blockTimestampLast)
_SEL_TOKEN0 = "0x0dfe1681"  # token0() -> address
_SEL_DECIMALS = "0x313ce567"  # decimals() -> uint8

_WORD_BYTES = 32


class PoolPriceError(ValueError):
    """A JSON-RPC error object (e.g. a revert), or a 2xx result whose shape/length
    the decode required but the payload lacked — raised at the adapter boundary
    before model construction. Never a silently zeroed price."""


class PoolPriceConfigError(UpstreamDataError):
    """No RPC URL is configured for a pool's chain, or the chain has no reserved
    RPC-URL secret. A configuration failure surfaced through the typed upstream
    taxonomy so callers branch on a reason rather than a bare exception."""


class PoolConfig(BaseModel):
    """One configured constant-product DEX pool to price (Plan 0079).

    `pair` is the canonical label the scanner queries by ("WETH/USDC"); several
    pools across venues share it. `base_token` / `quote_token` are the on-chain
    token addresses that orient the price (quote-per-base) and identify which
    reserve is which — the adapter reads `token0()` and matches it to `base_token`
    rather than assuming the pool's token ordering. `fee_bps` is the pool's swap fee
    in basis points (the screener subtracts it as a cost); it is configured, not
    read, because the v2-family fee lives in the factory, not a uniform pool getter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pool_id: str = Field(min_length=1)  # pool contract address (0x…)
    dex: str = Field(min_length=1)  # "aerodrome" | "uniswap-v2" | …
    chain: Chain
    pair: str = Field(min_length=1)  # canonical "BASE/QUOTE"
    base_token: str = Field(min_length=1)  # base token contract address
    quote_token: str = Field(min_length=1)  # quote token contract address
    fee_bps: float = Field(ge=0)  # pool swap fee in basis points (30 = 0.30%)


# No pools are configured by default — a live evidence run (Plan 0079 phase 4)
# supplies a verified set of real pool addresses via the `pools=` constructor arg
# (fabricated addresses would revert on-chain and muddy the smoke). With an empty
# set the tool reports zero configured pools rather than failing.
DEFAULT_POOLS: tuple[PoolConfig, ...] = ()


class OnchainPoolPriceAdapter:
    """Reads configured constant-product DEX pools' marginal prices over JSON-RPC."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore,
        pools: Sequence[PoolConfig] = DEFAULT_POOLS,
        http_client: ResilientHttpClient | None = None,
        inter_request_seconds: float = _INTER_REQUEST_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._secrets = secrets_store
        self._pools = tuple(pools)
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep
        self._now = now if now is not None else lambda: datetime.now(tz=UTC)

    def fetch_pool_quotes(self, pair: str, *, trade_size: float) -> Sequence[PoolQuote]:
        """Return a `PoolQuote` for every configured pool matching `pair`.

        `trade_size` is the base-token size the downstream slippage estimate is
        computed for; it is carried onto each quote (the price itself is the
        marginal price, not size-adjusted). An unconfigured pair returns `[]`.

        Raises `ValueError` on a non-positive `trade_size` (a caller bug, not an
        upstream failure), `PoolPriceConfigError` on a missing RPC URL / unsupported
        chain, the shared `RateLimitedError` / `UpstreamUnavailableError` on
        throttle / outage, and `PoolPriceError` (or `pydantic.ValidationError`) on a
        shape-broken read.
        """
        if trade_size <= 0:
            raise ValueError("trade_size must be positive")
        wanted = pair.strip().upper()
        as_of = self._now()
        return [
            self._quote_pool(pool, trade_size=trade_size, as_of=as_of)
            for pool in self._pools
            if pool.pair.strip().upper() == wanted
        ]

    def fetch_executable_quotes(self, pair: str, *, trade_size: float) -> Sequence[ExecutableQuote]:
        """Return an `ExecutableQuote` for every configured pool matching `pair`
        that can executably fill `trade_size` (Plan 0086 / ADR-0080).

        Reads the same constant-product reserves as `fetch_pool_quotes`, then folds
        the fee + slippage of a real round-trip leg into the quote: `buy_cost` (the
        `x·y=k` exact-output cost to acquire `trade_size` base) and `sell_proceeds`
        (the exact-input proceeds from selling it), both net. A pool whose base
        depth cannot source an exact-output buy of `trade_size` (`trade_size >=`
        base reserve) is **omitted** — the honest executable-model answer is "no
        quote at this size", never a fabricated number.

        Same error taxonomy as `fetch_pool_quotes`: `ValueError` on a non-positive
        `trade_size`, `PoolPriceConfigError` on config, the shared rate-limit /
        unavailable errors on transport, `PoolPriceError` / `ValidationError` on a
        shape-broken read. An unconfigured pair returns `[]`.
        """
        if trade_size <= 0:
            raise ValueError("trade_size must be positive")
        wanted = pair.strip().upper()
        as_of = self._now()
        quotes: list[ExecutableQuote] = []
        for pool in self._pools:
            if pool.pair.strip().upper() != wanted:
                continue
            quote = self._executable_quote_pool(pool, trade_size=trade_size, as_of=as_of)
            if quote is not None:
                quotes.append(quote)
        return quotes

    def _read_pool_depth(self, pool: PoolConfig) -> tuple[float, float]:
        """Read a constant-product pool's decimals-adjusted `(liquidity_base,
        liquidity_quote)` over `eth_call`, oriented quote-per-base regardless of the
        pool's on-chain token ordering. Raises `PoolPriceError` on a shape-broken
        read, a `token0` matching neither configured token, or a non-positive
        reserve — never a silently zeroed depth."""
        rpc_url = self._rpc_url_for(pool.chain)
        reserves = self._eth_call(rpc_url, pool.pool_id, _SEL_GET_RESERVES)
        reserve0 = _decode_uint(reserves, word=0)
        reserve1 = _decode_uint(reserves, word=1)
        token0 = _decode_address(self._eth_call(rpc_url, pool.pool_id, _SEL_TOKEN0))

        base = pool.base_token.lower()
        quote = pool.quote_token.lower()
        # Orient the reserves: `token0()` tells us whether reserve0 is the base or
        # the quote side (pool token ordering is by address, not by our pair label).
        if token0 == base:
            reserve_base_raw, reserve_quote_raw = reserve0, reserve1
        elif token0 == quote:
            reserve_base_raw, reserve_quote_raw = reserve1, reserve0
        else:
            raise PoolPriceError(
                f"onchain-pool: pool {pool.pool_id} token0 {token0} matches neither "
                f"the configured base {base} nor quote {quote}",
            )

        base_decimals = _decode_uint(self._eth_call(rpc_url, base, _SEL_DECIMALS), word=0)
        quote_decimals = _decode_uint(self._eth_call(rpc_url, quote, _SEL_DECIMALS), word=0)
        liquidity_base = reserve_base_raw / (10**base_decimals)
        liquidity_quote = reserve_quote_raw / (10**quote_decimals)
        if liquidity_base <= 0 or liquidity_quote <= 0:
            raise PoolPriceError(
                f"onchain-pool: pool {pool.pool_id} has a non-positive reserve "
                f"(base={liquidity_base}, quote={liquidity_quote})",
            )
        return liquidity_base, liquidity_quote

    def _quote_pool(self, pool: PoolConfig, *, trade_size: float, as_of: datetime) -> PoolQuote:
        liquidity_base, liquidity_quote = self._read_pool_depth(pool)
        price = liquidity_quote / liquidity_base
        return PoolQuote(
            pool_id=pool.pool_id,
            dex=pool.dex,
            chain=pool.chain,
            pair=pool.pair,
            base_token=pool.base_token,
            quote_token=pool.quote_token,
            trade_size=trade_size,
            price=price,
            fee_bps=pool.fee_bps,
            liquidity_base=liquidity_base,
            liquidity_quote=liquidity_quote,
            as_of=as_of,
        )

    def _executable_quote_pool(
        self, pool: PoolConfig, *, trade_size: float, as_of: datetime
    ) -> ExecutableQuote | None:
        liquidity_base, liquidity_quote = self._read_pool_depth(pool)
        legs = _cp_executable_legs(
            liquidity_base=liquidity_base,
            liquidity_quote=liquidity_quote,
            fee_bps=pool.fee_bps,
            trade_size=trade_size,
        )
        if legs is None:
            return None  # depth cannot source an exact-output buy of this size
        buy_cost, sell_proceeds, marginal_price = legs
        return ExecutableQuote(
            pool_id=pool.pool_id,
            dex=pool.dex,
            chain=pool.chain,
            pair=pool.pair,
            fee_tier=int(pool.fee_bps),
            trade_size=trade_size,
            buy_cost=buy_cost,
            sell_proceeds=sell_proceeds,
            marginal_price=marginal_price,
            as_of=as_of,
        )

    # -- transport ----------------------------------------------------------

    def _rpc_url_for(self, chain: Chain) -> str:
        key = _RPC_URL_KEYS.get(chain)
        if key is None:
            raise PoolPriceConfigError(
                f"onchain-pool: chain {chain!r} has no reserved RPC-URL secret — "
                "reads cover base / ethereum only",
            )
        url = self._secrets.get(key)
        if not url:
            raise PoolPriceConfigError(
                f"onchain-pool: no RPC URL configured — set `{key}` before scanning",
            )
        return url

    def _eth_call(self, rpc_url: str, to: str, data: str) -> bytes:
        """Perform one `eth_call` and return the decoded result bytes.

        Raises `PoolPriceError` on a JSON-RPC error object / non-hex result and the
        shared rate-limit / unavailable errors on transport failure."""
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


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: 429 → rate-limited, anything else → unavailable."""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("onchain-pool: rate limited (HTTP 429) on eth_call")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(f"onchain-pool: RPC unavailable ({detail}) on eth_call")


def _result_bytes(payload: object) -> bytes:
    """Extract the `result` hex from a JSON-RPC response and decode it to bytes.

    A JSON-RPC `error` object or a missing/non-hex `result` is a typed
    `PoolPriceError` (the read can't proceed)."""
    if not isinstance(payload, dict):
        raise PoolPriceError("onchain-pool: JSON-RPC response was not an object")
    if "error" in payload:
        raise PoolPriceError(f"onchain-pool: JSON-RPC error {payload['error']!r}")
    result = payload.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        raise PoolPriceError("onchain-pool: JSON-RPC result missing or not 0x-hex")
    try:
        return bytes.fromhex(result[2:])
    except ValueError as err:
        raise PoolPriceError("onchain-pool: JSON-RPC result is not valid hex") from err


def _word(data: bytes, index: int) -> bytes:
    start = index * _WORD_BYTES
    word = data[start : start + _WORD_BYTES]
    if len(word) != _WORD_BYTES:
        raise PoolPriceError(f"onchain-pool: result too short for word {index}")
    return word


def _decode_uint(data: bytes, *, word: int) -> int:
    return int.from_bytes(_word(data, word), "big", signed=False)


def _decode_address(data: bytes) -> str:
    """The low 20 bytes of the first word, as a lowercase `0x…` address."""
    return "0x" + _word(data, 0)[12:].hex()


def _cp_executable_legs(
    *,
    liquidity_base: float,
    liquidity_quote: float,
    fee_bps: float,
    trade_size: float,
) -> tuple[float, float, float] | None:
    """Executable `(buy_cost, sell_proceeds, marginal_price)` for a constant-product
    pool with decimals-adjusted reserves, or `None` when the pool cannot source an
    exact-output buy of `trade_size` base (`trade_size >= liquidity_base`, or a
    degenerate `fee_bps >= 10_000`).

    Uniswap-v2 fee-on-input model (ADR-0080), all in quote-token units:

    - marginal reference: ``P = liquidity_quote / liquidity_base`` (zero size);
    - buy leg (exact-output, acquire ``Δ`` base): fee on the quote input →
      ``buy_cost = R_q·Δ / ((R_b - Δ)·(1 - f))``;
    - sell leg (exact-input, sell ``Δ`` base): fee on the base input, so the base
      that reaches the invariant is ``(1 - f)·Δ`` →
      ``sell_proceeds = (1 - f)·Δ·R_q / (R_b + (1 - f)·Δ)``.

    Both are strictly positive and finite for ``0 < Δ < R_b`` and ``0 ≤ f < 1``."""
    marginal_price = liquidity_quote / liquidity_base
    fee_frac = fee_bps / 1e4
    if trade_size >= liquidity_base or fee_frac >= 1.0:
        return None
    buy_cost = liquidity_quote * trade_size / ((liquidity_base - trade_size) * (1.0 - fee_frac))
    effective_in = (1.0 - fee_frac) * trade_size
    sell_proceeds = effective_in * liquidity_quote / (liquidity_base + effective_in)
    return buy_cost, sell_proceeds, marginal_price


__all__ = [
    "DEFAULT_POOLS",
    "OnchainPoolPriceAdapter",
    "PoolConfig",
    "PoolPriceConfigError",
    "PoolPriceError",
]
