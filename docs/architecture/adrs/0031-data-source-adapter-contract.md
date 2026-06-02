# ADR-0031 — Per-capability data-source adapter contract

> **Status:** accepted
> **Date:** 2026-05-31 (accepted 2026-06-02 at Plan 0028 close)
> **Related plan(s):** [0028-data-layer-boundary-hardening](../plans/0028-data-layer-boundary-hardening.md)

## Context

Strategies have a clean producer-side contract and a discovery seam: a strategy is a module exporting `META` / `Params` / `generate_signals`, and `contracts/strategy.discover()` walks the package, validates each module, and keys it by id (ADR-0004). Adding a strategy is a one-file drop — no registry edit, no central wiring.

Data sources have no equivalent. The *consumer* side is well-abstracted — every downstream caller depends only on the `MarketDataProvider` Protocol (ADR-0007) — but the *producer* side has no contract at all. Each adapter invents its own method name and shape: `YahooAdapter.fetch_ohlcv` / `.search`, `YahooQuoteAdapter.get_quote`, `TradingViewScreenerAdapter.query`, `RssNewsAdapter.fetch`, `StockTwitsAdapter.fetch_sentiment`, `CryptoFearGreedAdapter.fetch_current`. Nothing checks that a new adapter conforms to anything.

Every source is then hand-threaded through `DefaultMarketDataProvider`: a constructor field (`default_provider.py:91-108`) plus a bespoke dispatch method body. Two of those bodies have already grown source-selector ladders — `get_sentiment`'s `if source == "rss-vader" / elif "stocktwits"` (`default_provider.py:341-347`) and `get_market_sentiment`'s `if market != "crypto"` (`:402-405`) — and the RSS-VADER path inlines a whole news-fetch-plus-aggregation block (`_news_vader_sentiment`, `:349-367`) into the provider itself. Both ladders grow by one branch per new source.

The forces: the project's stated purpose includes contributors adding new data sources over time, and the data layer is the one we own and evolve in-house (ADR-0009). The asymmetry between the strategy seam (drop a file) and the adapter seam (edit 4-5 files, with no shape to conform to) is the friction we want to remove — without pretending heterogeneous operations are homogeneous.

A key constraint distinguishes adapters from strategies: **strategy modules are pure and stateless, so they can be auto-instantiated by a package walk; adapters are stateful wired objects** (they hold a `ResilientHttpClient`, proxy config, cache TTLs). Auto-discovery-by-import is the wrong mirror for adapters — their construction is not free.

## Decision

We will give data sources a **producer-side contract as a set of per-capability Protocols** — one narrow Protocol per operation kind, not a single uber-interface — defined in a new `data/sources.py`:

> `OhlcvSource` (`fetch_ohlcv`), `SymbolSearchSource` (`search`), `QuoteSource` (`get_quote`), `ScreenerSource` (`query`), `NewsSource` (`fetch`), `SentimentSource` (`fetch_sentiment`), `MarketSentimentSource` (`fetch_current`). Each existing adapter is annotated as implementing the relevant Protocol(s); their method names stay as-is where they already match, and are normalized where they don't.

For the two operations that select among interchangeable sources — `get_sentiment(source=...)` and `get_market_sentiment(market=...)` — we will replace the inline `if/elif` ladders with an explicit **registry dict** (`dict[str, SentimentSource]`, `dict[str, MarketSentimentSource]`) built once in the provider constructor. Adding a sentiment source becomes "implement `SentimentSource`, add one registry entry," not "edit the dispatch body." The inlined RSS-VADER aggregation moves out of the provider into its own `RssVaderSentimentAdapter` (composing the news source + VADER), so it satisfies `SentimentSource` like any other and the provider no longer carries source-specific math.

`get_ohlcv` / `coverage` / `get_ohlcv_with_status` stay as provider methods. They are not thin dispatch — they own the cache/gap/`as_of` anti-lookahead orchestration (ADR-0007), which legitimately belongs in the provider, not an adapter. The `OhlcvSource` Protocol covers only the raw fetch the orchestration delegates to.

We explicitly do **not** add an auto-discovery package-walk for adapters (the strategy `discover()` analogue), because adapters are stateful and must be constructed with their dependencies. Registration stays explicit in the composition root; the seam we are adding is the *typed contract* plus the *selector registry*, not auto-wiring.

## Consequences

### Positive
- A new data source has a shape to conform to: implement the capability Protocol, and the type checker enforces the contract before runtime.
- The two source-selector ladders become table lookups — adding a sentiment source or a market-sentiment market no longer edits a dispatch body; it adds a registry entry.
- `DefaultMarketDataProvider` sheds the inlined VADER aggregation and stops growing per-branch; it shrinks toward "OHLCV cache coordinator + thin capability facade."
- The provider now depends on capability abstractions, not concrete adapter classes — the dependency-inversion the `MarketDataProvider` Protocol gives consumers is mirrored on the producer side.

### Negative
- Seven small Protocols are more surface than zero. The cost is real but bounded — each is a 3-5 line structural type, and the alternative is the implicit "guess the method name" status quo.
- Adding a *brand-new capability* (a kind of data no existing Protocol covers) still touches several files: the new Protocol, the `MarketDataProvider` method, the provider wiring, and the return type. This ADR makes adding a new *source of an existing capability* cheap; it does not make adding a new *capability* cheap (nor should it — a new capability is a genuine contract change).
- Normalizing the one or two mismatched method names is a churn commit that touches the adapter and its tests for no behavior change.

### Neutral
- The registry dicts must be constructed deterministically (plain dict literals, no set iteration) to preserve the determinism contract — same as every other ordered structure in the data layer.

## Alternatives considered

### Alternative A — Single `DataAdapter` Protocol with a `capabilities()` descriptor + auto-discovery
Mirror `discover()` exactly: adapters declare an `ADAPTER` meta and a package-walk builds the provider's dispatch table. Rejected because adapters are stateful wired objects, not pure modules — auto-instantiation would have to invent HTTP-client/proxy/TTL wiring at walk time, which is more fragile than the explicit composition-root wiring it replaces, and the heterogeneous return types don't fit one method signature.

### Alternative B — Minimal: extract only the `get_sentiment` ladder into a registry, leave everything else
The lowest-effort fix — it removes the one ladder that has already grown — but it leaves the core complaint (no producer contract; every adapter still a guess-the-method-name shape) unaddressed, and the `get_market_sentiment` ladder would re-grow the same smell on the next market. Rejected as treating the symptom, not the asymmetry.

### Alternative C — Leave as-is (status quo)
The consumer Protocol is already clean and the friction is "only" 4-5 files per source. Rejected because adding data sources is a first-class, recurring contributor activity for this app, and the friction compounds: each new source widens the god-aggregator and, for sentiment/market-sentiment, lengthens a dispatch ladder that should be a lookup.

## Notes

Pairs with [ADR-0032](0032-data-layer-no-api-dependency.md) (data must not depend on api), which Plan 0028 lands in the same data-layer-hygiene pass. Mirrors the producer/consumer split that [ADR-0004](0004-strategy-interface.md) established for strategies; the deliberate divergence (no auto-discovery) is the stateless-module vs stateful-object distinction.
