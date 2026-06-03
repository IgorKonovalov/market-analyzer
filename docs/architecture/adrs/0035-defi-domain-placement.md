# ADR-0035 — DeFi domain placement: a `defi/` package, on-chain fetch as ADR-0031 sources

> **Status:** proposed — accepts at the first DeFi plan close (the wallet-discovery plan, forthcoming in the 0032+ series — see [plans README](../plans/README.md))
> **Date:** 2026-06-03
> **Related plan(s):** forthcoming DeFi series (wallet discovery → deep adapters → P&L → risk → UI)
> **Related ADRs:** [ADR-0031](0031-data-source-adapter-contract.md) (the per-capability source contract this reuses), [ADR-0032](0032-data-layer-no-api-dependency.md) (the layering rule this obeys), [ADR-0007](0007-market-data-provider.md) (the TradFi provider this deliberately does **not** overload), [ADR-0009](0009-rewrite-data-layer-in-house.md) (in-house data-layer policy), [ADR-0019](0019-external-http-adapter-resilience.md) (the resilience client on-chain adapters inherit), [ADR-0006](0006-persistence-layout.md) (SQLite cache the immutable-tx-history cache lands in)

## Context

The app is gaining a second analysis domain. Today everything is TradFi: `data/` fetches OHLCV / quotes / screener / sentiment, `analysis/` computes indicators and patterns, `backtest/` runs strategies — all under the single `src/market_analyser/` import root. The new DeFi capability (paste a wallet → discover positions across EVM chains → reconstruct P&L → model risk) is a parallel domain with its own data sources (a portfolio aggregator, RPC nodes, The Graph, historical-price feeds), its own domain objects (positions, lots, lending health), and its own math (cost-basis replay, impermanent loss, liquidation distance). None of that exists in code yet.

The `defi-analyst` skill's frontmatter claims it owns `src/defi_analyser/` — a **separate top-level package**, parallel to `src/market_analyser/`. That claim predates any code and is the drift this ADR resolves. A second top-level import root is a real structural commitment: it would duplicate the composition root, the `ResilientHttpClient` wiring ([ADR-0019](0019-external-http-adapter-resilience.md)), the SQLite/Alembic setup ([ADR-0006](0006-persistence-layout.md)), and the layer-neutral `events/` bus ([ADR-0032](0032-data-layer-no-api-dependency.md)) — or force awkward cross-root imports between two packages that are really two domains of *one* application.

Two contracts constrain where the pieces land. First, [ADR-0031](0031-data-source-adapter-contract.md): a data source is a stateful adapter implementing a narrow per-capability `Protocol` in `data/sources.py`, wired explicitly in the composition root and dispatched through a selector registry — *not* an uber-interface, *not* auto-discovered. On-chain fetchers are data sources and should follow this. Second, [ADR-0032](0032-data-layer-no-api-dependency.md): nothing in `data/` (or any downstream domain) may import `api/`; progress events go through the neutral `events/` core. A long-running wallet scan that streams progress over SSE must honor that arrow.

The decision is genuinely two-sided because the `MarketDataProvider` Protocol ([ADR-0007](0007-market-data-provider.md)) is *right there* and tempting to extend — but it is OHLCV/quote/screener/sentiment-shaped, and wallet-position discovery, on-chain state reads, tx-history pulls, and historical-price lookups do not share that shape. Overloading it would rebuild exactly the god-aggregator ADR-0031 just dismantled.

## Decision

We will place the DeFi domain as a **cohesive package `src/market_analyser/defi/`**, a sibling to `data/` / `analysis/` / `backtest/` under the existing single import root — **not** a separate `src/defi_analyser/` top-level package. The `defi-analyst` skill's `src/defi_analyser/` reference is reconciled to `src/market_analyser/defi/` (a skill-frontmatter fix, not a code move, since no code exists yet).

The split between *fetch* and *domain logic* follows the existing layering:

> **On-chain fetch is source-adapter-shaped ([ADR-0031](0031-data-source-adapter-contract.md)).** New per-capability Protocols land in `data/sources.py` — e.g. `WalletPositionsSource` (address → discovered positions), `OnchainStateSource` (live protocol state: Aave health factor, Uni v3 fee accrual, tick-range status), `TxHistorySource` (decoded wallet events), `HistoricalPriceSource` (token USD price at a past block/timestamp). Their adapters live under `data/adapters/`, inherit `ResilientHttpClient` ([ADR-0019](0019-external-http-adapter-resilience.md)), and are wired explicitly in the composition root with a selector registry, exactly as the TradFi sources are.
>
> **DeFi domain logic lives in `src/market_analyser/defi/`** — the position model, the cost-basis/P&L engine ([ADR-0036](0036-defi-pnl-reconstruction.md)), the risk/forecast engine ([ADR-0037](0037-defi-position-risk-forecast.md)), and a thin DeFi access facade that composes the DeFi source Protocols. This domain depends on `data/` (its sources), `persistence/`, and `events/` — never on `api/` ([ADR-0032](0032-data-layer-no-api-dependency.md)).

