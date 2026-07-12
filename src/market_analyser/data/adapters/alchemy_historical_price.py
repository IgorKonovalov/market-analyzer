"""Alchemy Prices API historical-price fallback (Plan 0087 / ADR-0081; refines
ADR-0079's price-fallback seam, ADR-0034/0036 pricing).

Plan 0084's keyless CoinGecko fallback proved inert — its `market_chart/range`
returns HTTP 401 without a key — so a long-tail token DefiLlama cannot price still
nulls the whole wallet total (ADR-0036: one unpriced leg suppresses the total).
This adapter is the keyed replacement: Alchemy's Prices API serves historical USD
prices by network + contract address (verified for Base), reached over the
in-house `ResilientHttpClient` — a REST source and a **secret, not a new pinned
package**, so it carries no ADR-0012/0013 cooldown.

**Auth is a header, not the path (secret hygiene).** The key travels as
`Authorization: Bearer <key>` against the key-less `…/prices/v1/tokens/historical`
URL — never embedded in the URL path — because the resilient client's failure log
records the URL *path* (query and headers are never logged, ADR-0038 rule 1). A
path-embedded key would leak into that warning on any 401 / timeout; the header
keeps it out. The key is read **lazily** from the secrets store per call, so the
adapter constructs before a key exists and, absent the key, is simply **inert** —
it issues no request and returns `None` (no coverage), exactly the pre-0087
degraded behavior (an honest incomplete position, never a crash).

**Window-bracket + nearest point.** Alchemy's historical endpoint has no
single-timestamp query — it returns a series over `[startTime, endTime]` at a
chosen `interval` (span caps 7d/30d/1yr for 5m/1h/1d). We bracket the block
timestamp with a bounded window (well inside the 30d cap at the 1h interval) and
take the point **nearest** `ts` — the same nearest-indexed approximation DefiLlama
and the CoinGecko adapter make, so it is a like-for-like fallback, not a weaker one.

**Determinism = the same snapshot mechanism (ADR-0036).** The fallback snapshots
into the **same** `PriceSnapshotRepository`, keyed by the **same** `token_key`, as
the primary, so a re-run is byte-identical regardless of which source first
resolved a price (the snapshot records the price, not its origin).

**No price is `None`, never `0.0`.** An empty series, or a non-finite / non-positive
point, returns `None` — the typed "no coverage" the engine surfaces as an
incomplete position; nothing is coerced to zero or snapshotted as garbage. Errors
follow the shared taxonomy (ADR-0019): 429 → `RateLimitedError`, other HTTP /
transport exhaustion → `UpstreamUnavailableError`, a 2xx whose shape is broken →
`AlchemyPriceError`.

Package-internal per ADR-0031: reached through the `HistoricalPriceSource`
Protocol and the composition root, never imported directly downstream.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository
from market_analyser.persistence.secrets import SecretsStore

_HISTORICAL_URL = "https://api.g.alchemy.com/prices/v1/tokens/historical"
_SOURCE = "alchemy-historical-price"

# The snapshot repository is the durable cache; an HTTP-level TTL would only mask
# snapshot bugs (mirrors DefiLlama / the CoinGecko fallback).
_CACHE_TTL_SECONDS = 0.0

# Alchemy's per-network id (differs from our internal Chain names). All four target
# chains are ETH-native; the native coin prices by the "ETH" symbol.
_NETWORK_BY_CHAIN: dict[Chain, str] = {
    "ethereum": "eth-mainnet",
    "base": "base-mainnet",
    "arbitrum": "arb-mainnet",
    "optimism": "opt-mainnet",
}
_NATIVE_SYMBOL = "ETH"

# The series interval. 1h keeps the bracket window (below) well inside Alchemy's
# 30d span cap for this interval while giving block-time-adjacent resolution.
_INTERVAL = "1h"

# Half-width of the query window around the block timestamp. Two days keeps the
# full span (4d) comfortably inside the 1h-interval 30d cap while being wide enough
# that even a sparsely-traded token returns at least one point to snap to; the
# nearest point is then selected, so the width does not cost precision.
_WINDOW_SECONDS = 2 * 86_400


class AlchemyPriceError(ValueError):
    """The upstream 2xx payload was structurally not the expected shape —
    raised at the adapter boundary before any snapshot write."""


class AlchemyHistoricalPriceAdapter:
    """Fetches a token's USD price nearest a past block timestamp from Alchemy's
    keyed Prices API, snapshot-cached (the P&L fallback price source). Inert until
    an `alchemy_prices_key` secret is present."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore | None = None,
        http_client: ResilientHttpClient | None = None,
        snapshot_store: PriceSnapshotRepository | None = None,
    ) -> None:
        self._secrets = secrets_store
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
        """The token's USD price nearest epoch-second `ts`, or `None` when Alchemy
        has no usable coverage (or no key is configured). Snapshot-first when a
        store is wired (keyed identically to the primary); a fresh resolution is
        snapshotted before being returned."""
        token = token_key(chain, address)
        if self._snapshots is not None:
            cached = self._snapshots.get(token, ts)
            if cached is not None:
                return cached
        key = self._secrets.get("alchemy_prices_key") if self._secrets is not None else None
        if not key:
            # Unkeyed: inert. No request is issued and the key (absent) is never
            # touched — the chain degrades to no-coverage, the pre-0087 behavior.
            return None
        body = _request_body(chain, address, ts)
        if body is None:
            return None  # chain has no known Alchemy network id
        try:
            # Key in the Authorization header (never the URL path) so it cannot
            # reach the client's path-only failure log. Explicit body-sensitive
            # cache key: the POST body varies per (token, ts) but the URL does not.
            response = self._http.post(
                _HISTORICAL_URL,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                cache_key=f"{_SOURCE}:{token}:{ts}",
                expect_json=True,
            )
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        price = _nearest_price(response.json(), ts)
        if price is not None and self._snapshots is not None:
            self._snapshots.put(token, ts, price)
        return price


