# Spec — Market data provider

> **Subsystem:** The `MarketDataProvider` Protocol — the only data-layer contract downstream code (strategies, backtests, analysis, MCP tools) imports, and the `as_of` anti-lookahead seam it carries.
> **Source:** src/market_analyser/data/ (`provider.py`, `default_provider.py`, `sources.py`, `types.py`, adapters under `data/`)
> **Reconciled-through:** Plan 0112
> **Governing ADRs:** 0007-market-data-provider, 0009-rewrite-data-layer-in-house, 0031-data-source-adapter-contract, 0032-data-layer-no-api-dependency

The data provider is the swappability seam of the whole app: strategy, backtest,
and analysis code depend on the `MarketDataProvider` Protocol, never on a concrete
source (Yahoo, Binance, Coinbase, TradingView, …). The load-bearing behavioral
guarantee is the `as_of` parameter — the point where lookahead bias is prevented
at the data boundary rather than in every consumer.

## Invariants

- **One Protocol, no concrete-source imports downstream.** Downstream code
  (`strategies/`, `backtest/`, `analysis/`, `advisor/`, MCP tools) MUST depend only
  on the `MarketDataProvider` Protocol, never on a concrete adapter class. This is
  what lets a source be swapped or added without touching a single consumer.
  (ADR-0007; `data/provider.py:MarketDataProvider`)

- **Every method carries an `as_of: datetime | None` seam.** Every provider method
  MUST accept `as_of`. Live-mode callers pass `None`; backtest / historical callers
  pass a fixed datetime. When `as_of` is set, the implementation MUST NOT return any
  data timestamped after `as_of` — the anti-lookahead guarantee is declared at the
  Protocol level and enforced by every adapter, not re-implemented per consumer.
  (ADR-0007; `data/provider.py`)

- **No lookahead at read.** A read with `as_of = t` MUST behave as if the current
  moment were `t`: no bar, quote, news item, sentiment sample, or screener row from
  after `t` is observable. A decision made at bar `i` therefore cannot see data from
  `> i` via the provider. This mirrors the backtest engine's execution-timing rule
  ([backtest-engine spec](backtest-engine.md)) at the data boundary.  (ADR-0007; CLAUDE.md no-lookahead rule)

- **Adapters degrade honestly, never fabricate.** WHEN a source is unavailable,
  rate-limited, blocked, or returns nothing, the adapter MUST surface that as a typed
  error or an honest empty/`None` result — never a silently-synthesized value. A
  missing key, a 402/403, or an upstream gap is a real, visible degrade, not a
  fallback that invents data.  (ADR-0031; ADR-0032)

- **No implicit network dependency in the contract.** The Protocol MUST NOT bake in
  any single source's availability; the data layer is written in-house (ADR-0009,
  superseding the vendoring policy) and evolves directly. Sources are selected/routed
  behind the Protocol, so the app never hard-depends on one upstream being reachable.
  (ADR-0009; ADR-0032; `data/sources.py`, `data/default_provider.py`)

- **Validate at the boundary, trust within.** Data crossing into the app from an
  external feed MUST be validated (via `data/types.py` models — `Bar`, `Quote`,
  `NewsItem`, `SentimentSample`, `ScreenerRow`, …) at the adapter boundary: bad
  timestamps, `None`/`NaN`/inf prices, and impossible values are rejected there, so
  code downstream of the validator can assume sane values.  (ADR-0031; `data/types.py`)

## Scenarios

- WHEN a backtest calls `get_ohlcv(symbol, tf, start, end, as_of=t)` THEN the
  returned bars all have `event_ts ≤ t`, even if the cache holds newer bars.  (ADR-0007)

- WHEN a live analysis calls `get_ohlcv(..., as_of=None)` THEN the provider returns
  the most recent bars available, fetching on a cache miss.  (`data/default_provider.py`)

- WHEN a consumer imports the data layer THEN it imports the `MarketDataProvider`
  Protocol type, not `YahooSource` / `BinanceSource` / `CoinbaseSource` — a
  cross-import of a concrete adapter into strategy/analysis/backtest code is a
  layering violation and a review finding.  (ADR-0007)

- WHEN a sentiment source is key-gated and no key is configured (e.g. LunarCrush at
  the free tier) THEN `get_sentiment` degrades to an honest empty/inert sample, not
  a fabricated score.  (ADR-0031; ADR-0032)

- WHEN an adapter receives a bar with a `NaN` close or a non-UTC / malformed
  timestamp THEN construction of the `Bar` model rejects it at the boundary, rather
  than letting the bad value flow into indicator or backtest math.  (`data/types.py`)

## Known gaps / honest nulls

- **`as_of` correctness is per-adapter, not centrally enforced.** The Protocol
  *declares* the seam; each adapter must honor it. An adapter that ignores `as_of`
  (e.g. a range-only upstream that can only fetch now-relative windows) is a real
  gap — the Yahoo adapter's relative-`range=`-only limitation is a documented example
  that blocks past-ending-window reads. Honoring `as_of` is an adapter obligation the
  Protocol cannot mechanically guarantee.

- **Some methods are still stubs.** `get_quote` and `search_symbols` are declared on
  the Protocol but earned by later plans; calling an unimplemented method is a typed
  failure, not a silent empty. The Protocol surface is wider than the shipped
  coverage.  (`data/provider.py` readiness notes)

- **Source selection/routing is not part of the anti-lookahead guarantee.** Which
  concrete source answers a call (Yahoo vs Binance vs Coinbase) is a routing concern;
  `as_of` constrains *what timestamps* are visible, not *which upstream* served them.
