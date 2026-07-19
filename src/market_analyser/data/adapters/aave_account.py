"""Aave v3 aggregate account-health adapter — RPC `eth_call` (Plan 0042 phase 1).

The lending *depth* half of the DeFi program (ADR-0034): discovery (Zerion) tells us
a wallet holds Aave supply/borrow positions but not the account's health factor,
collateral/debt totals, or liquidation threshold — the risk-grade current state the
scenario engine (ADR-0037) needs. This adapter reads them on-chain in one `eth_call`
to the Aave v3 `Pool.getUserAccountData(user)` and returns an `AaveAccountDetail`. It
implements the source-agnostic `AaveAccountSource` Protocol (`data/sources.py`,
ADR-0031) and is reached only through that seam.

`getUserAccountData` is a per-`(wallet, chain)` **aggregate** read: one call returns
the account-wide collateral / debt / threshold / HF across all of the wallet's Aave
positions on the chain, so an `AaveAccountDetail` is a wallet+chain fact, not a
per-position fold (contrast LP enrichment).

Reuses the shared read-only RPC transport the LP-detail adapter proves out
(`rpc_eth_call` / `rpc_url_for`, ADR-0019/0038) — the same `eth_call`, the same
per-chain RPC-URL secret (`base_rpc_url` / `eth_rpc_url`; deep reads cover base /
ethereum only), and the same typed error taxonomy: a missing RPC URL / unsupported
chain raises `LpDetailConfigError`, a 429 / outage maps to `RateLimitedError` /
`UpstreamUnavailableError`, and a JSON-RPC revert or a too-short result raises
`LpDetailError`. Read-only by construction (the only method is `eth_call`, a
staticcall); no cache (deep state is live, like `lp_detail`).

Return decoding (`getUserAccountData` → 6 `uint256` words): `totalCollateralBase`,
`totalDebtBase`, `availableBorrowsBase` (base currency, USD 8-decimals → `/1e8`);
`currentLiquidationThreshold`, `ltv` (basis points → `/1e4`); `healthFactor`
(WAD → `/1e18`). A **no-debt** account (`totalDebtBase == 0`) has an undefined HF
(Aave returns `type(uint256).max`) → carried as `None`, never a fabricated number.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.adapters.lp_detail import (
    LpDetailConfigError,
    LpDetailError,
    rpc_eth_call,
    rpc_url_for,
)
from market_analyser.defi.models import AaveAccountDetail, Chain
from market_analyser.persistence.secrets import SecretsStore

_SOURCE = "aave-account-rpc"
# Deep state is live/volatile (ADR-0034) and a read is request-triggered, so the
# adapter does not cache — each read is fresh (mirrors `lp_detail`).
_CACHE_TTL_SECONDS = 0.0

# getUserAccountData(address) -> (totalCollateralBase, totalDebtBase,
# availableBorrowsBase, currentLiquidationThreshold, ltv, healthFactor). The selector
# is keccak256(signature)[:4], self-checked in the tests (the only ground truth; a
# wrong selector reverts, never corrupts a read).
_SEL_GET_USER_ACCOUNT_DATA = "0xbf92857c"

# Canonical Aave v3 `Pool` address per chain (checksummed on-chain, lowercased here to
# match the repo's address convention). Only chains with a reserved RPC-URL secret are
# reachable — `rpc_url_for` covers base / ethereum — so a position on another chain
# fails typed at URL resolution before a Pool address is needed.
_POOL_ADDRESSES: dict[Chain, str] = {
    "ethereum": "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
    "base": "0xa238dd80c259a72e81d7e4664a9801593f98d1c5",
}

_WORD_BYTES = 32
# The base currency on the target Aave v3 markets is USD with 8 decimals; the
# liquidation threshold and LTV are basis points; the health factor is WAD (1e18).
_BASE_UNIT = 10**8
_BPS_DENOMINATOR = 10_000
_WAD = 10**18


class AaveAccountAdapter:
    """Reads a wallet's aggregate Aave v3 account health over JSON-RPC (the lending
    depth the scenario engine consumes). Implements `AaveAccountSource`."""

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

    def fetch_account_detail(self, *, chain: Chain, owner: str) -> AaveAccountDetail:
        """Return the wallet's aggregate Aave v3 account health on `chain`.

        Raises `LpDetailConfigError` on a missing RPC URL / a chain with no known Aave
        v3 `Pool`, the shared `RateLimitedError` / `UpstreamUnavailableError` on
        throttle / outage, and `LpDetailError` on a revert or a shape-broken result."""
        rpc_url = rpc_url_for(self._secrets, chain)
        pool = self._pool_for(chain)
        data = _SEL_GET_USER_ACCOUNT_DATA + _addr_arg(owner)
        result = rpc_eth_call(self._http, rpc_url, pool, data)
        return _decode_account_detail(result, chain)

    def _pool_for(self, chain: Chain) -> str:
        pool = _POOL_ADDRESSES.get(chain)
        if pool is None:
            raise LpDetailConfigError(
                f"aave-account: no Aave v3 Pool known for chain {chain!r} "
                "(deep reads cover base / ethereum only)",
            )
        return pool


def _addr_arg(address: str) -> str:
    """A 20-byte address left-padded to a 32-byte ABI word (hex, no `0x`)."""
    return bytes.fromhex(address[2:]).rjust(_WORD_BYTES, b"\x00").hex()


def _uint_word(data: bytes, index: int) -> int:
    """The unsigned integer in 32-byte word `index`; a result too short is a typed
    `LpDetailError` (the read cannot be decoded), never a silent zero."""
    start = index * _WORD_BYTES
    word = data[start : start + _WORD_BYTES]
    if len(word) != _WORD_BYTES:
        raise LpDetailError(f"aave-account: result too short for word {index}")
    return int.from_bytes(word, "big")


def _decode_account_detail(data: bytes, chain: Chain) -> AaveAccountDetail:
    """Decode a `getUserAccountData` return (6 `uint256` words) into an
    `AaveAccountDetail` with base/bps/WAD scaling; a no-debt account → `health_factor`
    of `None`. Out-of-range values are rejected by the model boundary."""
    total_collateral = _uint_word(data, 0)
    total_debt = _uint_word(data, 1)
    available_borrows = _uint_word(data, 2)
    liquidation_threshold_bps = _uint_word(data, 3)
    ltv_bps = _uint_word(data, 4)
    health_factor_wad = _uint_word(data, 5)
    return AaveAccountDetail(
        chain=chain,
        total_collateral_base=total_collateral / _BASE_UNIT,
        total_debt_base=total_debt / _BASE_UNIT,
        available_borrows_base=available_borrows / _BASE_UNIT,
        liquidation_threshold=liquidation_threshold_bps / _BPS_DENOMINATOR,
        ltv=ltv_bps / _BPS_DENOMINATOR,
        # No debt → Aave returns type(uint256).max (an undefined HF); carry None
        # rather than a nonsensical ~1e59 float.
        health_factor=None if total_debt == 0 else health_factor_wad / _WAD,
        as_of=_now(),
    )


def _now() -> datetime:
    """Wall-clock seam (provenance `as_of`), monkeypatched by tests to freeze time."""
    return datetime.now(tz=UTC)


__all__ = ["AaveAccountAdapter"]
