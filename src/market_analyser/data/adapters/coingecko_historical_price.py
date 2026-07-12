"""CoinGecko keyless historical-price *fallback* + the primary→fallback chain
(Plan 0084 phase 4, ADR-0079; refines ADR-0034/0036 pricing).

The P&L engine values every leg at its own block time through a
`HistoricalPriceSource` (DefiLlama, `defillama.py`). One long-tail token on the
test wallet has no DefiLlama coverage, and under ADR-0036 a single unpriced leg
nulls the whole wallet total. This module adds a **secondary** source behind
DefiLlama so a primary miss falls through instead of failing the wallet.

**Why CoinGecko was chosen — and the keyless limitation the phase-6 smoke found.**
The plan preferred reusing an API we already depend on over adding a pinned package
(ADR-0012/0013). CoinGecko's `/coins/{platform}/contract/{address}/market_chart/
range` (and `/coins/{id}/market_chart/range` for the native coin) returns
historical USD prices at the point **nearest** the block timestamp — the same
nearest-indexed approximation DefiLlama's `historical/{ts}` makes.

**However, the Plan 0084 phase-6 smoke established that this endpoint is NOT
keyless: an unauthenticated call returns HTTP 401** (unlike `/simple/price`, still
keyless-200, which the macro adapter uses). So as wired keyless this fallback is
effectively inert — every call 401s and, by the chain's best-effort posture below,
degrades to "no coverage" (the token stays unpriced, its position incomplete). It
therefore does no harm but adds no coverage either. Closing the real "1 missing
price" gap needs a **keyed** historical price source — a CoinGecko demo/pro key
(`x-cg-demo-api-key`) or an Alchemy price fallback — which is a dependency/secret
decision for the architect (the plan's documented followup), deliberately not made
silently here.

**Determinism = the same snapshot mechanism (ADR-0036).** The fallback snapshots
into the **same** `PriceSnapshotRepository`, keyed by the **same** `token_key`, as
the primary. On a cold miss the primary snapshots nothing and the fallback
snapshots the resolved price; on any re-run the primary's snapshot-first read
returns that exact price, so the merged result is byte-identical regardless of
which source first resolved it (the snapshot records the price, not its origin).

**No price is `None`, never `0.0`.** A token CoinGecko does not cover at the
timestamp — an empty series, or a non-finite / non-positive point — returns
`None`, the typed "no coverage" the engine surfaces as an incomplete position;
nothing is coerced to zero or snapshotted as garbage. Errors follow the shared
taxonomy (ADR-0019): 429 → `RateLimitedError`, other HTTP/transport exhaustion →
`UpstreamUnavailableError`, a 2xx whose shape is broken → `CoinGeckoPriceError`.

Package-internal per ADR-0031: reached through the `HistoricalPriceSource`
Protocol and the composition root, never imported directly downstream.
"""

from __future__ import annotations

import math
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.defi.models import Chain
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository

_CONTRACT_URL = (
    "https://api.coingecko.com/api/v3/coins/{platform}/contract/{address}/market_chart/range"
)
_NATIVE_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart/range"
_SOURCE = "coingecko-historical-price"

# The snapshot repository is the durable cache; an HTTP-level TTL would only mask
# snapshot bugs (mirrors DefiLlama).
_CACHE_TTL_SECONDS = 0.0

# CoinGecko's per-chain asset-platform id (differs from our internal Chain names).
_PLATFORM_BY_CHAIN: dict[Chain, str] = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum-one",
    "optimism": "optimistic-ethereum",
}
# All four target chains are ETH-native; the native coin prices via the ETH id.
_NATIVE_COIN_ID = "ethereum"

# Half-width of the query window around the block timestamp. Wide enough that even
# daily-granularity old history (the free-tier resolution far in the past) returns
# at least one point to snap to; the nearest point is then selected.
_WINDOW_SECONDS = 2 * 86_400


class CoinGeckoPriceError(ValueError):
    """The upstream 2xx payload was structurally not the expected shape —
    raised at the adapter boundary before any snapshot write."""


