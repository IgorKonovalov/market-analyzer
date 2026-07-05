"""DefiLlama historical-price adapter (ADR-0034/0036, Plan 0035 phase 4).

Implements `HistoricalPriceSource` against the keyless DefiLlama coins API:
`GET https://coins.llama.fi/prices/historical/{ts}/{token}` where `token` is
the canonical coin key — `chain:address` for a contract token, or
`coingecko:ethereum` for the native coin (all four target chains are
ETH-native). No API key, no secret (ADR-0038 adds nothing here).

**Snapshot cache = the determinism mechanism (ADR-0036).** When a
`PriceSnapshotRepository` is wired (composition root, the
`CryptoFearGreedAdapter(metric_store=…)` write-through precedent), every
lookup reads the snapshot first and returns it without touching the network;
a fresh resolution is snapshotted first-write-wins before it is returned. A
re-run therefore re-reads the exact prices the first run used, even if
DefiLlama later revises. An unwired store (tests, ad-hoc use) degrades to
pass-through fetches.

**No price is `None`, never `0.0`.** A token DefiLlama does not cover at the
timestamp — absent from the response, or carrying a non-finite / non-positive
price — returns `None`, the typed "no coverage" the engine surfaces as an
*incomplete* position (ADR-0036 loud failure). Nothing is coerced to zero,
and a garbage upstream price is treated as no coverage rather than snapshotted
into every future replay.

Errors follow the shared taxonomy (ADR-0019): 429 → `RateLimitedError`, other
HTTP/transport exhaustion → `UpstreamUnavailableError`, a 2xx whose shape is
broken → `DefiLlamaError`.

Package-internal per ADR-0031: reached through the `HistoricalPriceSource`
Protocol and the composition root, never imported directly downstream.
"""

from __future__ import annotations

import math
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.defi.models import Chain
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository

_HISTORICAL_URL = "https://coins.llama.fi/prices/historical/{ts}/{token}"
_SOURCE = "defillama"

# The snapshot repository is the durable cache; an HTTP-level TTL would only
# mask snapshot bugs.
_CACHE_TTL_SECONDS = 0.0

# All four target chains (ADR-0034) settle in ETH; DefiLlama keys native-coin
# lookups by the coingecko id.
_NATIVE_COIN_KEY = "coingecko:ethereum"


class DefiLlamaError(ValueError):
    """The upstream 2xx payload was structurally not the expected shape —
    raised at the adapter boundary before any snapshot write."""


class DefiLlamaAdapter:
    """Fetches token USD prices at past timestamps, snapshot-cached."""

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
        """The token's USD price at epoch-second `ts`, or `None` when
        DefiLlama has no usable coverage. Snapshot-first when a store is
        wired; a fresh resolution is snapshotted before being returned."""
        token = token_key(chain, address)
        if self._snapshots is not None:
            cached = self._snapshots.get(token, ts)
            if cached is not None:
                return cached
        try:
            response = self._http.get(
                _HISTORICAL_URL.format(ts=ts, token=token),
                expect_json=True,
            )
        except ResilientHttpError as err:
            raise _classify_error(err) from err
        price = _parse_price(response.json(), token)
        if price is not None and self._snapshots is not None:
            self._snapshots.put(token, ts, price)
        return price


def token_key(chain: Chain, address: str | None) -> str:
    """The canonical DefiLlama coin key — also the snapshot-cache key, exposed
    so the engine's incomplete-position notes can name the token it lacked."""
    if address is None:
        return _NATIVE_COIN_KEY
    return f"{chain}:{address.lower()}"


def _parse_price(payload: Any, token: str) -> float | None:
    if not isinstance(payload, dict):
        raise DefiLlamaError("defillama: response was not a JSON object")
    coins = payload.get("coins")
    if not isinstance(coins, dict):
        raise DefiLlamaError("defillama: response 'coins' is missing or not an object")
    entry = coins.get(token)
    if entry is None:
        return None  # no coverage for this token at this timestamp
    if not isinstance(entry, dict):
        raise DefiLlamaError(f"defillama: coin entry for {token!r} was not an object")
    price = entry.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    value = float(price)
    if not math.isfinite(value) or value <= 0:
        # A NaN/zero/negative "price" is garbage, not a valuation — treat as
        # no coverage so it can never be snapshotted or multiplied into P&L.
        return None
    return value


def _classify_error(err: ResilientHttpError) -> UpstreamDataError:
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError("defillama: rate limited (HTTP 429) fetching historical price")
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"defillama: upstream unavailable ({detail}) fetching historical price",
    )


__all__ = ["DefiLlamaAdapter", "DefiLlamaError", "token_key"]
