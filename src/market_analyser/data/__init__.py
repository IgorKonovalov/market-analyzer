"""Data layer: MarketDataProvider Protocol, adapters, and vendored sources.

Only `provider.MarketDataProvider`, `default_provider.DefaultMarketDataProvider`,
and the pydantic models in `types` are part of the downstream contract. Adapters
and the vendored tree are package-internal per ADR-0007.
"""