def _request_body(chain: Chain, address: str | None, ts: int) -> dict[str, Any] | None:
    """The Alchemy `tokens/historical` request body for a token at `ts`, or `None`
    when the chain has no known Alchemy network id. Identified by symbol for the
    native coin, by network + contract address otherwise."""
    common: dict[str, Any] = {
        "startTime": _iso(ts - _WINDOW_SECONDS),
        "endTime": _iso(ts + _WINDOW_SECONDS),
        "interval": _INTERVAL,
    }
    if address is None:
        return {"symbol": _NATIVE_SYMBOL, **common}
    network = _NETWORK_BY_CHAIN.get(chain)
    if network is None:
        return None
    return {"network": network, "address": address.lower(), **common}


def _iso(ts: int) -> str:
    """`ts` (UTC epoch seconds) as an ISO-8601 `…Z` string — deterministic given
    `ts` (no wall-clock read)."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nearest_price(payload: Any, ts: int) -> float | None:
    """The price whose sample timestamp is nearest `ts` from an Alchemy historical
    payload (`{"data": [{"value": "1.23", "timestamp": "2024-01-01T00:00:00Z"}, …]}`),
    or `None` when the series is empty / shape-broken / carries no finite positive
    point."""
    if not isinstance(payload, dict):
        raise AlchemyPriceError("alchemy-price: response was not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise AlchemyPriceError("alchemy-price: response 'data' is missing or not a list")
    best: float | None = None
    best_distance: float | None = None
    for point in data:
        if not isinstance(point, dict):
            continue
        value = _coerce_price(point.get("value"))
        if value is None:
            continue  # garbage / non-positive is no coverage, never snapshotted
        sample_ts = _coerce_epoch(point.get("timestamp"))
        if sample_ts is None:
            continue
        distance = abs(sample_ts - ts)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = value
    return best


def _coerce_price(raw: Any) -> float | None:
    """A finite, strictly-positive float from Alchemy's `value` (a JSON string or
    number), or `None` for anything else — no coverage, never `0.0`."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _coerce_epoch(raw: Any) -> float | None:
    """Epoch seconds from Alchemy's `timestamp` (an ISO-8601 string, or a numeric
    epoch), or `None` when unparseable. `Z` is normalized for `fromisoformat`
    (portable across 3.10+); a naive timestamp is read as UTC."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    return None


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("alchemy-price: rate limited (HTTP 429) fetching historical price")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"alchemy-price: upstream unavailable ({detail}) fetching historical price",
    )


__all__ = [
    "AlchemyHistoricalPriceAdapter",
    "AlchemyPriceError",
]
