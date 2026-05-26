"""Data layer: MarketDataProvider Protocol and adapters.

Only `provider.MarketDataProvider`, `default_provider.DefaultMarketDataProvider`,
the pydantic models in `types`, and the typed error taxonomy in `errors` are
part of the downstream contract. Adapters are package-internal per ADR-0007.
"""

from __future__ import annotations

from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamDataError,
    UpstreamUnavailableError,
)

__all__ = [
    "RateLimitedError",
    "UnknownSymbolError",
    "UpstreamDataError",
    "UpstreamUnavailableError",
]
