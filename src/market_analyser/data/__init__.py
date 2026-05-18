"""Data layer: MarketDataProvider Protocol and adapters.

Only `provider.MarketDataProvider`, `default_provider.DefaultMarketDataProvider`,
and the pydantic models in `types` are part of the downstream contract. Adapters
are package-internal per ADR-0007.
"""
