# ADR-0034 — DeFi portfolio aggregator: Zerion for discovery + tx history, behind a swappable source seam

> **Status:** proposed — accepts at the first DeFi plan close (the wallet-discovery plan, forthcoming in the 0032+ series — see [plans README](../plans/README.md))
> **Date:** 2026-06-03
> **Related plan(s):** forthcoming DeFi series (wallet discovery is the first)
> **Related ADRs:** [ADR-0035](0035-defi-domain-placement.md) (where this source plugs in), [ADR-0031](0031-data-source-adapter-contract.md) (the Protocol seam that makes the provider swappable), [ADR-0009](0009-rewrite-data-layer-in-house.md) (the in-house policy this reconciles against), [ADR-0019](0019-external-http-adapter-resilience.md) (the resilience client the adapter inherits), [ADR-0012](0012-dependency-cooldown.md) / [ADR-0013](0013-pin-direct-dependencies.md) (dep discipline), [ADR-0011](0011-bearer-secret-transport.md) (the secret-handling discipline the API key falls under), [ADR-0036](0036-defi-pnl-reconstruction.md) (the consumer of this source's tx history)

## Context

The wallet-analysis feature must, from a pasted public address, **discover every DeFi position across Ethereum / Base / Arbitrum / Optimism** — decoded into structured positions (Aave v3 supply/borrow, Uniswap v3 LP with tick range, Aerodrome LP), not raw token balances. Doing this in-house means indexing and decoding thousands of protocol contracts across four chains and tracking them as they upgrade — exactly what DeBank / Zerion / Zapper do with large proprietary indexing infrastructure. Building generic cross-protocol discovery ourselves is infeasible at quality; this was the central conclusion of the design conversation, and it is why the chosen model is **hybrid** (aggregator for breadth/discovery + our own RPC/subgraph adapters for depth on priority protocols).

This sits against [ADR-0009](0009-rewrite-data-layer-in-house.md), which rewrote the data layer in-house "so we control its evolution and backtests are reproducible." The reconciliation matters and is not a fudge: ADR-0009 forbids *mirroring an upstream codebase as a vendored subtree* — it does not forbid depending on external data *services*. The data layer already wraps Yahoo, TradingView, alternative.me, and RSS feeds; an aggregator is one more external service. What ADR-0009's reproducibility clause demands is that the **financially-meaningful** computation stays ours — and it does: we reconstruct P&L ourselves from the aggregator's decoded events ([ADR-0036](0036-defi-pnl-reconstruction.md)) rather than trusting the aggregator's P&L number, and we wrap the aggregator behind our own Protocol so the dependency is contained at one swappable seam.

A market scan (early 2026) compared DeBank Cloud, Zerion, Covalent/GoldRush, Moralis, Alchemy, Zapper, Bitquery, 1inch. The findings that drove the decision: **Zerion** returns interpreted positions across Aave/Uniswap/**Aerodrome** on all four target chains, decoded human-readable tx history, and a built-in PnL endpoint, on a **free Developer tier** sufficient for one user. **DeBank** has the deepest coverage and a true timestamp-historical-price endpoint but **no free tier** (prepaid compute units) and 20-item-per-page history pagination. **Moralis** does not interpret Aerodrome (Base LP degrades to raw balances) — disqualifying for our protocol set. **Alchemy** returns raw balances only with no DeFi-position interpretation — disqualifying for discovery, though its free, block-precise Prices API is a strong *price* source. (Confidence note: the comparison agent could not load vendor pages directly; tier limits, prices, and ToS clauses below are snippet-sourced and must be re-verified against the live pages before the plan commits.)

## Decision

We will use **Zerion's REST API as the primary DeFi data source for position discovery and decoded transaction history**, accessed through the data layer's `ResilientHttpClient` ([ADR-0019](0019-external-http-adapter-resilience.md)) — **no vendor SDK** is pinned (we call the REST endpoints directly, as the Yahoo/TradingView adapters do), so the dependency is an API key, not a package under the cooldown/pin policy.

> The Zerion adapter implements the per-capability source Protocols from [ADR-0035](0035-defi-domain-placement.md) — `WalletPositionsSource` (discovery) and `TxHistorySource` (decoded events) — registered explicitly in the composition root behind the [ADR-0031](0031-data-source-adapter-contract.md) selector registry. **The provider is chosen behind that seam precisely so it is swappable**: if Zerion's interpretation proves insufficient, DeBank is the documented upgrade path with no change above the Protocol.

Two roles are deliberately **not** Zerion's:
- **Historical pricing** is a *separate* source. Zerion exposes price *charts* (interpolate-from-series), not clean price-at-block-timestamp lookups, which [ADR-0036](0036-defi-pnl-reconstruction.md)'s per-event valuation needs. We use a dedicated `HistoricalPriceSource` — **DefiLlama's keyless historical-price endpoint** as primary (keyless fits the project's preference and adds no secret), with a keyed block-precise source (Alchemy Prices) available as a fallback if coverage gaps appear.
- **Deep on-chain state** (exact Aave health factor, Uniswap v3 uncollected fees, live tick-range status) comes from our own RPC + The Graph adapters — the *depth* half of the hybrid, decided and built in their own plan. Zerion supplies breadth and discovery; precise risk-grade numbers come from contracts directly.

We will **not** consume Zerion's built-in PnL endpoint as the source of truth for profitability — the user explicitly chose reconstructed-from-history P&L for auditability and reproducibility ([ADR-0036](0036-defi-pnl-reconstruction.md)). Zerion's PnL (FIFO) is retained only as an optional *cross-check* signal to flag gross reconstruction errors.

## Consequences

### Positive
- All-chain, all-protocol discovery on day one without building or maintaining a cross-protocol indexer — the one piece that is genuinely infeasible in-house.
- The free Developer tier means the feature can be built and validated against a real wallet at zero cost before any spend commitment.
- The [ADR-0031](0031-data-source-adapter-contract.md) Protocol seam contains the dependency to one adapter; swapping to DeBank (or adding it as a second source) is a registry entry, not a domain rewrite.
- Splitting pricing and deep-state off Zerion keeps each source doing what it's best at and keeps the reproducibility-critical paths (per-event valuation, risk-grade state) on sources we control or can snapshot.

### Negative
- **We now depend on an external interpreter for a core capability.** If Zerion misclassifies or drops a protocol, positions silently vanish or distort — and we cannot fix it, only detect it and switch providers. This is the ADR-0009 tension made concrete: discovery is *not* reproducible from our own code the way OHLCV is. Mitigation is the swappable seam and reconstructing P&L ourselves, but the discovery layer's correctness is outside our repository.
- **Free-tier cliff.** The free Developer tier has a request cap and (per snippets) requires a card on file; the only paid step-up found is **$499/mo** with no cheap middle tier. A single user should stay within free limits, but a chatty UI (auto-refresh, re-scan-on-focus) could blow the cap, and there is no graceful paid intermediate. The scan cadence must be deliberate, not reactive.
- **An authenticated source forces the deferred secrets decision.** Every DeFi source (Zerion key, RPC endpoints, `GRAPH_API_KEY`, optional Alchemy key) needs a credential, and the app's "third-party data-source API keys" decision is still open backlog (all TradFi sources are keyless). The first DeFi plan must resolve secret storage/rotation/Settings-UI under [ADR-0011](0011-bearer-secret-transport.md)'s no-secret-in-argv/logs rule before this adapter ships.
- **Unverified commercial terms.** Pricing and ToS (redistribution / desktop-app clauses) are snippet-sourced; the live pages were not loaded. For a personal read-only tool nothing is expected to bite, but the plan must re-verify before committing.

### Neutral
- Multi-source by design: Zerion (discovery + tx) + DefiLlama (prices) + own RPC/Graph (deep state). More sources than a single-provider pick, but each is a thin adapter behind its own Protocol, and the split is what keeps the reproducibility-critical paths controllable.
- The Zerion adapter is keyed but SDK-free, so it adds no entry to the cooldown/pin surface — only a secret to manage.

## Alternatives considered

### Alternative A — DeBank Cloud as primary
Deepest protocol coverage and a true price-at-timestamp endpoint, so it hits all three needs most literally. **Rejected as the day-one pick** because it has no free tier (prepaid compute units, ~$0.0002/unit per snippets) — you must commit money before you can even evaluate interpretation quality — and its 20-item-per-page history pagination makes long-history reconstruction call-heavy against a paid meter. Retained as the **documented upgrade path** behind the Protocol seam if Zerion's interpretation proves insufficient.

### Alternative B — Covalent / GoldRush as primary
Cheapest viable option (100k credits/mo free, $10–$50 paid tiers) with solid decoded tx history and daily historical prices. **Rejected** because it does not return clean interpreted per-protocol position objects — you assemble Aave/Uni-v3/Aerodrome positions yourself from balances + decoded logs, which rebuilds in-house exactly the cross-protocol decoding the aggregator exists to avoid. Its block-level decoded logs remain a candidate *tx-history* source if Zerion's history proves too coarse.

### Alternative C — Build discovery fully in-house (RPC + subgraphs only, no aggregator)
The pure [ADR-0009](0009-rewrite-data-layer-in-house.md) reading: own everything. **Rejected** as infeasible at quality — "all positions on all major chains" means indexing thousands of protocols and chasing their upgrades indefinitely. In-house adapters are reserved for *depth* on the handful of protocols actually held (the hybrid's second half), where precision matters and the surface is bounded; breadth is the aggregator's job.

### Alternative D — Trust the aggregator's PnL endpoint instead of reconstructing
Zerion (and others) return a computed PnL, so skip the reconstruction engine entirely. **Rejected** — this is the option the user explicitly declined: aggregator PnL is opaque, non-reproducible, and varies by provider, failing the determinism/auditability posture. We reconstruct from decoded events ([ADR-0036](0036-defi-pnl-reconstruction.md)) and use the aggregator's number only as a sanity cross-check.

## Notes
- **Re-verify before the plan commits:** Zerion's exact free-tier request cap and current paid pricing; DefiLlama historical-price token coverage for the held assets; and each source's ToS for redistribution/desktop clauses. These were snippet-sourced (vendor pages not directly loadable during research).
- Pairs with [ADR-0035](0035-defi-domain-placement.md) (placement), [ADR-0036](0036-defi-pnl-reconstruction.md) (P&L method that consumes the decoded tx history), and [ADR-0037](0037-defi-position-risk-forecast.md) (risk/forecast). The deep-state RPC/subgraph adapters are a sibling decision settled in their build plan, not here.
