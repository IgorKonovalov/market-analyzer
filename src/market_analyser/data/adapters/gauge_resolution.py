"""Gauge→pool resolution adapter — RPC `eth_call` over JSON-RPC (Plan 0084 / ADR-0079).

Aerodrome/Velodrome route liquidity-mining emissions through a per-pool **gauge**
contract distinct from the pool itself. The P&L replay engine (ADR-0036) joins a
transaction to a position by the position's `pool_address`, so a gauge `getReward`
transaction — whose `contract_address` is the *gauge*, not the pool — fails to
join and its rewards go unattributed (ADR-0079: the gauge indirection breaks the
join, not the vocabulary). This adapter resolves the missing link: gauge address →
pool address, via a single `gauge.pool()` read.

It implements the source-agnostic `GaugeResolutionSource` Protocol
(`data/sources.py`, ADR-0031) and is reached only through that seam. The read
reuses the LP-detail adapter's proven read-only transport (`rpc_eth_call`,
`gauge_pool_via_rpc`) and the same per-chain RPC-URL secret (`rpc_url_for`,
ADR-0038) — the `gauge.pool()` selector is exactly the one `lp_detail.py` already
uses for the staked-CL shape probe, shared rather than duplicated.

**In-process memoization, not a persisted snapshot (determinism note).** A gauge's
pool is an on-chain **immutable** — `gauge.pool()` returns the same address for the
life of the gauge — so, unlike revisable historical prices (`DefiLlamaAdapter`), a
re-resolution can never change a replay. Cross-run persistence therefore adds
nothing to determinism, and the plan stays migration-free: a per-instance cache of
both hits and misses bounds RPC within a run (a warm call reads the cache with zero
RPC) while a fresh process re-derives the identical, immutable mapping. Determinism
is preserved by construction, not by a stored snapshot.

**Read-only and precision-first.** The adapter holds no key, signs nothing, and
issues only the `eth_call` staticcall. A non-gauge address, a reverting `pool()`,
or a zero-address result resolves to `None` (never a raise, never a guess), so the
classifier degrades to an honest `unclassified` rather than a wrong attribution.
A transport failure (429 / 5xx / exhaustion) surfaces as the shared
`RateLimitedError` / `UpstreamUnavailableError`; a missing/unsupported RPC URL
raises `LpDetailConfigError` (the shared typed config error).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.adapters.lp_detail import (
    gauge_pool_via_rpc,
    rpc_url_for,
)
from market_analyser.defi.models import Chain
from market_analyser.persistence.secrets import SecretsStore

_SOURCE = "gauge-resolution-rpc"

# The snapshot repository / durable cache is deliberately absent: a gauge→pool
# mapping is an on-chain immutable, so an HTTP-level TTL would only mask the
# in-process memoization the adapter already does.
_CACHE_TTL_SECONDS = 0.0

# A pause before each RPC request, mirroring the LP-detail adapter: the configured
# Base provider enforces a strict per-second limit. Injectable so tests don't wait.
_INTER_REQUEST_SECONDS = 0.5


class GaugeResolutionAdapter:
    """Resolves a gauge contract address to its pool address over JSON-RPC,
    memoized per (chain, gauge) for the life of the instance."""

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
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep
        # Memoize both hits and misses so a repeated lookup (the common case — one
        # gauge pays many reward txs) reads zero RPC, and a known non-gauge is not
        # re-probed. Keyed by (chain, lowercased gauge address).
        self._cache: dict[tuple[Chain, str], str | None] = {}

    def resolve_pool(self, *, chain: Chain, gauge_address: str) -> str | None:
        """The pool address `gauge_address` distributes emissions for, lowercased,
        or `None` when the address is not a resolvable gauge (revert / zero
        address). Warm on the second call for the same (chain, gauge). Raises
        `LpDetailConfigError` on a missing/unsupported RPC URL and the shared
        rate-limit / unavailable errors on transport failure — but never on a
        revert, which is an honest `None`."""
        key = (chain, gauge_address.lower())
        # `in` (not `.get`) so a memoized `None` — a known non-gauge — is a cache
        # hit, never re-probed.
        if key in self._cache:
            return self._cache[key]
        rpc_url = rpc_url_for(self._secrets, chain)
        self._sleep(self._inter_request_seconds)
        pool = gauge_pool_via_rpc(self._http, rpc_url, gauge_address)
        resolved = pool.lower() if pool is not None else None
        self._cache[key] = resolved
        return resolved


__all__ = ["GaugeResolutionAdapter"]
