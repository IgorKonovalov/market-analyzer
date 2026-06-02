"""Crypto Fear & Greed adapter — Plan 0011 (ADR-0019, ADR-0007).

One HTTP call to Alternative.me's free, unauthenticated index endpoint
(`GET https://api.alternative.me/fng/?limit=1`) returns the current crypto
Fear & Greed reading: a 0-100 value plus a five-bucket classification. The call
goes through `ResilientHttpClient` (shared TTL cache / retry / backoff /
concurrency cap) — the index updates roughly once a day, so a 5-minute TTL
absorbs the "agent asks twice in a minute" pattern without ever serving a stale
reading in practice.

Upstream encodes both `value` and `timestamp` as strings; the adapter coerces
them and hands the result to `MarketSentimentSample`, which validates the range
(`0..100`) and the label (`Literal` over the five canonical buckets) at the
boundary — an out-of-range value or an unknown label raises rather than being
silently truncated or passed through.

Package-internal per ADR-0007: downstream code reaches this through the
`MarketDataProvider` Protocol, never by importing this class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.sources import MarketSentimentSource
from market_analyser.data.types import MarketSentimentSample

_FNG_URL = "https://api.alternative.me/fng/"
_SOURCE = "alternative.me-fng"

# 5-minute TTL: the index publishes daily, so this is generous (ADR-0019).
_DEFAULT_TTL_SECONDS = 300.0


class CryptoFearGreedError(ValueError):
    """The upstream payload was missing its `data` array or the leading entry —
    raised at the adapter boundary before model construction."""


class CryptoFearGreedAdapter(MarketSentimentSource):
    """Fetches the current crypto Fear & Greed reading from Alternative.me."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="crypto-fng",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
            )
        )

    def fetch_current(self) -> MarketSentimentSample:
        """Return the current reading. Raises `ResilientHttpError` on upstream
        exhaustion, `CryptoFearGreedError` on a shape-broken payload, and
        `pydantic.ValidationError` on an out-of-range value or unknown label."""
        response = self._http.get(_FNG_URL, params={"limit": 1}, expect_json=True)
        return self._parse(response.json())

    def _parse(self, payload: Any) -> MarketSentimentSample:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            raise CryptoFearGreedError("alternative.me-fng: payload missing 'data' entries")
        entry = data[0]
        return MarketSentimentSample(
            market="crypto",
            value=int(entry["value"]),
            classification=entry["value_classification"],
            published_at=datetime.fromtimestamp(int(entry["timestamp"]), tz=UTC),
            source=_SOURCE,
            window="current",
        )


__all__ = ["CryptoFearGreedAdapter", "CryptoFearGreedError"]