class CoinGeckoHistoricalPriceAdapter:
    """Fetches a token's USD price nearest a past block timestamp from CoinGecko's
    keyless market-chart API, snapshot-cached (the P&L fallback price source)."""

    def __init__(
        self,
        *,
        http_client: ResilientHttpClient | None = None,
        snapshot_store: PriceSnapshotRepository | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_CACHE_TTL_SECONDS)
        )
        self._snapshots = snapshot_store

    def fetch_price(
        self,
        *,
        chain: Chain,
        address: str | None,
        ts: int,
    ) -> float | None:
        """The token's USD price nearest epoch-second `ts`, or `None` when
        CoinGecko has no usable coverage. Snapshot-first when a store is wired
        (keyed identically to the primary); a fresh resolution is snapshotted
        before being returned."""
        token = token_key(chain, address)
        if self._snapshots is not None:
            cached = self._snapshots.get(token, ts)
            if cached is not None:
                return cached
        url, params = _endpoint(chain, address, ts)
        if url is None:
            return None  # chain has no known CoinGecko platform id
        try:
            response = self._http.get(url, params=params, expect_json=True)
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        price = _nearest_price(response.json(), ts)
        if price is not None and self._snapshots is not None:
            self._snapshots.put(token, ts, price)
        return price


class ChainedHistoricalPriceSource:
    """A `HistoricalPriceSource` that tries `primary`, then `fallback` only on a
    primary miss (Plan 0084 phase 4). Both should share one snapshot store so the
    merged result is deterministic across re-runs (ADR-0036).

    Error posture is deliberately asymmetric. A **primary** error propagates — a
    DefiLlama outage means no prices at all, a real signal. A **fallback** error is
    swallowed to `None` (no coverage): the fallback is a best-effort *supplement*,
    so its failure must degrade to an honest incomplete position, never crash a
    reconstruction the primary alone would have completed. (This matters concretely:
    CoinGecko's keyless historical endpoint returns HTTP 401 — see the module note —
    so without this the fallback would 502 every wallet holding a DefiLlama-unpriced
    token.) A clean primary `None` still falls through to the fallback attempt."""

    def __init__(
        self,
        *,
        primary: HistoricalPriceSource,
        fallback: HistoricalPriceSource,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        price = self._primary.fetch_price(chain=chain, address=address, ts=ts)
        if price is not None:
            return price
        try:
            return self._fallback.fetch_price(chain=chain, address=address, ts=ts)
        except UpstreamDataError:
            # Best-effort supplement: a fallback transport failure (401 / throttle /
            # outage) is "no coverage", not a crash — the token stays unpriced and
            # its position honestly incomplete (ADR-0036), exactly as pre-phase-4.
            return None


def _endpoint(chain: Chain, address: str | None, ts: int) -> tuple[str | None, dict[str, str]]:
    """The CoinGecko market-chart-range URL + params for a token at `ts`, or
    `(None, {})` when the chain has no known platform id."""
    params = {
        "vs_currency": "usd",
        "from": str(ts - _WINDOW_SECONDS),
        "to": str(ts + _WINDOW_SECONDS),
    }
    if address is None:
        return _NATIVE_URL.format(coin_id=_NATIVE_COIN_ID), params
    platform = _PLATFORM_BY_CHAIN.get(chain)
    if platform is None:
        return None, params
    return _CONTRACT_URL.format(platform=platform, address=address.lower()), params


def _nearest_price(payload: Any, ts: int) -> float | None:
    """The price whose sample timestamp is nearest `ts` from a `market_chart`
    payload (`{"prices": [[ts_ms, price], ...]}`), or `None` when the series is
    empty / shape-broken / carries no finite positive point."""
    if not isinstance(payload, dict):
        raise CoinGeckoPriceError("coingecko-price: response was not a JSON object")
    prices = payload.get("prices")
    if not isinstance(prices, list):
        raise CoinGeckoPriceError("coingecko-price: response 'prices' is missing or not a list")
    best: float | None = None
    best_distance: float | None = None
    for point in prices:
        if not isinstance(point, list) or len(point) != 2:
            continue
        sample_ts_ms, raw_price = point
        if isinstance(sample_ts_ms, bool) or not isinstance(sample_ts_ms, (int, float)):
            continue
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            continue
        value = float(raw_price)
        if not math.isfinite(value) or value <= 0:
            continue  # garbage is no coverage, never snapshotted
        distance = abs(sample_ts_ms / 1000.0 - ts)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = value
    return best


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            "coingecko-price: rate limited (HTTP 429) fetching historical price"
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"coingecko-price: upstream unavailable ({detail}) fetching historical price",
    )


__all__ = [
    "ChainedHistoricalPriceSource",
    "CoinGeckoHistoricalPriceAdapter",
    "CoinGeckoPriceError",
]