We explicitly **do not** add DeFi methods to `MarketDataProvider`. That Protocol stays TradFi/OHLCV-centric; the DeFi capabilities are their own narrow Protocols, honoring ADR-0031's "one narrow Protocol per operation kind, no uber-interface." The `api/` layer grows DeFi routes + MCP tools that call *down* into `defi/`; both the renderer paste-box and the agent reach the same endpoints ([ADR-0015](0015-claude-code-primary-control-surface.md) reconciliation: UI for the at-a-glance dashboard, agent for narrative deep-dives).

## Consequences

### Positive
- One import root, one composition root, one resilience client, one event bus, one SQLite setup — the DeFi domain reuses all of it instead of standing up a parallel copy. `defi/` is independently testable the same way `data/` is.
- The fetch/domain split mirrors the proven TradFi shape (`data/` fetches, `analysis/`+`backtest/` compute), so the layering rules (`no data→api`, per-capability Protocols) apply unchanged — a contributor who knows the TradFi side already knows where DeFi pieces go.
- On-chain sources slot into the ADR-0031 registry: adding the next protocol's state reader is "implement the Protocol, add one registry entry," not a new subsystem.
- Immutable tx history caches cleanly in the existing SQLite layer ([ADR-0006](0006-persistence-layout.md)) — a re-scan re-reads cached decoded events instead of re-pulling, which is what makes the P&L replay ([ADR-0036](0036-defi-pnl-reconstruction.md)) affordable.

### Negative
- `MarketDataProvider` and the DeFi access facade are now **two** provider-shaped seams in the codebase. That is deliberate (their capabilities don't overlap), but it is a second thing to learn and a second place "where do I get data" can be answered. The mitigation is that both follow the identical ADR-0031 Protocol+registry idiom, so the *pattern* is one, even if the instances are two.
- `src/market_analyser/` now spans two financial domains under a TradFi-flavored package name. The name is now a mild misnomer (the package is "the app," not "the equities analyzer"). Renaming the root is out of scope and not worth the churn; we accept the naming smell.
- The DeFi domain is a broad new surface (sources + domain + persistence schema + routes + UI) landing across several plans. This ADR only fixes *where* it lives; the heavy decisions (P&L method, risk posture, aggregator choice) are their own ADRs and the build is its own plan series.

### Neutral
- The `defi-analyst` skill is read-only and consumes this code; it does not author it (new code goes architect → dev, per the skill's own charter). Reconciling its `src/defi_analyser/` path reference to `src/market_analyser/defi/` is a one-line frontmatter edit, sequenced whenever the first DeFi plan lands.
- Whether DeFi positions ever persist to SQLite vs. stay request-scoped is deferred to the discovery plan; this ADR only reserves the cache *location* (the existing persistence layer), not the schema.

## Alternatives considered

### Alternative A — Separate top-level `src/defi_analyser/` package (as the skill frontmatter claims)
A second import root parallel to `src/market_analyser/`. **Rejected:** it duplicates the composition root, resilience client, persistence setup, and event bus — or forces cross-root imports between two packages that are two domains of a single app, not two apps. The TradFi/DeFi divide is a domain boundary *inside* the application; a package boundary that heavy is the wrong tool for it. The skill's reference is corrected to match, not the other way around.

### Alternative B — Extend `MarketDataProvider` with wallet/position/on-chain methods
Reuse the existing provider seam for DeFi fetches. **Rejected:** wallet-position discovery, on-chain state, tx history, and historical pricing do not share the OHLCV/quote/screener/sentiment shape; bolting them on rebuilds the exact god-aggregator [ADR-0031](0031-data-source-adapter-contract.md) just decomposed into narrow Protocols. Narrow DeFi-specific Protocols are the consistent choice.

### Alternative C — Scatter DeFi across existing packages (positions in `data/`, P&L in `backtest/`, risk in `analysis/`)
Place each DeFi concern in the nearest-looking TradFi package. **Rejected:** it gives the domain no cohesive home, couples DeFi math into TradFi modules that don't share its objects, and directly violates the project's "don't ad-hoc shared infrastructure into a single skill / avoid god-modules" guidance. P&L replay is not a backtest; impermanent loss is not a candlestick indicator.

## Notes
- **Authenticated-source prerequisite.** Every DeFi source needs a credential (aggregator API key, RPC endpoint, `GRAPH_API_KEY`). The "third-party data-source API keys" decision (secrets schema, rotation, Settings UI) is an **open backlog item** — all TradFi Tier-2 sources to date are keyless, so the app has never needed it. The first DeFi plan must resolve that secrets-handling decision before any authenticated adapter ships; it intersects [ADR-0011](0011-bearer-secret-transport.md)'s "no secret in argv/logs" discipline and may warrant its own small ADR.
- Pairs with [ADR-0036](0036-defi-pnl-reconstruction.md) (what the domain computes) and [ADR-0037](0037-defi-position-risk-forecast.md) (the risk/forecast posture). The aggregator-selection decision is [ADR-0034](0034-defi-portfolio-aggregator.md).
