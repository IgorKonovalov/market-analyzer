"""On-chain LP deep-state adapter — RPC `eth_call` over JSON-RPC (Plan 0034).

The *depth* half of the DeFi program (ADR-0034): discovery (Zerion, `zerion.py`)
tells us *which* LP positions a wallet holds and their pool address; this adapter
reads the precise on-chain state of one such position — its tick range, where the
pool's current tick sits relative to it (in-range status), and the fees accrued
but not yet collected. It implements the source-agnostic `LpPositionDetailSource`
Protocol (`data/sources.py`, ADR-0031) and is reached only through that seam.

Two concentrated-liquidity classes, keyed differently (Zerion-API survey §8):

- **Aerodrome / Velodrome Slipstream (one hop, phase 3).** Keyed on the
  `pool_address` discovery carries on every complex position. `token_id` is
  `None`.
- **Uniswap-v3 (two hops, phase 4).** Each position is an NFT and two positions
  can share a pool with different ranges, so the read keys on the position NFT
  `token_id` against the `NonfungiblePositionManager`.

Both read the same primitives via real Uniswap-v3-style ABIs (Slipstream is a
Uni-v3 fork): `slot0()` for the current tick, a position read for the tick bounds
+ owed token amounts, and `token0()`/`token1()` + ERC-20 `symbol()`/`decimals()`
to label and scale the owed fees. Calls go through the shared
`ResilientHttpClient` (ADR-0019); the per-chain RPC URL is read **lazily** from
the `SecretsStore` (ADR-0038 server-side injection), so the adapter constructs
before a URL is set and a read without one fails typed (`LpDetailConfigError`),
not at construction.

Errors are typed (done-when): a missing RPC URL or an unsupported chain raises
`LpDetailConfigError`; a 429 / 5xx / transport exhaustion raises the shared
`RateLimitedError` / `UpstreamUnavailableError`; a JSON-RPC error object or a
shape-broken result raises `LpDetailError`. An out-of-range decoded measurement
surfaces as `pydantic.ValidationError` from the `LpPositionDetail` boundary.

**Live-ABI note (Plan 0034 risk, deferred).** The exact Aerodrome Slipstream
staked-position view and the Uni-v3 `tokenId` resolution are confirmed against
mainnet in the phase-5 live smoke — the plan flags the Slipstream ABI as the
"first real implementation unknown" and defers live Uni-v3 (no in-scope wallet
holds one, the F3 gap). What is verified offline here is the JSON-RPC call
construction and the ABI decode path, exercised against recorded fixtures.
"""

from __future__ import annotations

from typing import Any

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

# The per-chain RPC-URL secret each target chain reads from (ADR-0038). Only the
# two chains the secrets schema reserves a URL for are deep-readable; a position
# on another target chain fails typed rather than silently empty.
_RPC_URL_KEYS: dict[Chain, SecretKey] = {
    "base": "base_rpc_url",
    "ethereum": "eth_rpc_url",
}

# Function selectors (first 4 bytes of keccak256(signature)). Slipstream is a
# Uniswap-v3 fork, so the pool/ERC-20 selectors are shared with Uni-v3.
_SEL_SLOT0 = "0x3850c7bd"  # slot0() -> (sqrtPriceX96, tick, ...)
_SEL_TOKEN0 = "0x0dfe1681"  # token0() -> address
_SEL_TOKEN1 = "0xd21220a7"  # token1() -> address
_SEL_SYMBOL = "0x95d89b41"  # symbol() -> string
_SEL_DECIMALS = "0x313ce567"  # decimals() -> uint8
# positions(uint256 tokenId) on the NonfungiblePositionManager -> (nonce,
# operator, token0, token1, fee/tickSpacing, tickLower, tickUpper, liquidity,
# feeGrowthInside0, feeGrowthInside1, tokensOwed0, tokensOwed1).
_SEL_POSITIONS = "0x99fbab88"

# Word index of each field in the `positions(tokenId)` return (12 words). The
# tick bounds and owed amounts are what the detail needs; the rest is skipped.
_POS_TICK_LOWER_WORD = 5
_POS_TICK_UPPER_WORD = 6
_POS_OWED0_WORD = 10
_POS_OWED1_WORD = 11

_WORD_BYTES = 32


