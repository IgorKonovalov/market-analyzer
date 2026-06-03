"""DeFi domain package (ADR-0035).

A cohesive sibling to `data/` / `analysis/` / `backtest/` under the single
`market_analyser` import root — **not** a separate `src/defi_analyser/` package.
It holds the DeFi domain objects and logic (the normalized position model here;
the discovery service, P&L engine, and risk engine in later plans). On-chain
*fetch* is source-adapter-shaped and lives in `data/` (per-capability Protocols
in `data/sources.py`, adapters in `data/adapters/`); this package consumes those
sources and never imports `api/` (ADR-0032).
"""
