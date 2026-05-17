# ADR-0007 — Data layer: `MarketDataProvider` abstraction over vendored sources

> **Status:** accepted
> **Date:** 2026-05-17
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md)
> **Related ADRs:** [ADR-0003](0003-vendoring-strategy.md), [ADR-0004](0004-strategy-interface.md), [ADR-0006](0006-persistence-layout.md)

## Context

The vendored data layer ([ADR-0003](0003-vendoring-strategy.md)) exposes one service per source: `screener_service`, `yahoo_finance_service`, `sentiment_service`, `news_service`, `bitcoin_market_service`, and so on. That shape is correct for the MCP server those modules originated in, where each service maps to an MCP tool. For this desktop app it is wrong, because:

- The frontend (and any future headless CLI) wants a single stable contract: "give me OHLCV for `AAPL` `1d`". It does not care whether the bytes came from Yahoo, TradingView, or a local cache.
- The backtest engine ([ADR-0004](0004-strategy-interface.md)) must be reproducible. If two callers each pick a different service directly for "OHLCV of `BTCUSDT 1h`", backtest results become source-dependent and unstable.
- The persistence cache ([ADR-0006](0006-persistence-layout.md)) needs a single chokepoint to instrument. Five caches behind five services is the worst version of caching.
- Tests need to substitute the data layer. One Protocol is one mock; N services are N mocks.

An earlier abandoned Tauri-era bootstrap draft deferred this abstraction with the rationale "we have one provider, we don't need an interface yet — revisit when the second lands." That rationale held for a BTC-screener-only walking skeleton. The current bootstrap ([Plan 0001](../plans/0001-bootstrap.md)) introduces Yahoo Finance for OHLCV from slice 2, alongside the existing TradingView screener path — so we have two sources from day one, and the deferral premise no longer applies.

There is a tension to acknowledge: a separate decision committed us to **lazy vendoring** — only bring in tradingview-mcp modules as slices need them. A literal "rewrite everything upfront" interpretation of a unified provider would force us to vendor everything now. We reconcile this by inverting the order: the *Protocol* is declared upfront with stubs for every planned method, and each method's implementation lands when its underlying source is vendored. The Protocol is the schedule; each slice fills in one stub.

## Decision

We will declare a single `MarketDataProvider` Protocol in `src/market_analyser/data/provider.py` as the only data-layer contract that downstream code (sidecar endpoints, backtest engine, strategy runtime) is allowed to import. The Protocol covers OHLCV retrieval, symbol search, screener queries, sentiment lookup, and news lookup. Every method takes an explicit `as_of: datetime | None` argument used by the backtest engine to constrain results to data available at that point in time. That argument is the anti-lookahead seam at the data layer.

A `DefaultMarketDataProvider` implementation dispatches to per-source *adapters* (`adapters/yahoo.py`, `adapters/tradingview_screener.py`, `adapters/coingecko.py`, etc.). Each adapter is a thin port over the vendored tradingview-mcp service for that source. Adapters are package-internal to `src/market_analyser/data/`; downstream code never imports them. Vendored files themselves remain untouched per [ADR-0003](0003-vendoring-strategy.md) — adapters wrap, not edit.

Methods not yet implemented raise `NotImplementedError` with a message naming the slice expected to land them. The test suite asserts each method is callable after its owning slice ships, so forgotten stubs surface as failed tests, not runtime crashes in production.

## Consequences

### Positive
- One stable interface across all downstream consumers. Swapping a source later (Yahoo → a paid feed) is a one-adapter change, not an every-caller change.
- A single chokepoint for caching (per [ADR-0006](0006-persistence-layout.md)), rate limiting, telemetry, and `as_of`-based backtest reproducibility.
- Tests rely on one `FakeMarketDataProvider`. Backtests run against a deterministic in-memory provider in CI.
- Clean sibling-skill ownership: `strategy-author` and `backtester` consume the Provider; `ui-builder` consumes the sidecar HTTP API which itself consumes the Provider. Nobody reaches around the abstraction.
- Compatible with the vendoring discipline in [ADR-0003](0003-vendoring-strategy.md): adapters are non-vendored code, vendored code stays verbatim.

### Negative
- Upfront design cost on the Protocol shape. A wrong shape costs an adapter-rewrite cycle across every source.
- An indirection layer that does not exist upstream — pulling future fixes from `tradingview-mcp` requires translating across the adapter, not direct copy. The drift-check script from ADR-0003 reduces but does not eliminate this cost.
- `NotImplementedError` stubs are landmines if forgotten. Mitigation as above: tests assert per-slice readiness.
- The Protocol locks the surface area. Every new data source must fit it or earn a new method. This is by design — we want drive-by adapter additions to be visible architecture changes — but it slows down impulsive integrations.
- We accept that the abstraction will look slightly wrong for a year or two as the second and third sources land. Premature interface refinement is a worse trade than living with rough edges and refactoring once the shape is empirical.

### Neutral
- The `data/` package becomes the only directory in the repo where vendored tradingview-mcp code lives (alongside `data/adapters/` and `data/provider.py`). Provenance stays auditable.

## Alternatives considered

### Alternative A — Keep service-per-source (the original abandoned-draft deferral)
Zero abstraction cost now. Rejected because as of Plan 0001 we have two sources from day one (Yahoo + TradingView screener), and the cache-chokepoint + reproducibility arguments now bite immediately. "Refactor later" loses to "do it once at the start" when the right interface is reasonably clear, which it is here.

### Alternative B — Thin facade only (callers still pick a source)
A halfway position: have a `MarketDataProvider` namespace but require callers to pass which source. Rejected because it leaks source choice into callers, which is exactly the coupling we want to remove. Either source selection is hidden or it isn't.

### Alternative C — Full rewrite of vendored services (drop ADR-0003 vendoring)
Replace vendored code with original implementations behind the Protocol. Rejected because it discards ADR-0003's upstream-fix flow and the existing vendored implementations are battle-tested. The Protocol gives us the surface we want without touching the vendored core.

## Notes

- The Protocol is synchronous for the bootstrap. An `AsyncMarketDataProvider` is reserved for when a real concurrency need surfaces (e.g. fan-out screening across many symbols). Two parallel interfaces from day one is over-engineering.
- All Provider methods return pydantic models defined in `src/market_analyser/data/types.py`, not raw dicts or DataFrames. The data layer can use pandas internally for performance but does not leak it across the Protocol — that is what lets us swap implementations without rewriting callers.
- The `as_of` argument is non-optional in spirit: it is `datetime | None` only because live-mode callers pass `None`. Backtest callers always pass a real `datetime`. The default in tests is to pass a fixed `as_of` so determinism failures surface loudly.
- The interaction with [ADR-0004](0004-strategy-interface.md) (strategy interface): strategies do NOT call the provider. The backtest engine and the live runtime fetch bars via the provider and hand them to `generate_signals` as a `Sequence[Bar]`. The provider is invisible to strategies.
- The interaction with [ADR-0006](0006-persistence-layout.md) (persistence): the provider's caching adapter writes to `bars` and reads via the repository layer. Caching is the only reason the provider knows persistence exists.
