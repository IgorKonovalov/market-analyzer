# ADR-0026 — Symbol search is bound to the OHLCV provider

> **Status:** proposed
> **Date:** 2026-05-26
> **Related plan(s):** [0024-symbol-search-and-autocomplete](../plans/0024-symbol-search-and-autocomplete.md)

## Context

The `MarketDataProvider` Protocol has declared `search_symbols(query, as_of) -> Sequence[SymbolInfo]` since Plan 0001, but it has never been implemented — `DefaultMarketDataProvider.search_symbols` raises `NotImplementedError`. The triggering problem: a user types `BTC` or `ETH` into the symbol field, the app calls `get_ohlcv("BTC")`, Yahoo has no such ticker (it wants `BTC-USD`), and the fetch fails with no recovery path. The user wants a TradingView-style dropdown that suggests valid symbols with the market they trade on.

The load-bearing question is *which upstream the suggestions come from*, and it is a genuine decision because the obvious "richest" answer is a trap. TradingView's search endpoint returns exchange-qualified symbols in its own namespace — `BINANCE:BTCUSDT`, `COINBASE:BTCUSD`, `NASDAQ:AAPL`. Our OHLCV path is Yahoo-only (`get_ohlcv` → `YahooAdapter` → `_yahoo_fetch`), and Yahoo does not understand those symbols. A dropdown sourced from TradingView would therefore surface suggestions the app cannot chart: the user picks `BINANCE:BTCUSDT`, the chart fetch fails, and we have moved the original failure one step later — after the user has already committed to a choice that looked valid.

The whole value of the feature is that *a suggested symbol is chartable*. Any data source whose symbol namespace `get_ohlcv` can't resolve breaks that invariant unless we also build and maintain a per-asset-class symbol resolver (TradingView → Yahoo), which is fragile and has coverage gaps for exotic exchanges.

Yahoo's own search endpoint (`/v1/finance/search`) returns symbols in Yahoo's native namespace — `BTC-USD`, `AAPL`, `BTC=F` — together with name, exchange display, and asset type. Every result is directly fetchable by the existing OHLCV path, with no mapping layer.

## Decision

We will source symbol search from the **same provider that serves OHLCV** — Yahoo, via its `/v1/finance/search` endpoint — so that every suggestion lives in the OHLCV path's own symbol namespace and is directly fetchable by `get_ohlcv`. `search_symbols` dispatches through the `MarketDataProvider` Protocol like every other data-layer call; the suggestion namespace and the fetch namespace are the same set by construction.

As a durable invariant: **we will not introduce a symbol-search source whose namespace `get_ohlcv` cannot resolve** without also shipping a dedicated, tested resolver from that namespace into a fetchable one. Search breadth never comes at the cost of suggesting symbols the app cannot chart.

## Consequences

### Positive
- Every suggested symbol is chartable — the feature actually closes the "BTC returns nothing" failure instead of relocating it.
- No symbol-resolution / mapping layer, and no fragile per-asset-class translation rules to maintain.
- One upstream for both fetch and search, so the Plan 0013 typed-error taxonomy (`UpstreamDataError` and friends) applies uniformly, and the agent gains a recovery path for `UnknownSymbolError`: call `search_symbols`, then retry `get_ohlcv` with a returned symbol.
- Keyless: Yahoo search needs no API key, so it lands without touching the deferred third-party-API-key ADR.

### Negative
- Search breadth is limited to Yahoo's universe and its **canonical pairs**. The user sees `BTC-USD`, not the per-exchange rows (`BINANCE:BTCUSDT` vs `COINBASE:BTCUSD`) TradingView would show. For this app — which charts a single canonical series per symbol — that granularity has no consumer, but it is a real reduction in displayed coverage versus TradingView.
- If a future plan adds a **second OHLCV provider** with a different symbol namespace, search must fan out per provider (each provider searches its own namespace), not unify across them — or that provider needs its own resolver. This ADR should be revisited at that point.

### Neutral
- `SymbolInfo` carries `exchange` and `quote_type` as Yahoo display strings (`exchDisp` / `typeDisp`), so the renderer can show "BTC-USD · Bitcoin USD · Cryptocurrency" without a code→label lookup table.

## Alternatives considered

### Alternative A — TradingView symbol search
We already have a TradingView screener adapter, and TradingView's search has broader, per-exchange coverage. Rejected because its symbols (`EXCHANGE:TICKER`) are in a namespace the Yahoo OHLCV path cannot fetch; making picks chartable would require a fragile TV→Yahoo resolver with per-asset-class rules and inevitable coverage gaps, reintroducing the exact unchartable-pick failure the feature exists to remove.

### Alternative B — Static curated symbol list
Ship a bundled JSON of common symbols + exchanges. Rejected because it is stale by definition, has no long-tail coverage (any symbol nobody added simply doesn't exist), and demands perpetual manual maintenance — a poor fit for a feature whose job is to find the symbol the user didn't know the exact ticker for.

## Notes

This ADR pairs with Plan 0024. It moves to `accepted` at that plan's close ceremony if the Yahoo-search implementation confirms the decision held. The "bind search to the fetch provider" invariant is the part worth remembering: a future contributor tempted to widen coverage with TradingView search should read this first.