class LpDetailError(ValueError):
    """A JSON-RPC error object, or a 2xx result whose shape/length the decode
    required but the payload lacked — raised at the adapter boundary before model
    construction (caught by the scan path as a malformed-response reason)."""


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
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )

    def fetch_lp_detail(
        self,
        *,
        chain: Chain,
        pool_address: str,
        token_id: int | None = None,
    ) -> LpPositionDetail:
        """Return the deep on-chain state of the LP position at `pool_address`.

        `token_id` selects the keying class: `None` reads the Aerodrome /
        Velodrome Slipstream one-hop path (phase 3); an integer reads the
        Uniswap-v3 two-hop path against the position NFT (phase 4).

        Raises `LpDetailConfigError` on a missing RPC URL / unsupported chain, the
        shared `RateLimitedError` / `UpstreamUnavailableError` on throttle / outage,
        and `LpDetailError` (or `pydantic.ValidationError`) on a shape-broken read.
        """
        rpc_url = self._rpc_url_for(chain)
        if token_id is None:
            return self._fetch_aerodrome(rpc_url, pool_address)
        return self._fetch_univ3(rpc_url, pool_address, token_id)

    # -- per-class reads ----------------------------------------------------

    def _fetch_aerodrome(self, rpc_url: str, pool_address: str) -> LpPositionDetail:
        """One-hop read keyed on `pool_address`: the pool's current tick (`slot0`),
        the staked position's tick bounds + owed amounts, and the two underlying
        tokens (for labelling/scaling the owed fees).

        The position read uses the same `positions`-shaped return the Uni-v3 path
        decodes; the live Slipstream staked-position view is confirmed in the
        phase-5 smoke (the plan's flagged ABI unknown)."""
        current_tick = _decode_int(self._eth_call(rpc_url, pool_address, _SEL_SLOT0), word=1)
        position = self._eth_call(rpc_url, pool_address, _SEL_POSITIONS)
        return self._assemble_detail(rpc_url, pool_address, current_tick, position)

    def _fetch_univ3(
        self, rpc_url: str, pool_address: str, token_id: int
    ) -> LpPositionDetail:  # pragma: no cover - implemented in phase 4
        raise NotImplementedError("Uniswap-v3 two-hop read lands in Plan 0034 phase 4")

    def _assemble_detail(
        self, rpc_url: str, pool_address: str, current_tick: int, position: bytes
    ) -> LpPositionDetail:
        """Decode a `positions`-shaped return into tick bounds + owed amounts, read
        the two underlying tokens, and fold everything into an `LpPositionDetail`."""
        tick_lower = _decode_int(position, word=_POS_TICK_LOWER_WORD)
        tick_upper = _decode_int(position, word=_POS_TICK_UPPER_WORD)
        owed0_raw = _decode_uint(position, word=_POS_OWED0_WORD)
        owed1_raw = _decode_uint(position, word=_POS_OWED1_WORD)

        addr0 = _decode_address(self._eth_call(rpc_url, pool_address, _SEL_TOKEN0))
        addr1 = _decode_address(self._eth_call(rpc_url, pool_address, _SEL_TOKEN1))
        sym0 = _decode_string(self._eth_call(rpc_url, addr0, _SEL_SYMBOL))
        sym1 = _decode_string(self._eth_call(rpc_url, addr1, _SEL_SYMBOL))
        dec0 = _decode_uint(self._eth_call(rpc_url, addr0, _SEL_DECIMALS), word=0)
        dec1 = _decode_uint(self._eth_call(rpc_url, addr1, _SEL_DECIMALS), word=0)

        uncollected = _owed_tokens([(sym0, addr0, owed0_raw, dec0), (sym1, addr1, owed1_raw, dec1)])
        return LpPositionDetail(
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
            in_range=tick_lower <= current_tick < tick_upper,
            uncollected_fees=uncollected,
        )

    # -- transport ----------------------------------------------------------

    def _rpc_url_for(self, chain: Chain) -> str:
        key = _RPC_URL_KEYS.get(chain)
        if key is None:
            raise LpDetailConfigError(
                f"lp-detail: chain {chain!r} has no reserved RPC-URL secret — "
                "deep reads cover base / ethereum only",
            )
        url = self._secrets.get(key)
        if not url:
            raise LpDetailConfigError(
                f"lp-detail: no RPC URL configured — set `{key}` before enriching",
            )
        return url

    def _eth_call(self, rpc_url: str, to: str, data: str) -> bytes:
        """Perform one `eth_call` and return the decoded result bytes.

        Raises `LpDetailError` on a JSON-RPC error object / non-hex result and the
        shared rate-limit / unavailable errors on transport failure."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
        }
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


def _decode_address(data: bytes) -> str:
    """The low 20 bytes of the first word, as a lowercase `0x…` address."""
    return "0x" + _word(data, 0)[12:].hex()


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


def _owed_tokens(legs: list[tuple[str, str, int, int]]) -> list[PositionToken]:
    """Scale each `(symbol, address, raw_owed, decimals)` leg by its decimals into
    a `PositionToken`, dropping legs that owe nothing (a zero amount is "no fee",
    not a malformed token — `PositionToken` requires a positive amount)."""
    tokens: list[PositionToken] = []
    for symbol, address, raw, decimals in legs:
        if raw <= 0:
            continue
        tokens.append(
            PositionToken(symbol=symbol, address=address, amount=raw / (10**decimals)),
        )
    return tokens


__all__ = ["LpDetailConfigError", "LpDetailError", "RpcLpDetailAdapter"]
