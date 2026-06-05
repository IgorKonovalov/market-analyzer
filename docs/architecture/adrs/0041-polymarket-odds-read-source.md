# ADR-0041 — Polymarket as a read-only prediction-market odds source

> **Status:** proposed — accepts at the Polymarket data plan close ([Plan 0040](../plans/0040-polymarket-odds-adapter.md))
> **Date:** 2026-06-05
> **Related plan(s):** [Plan 0040](../plans/0040-polymarket-odds-adapter.md) (Polymarket odds read adapter)
> **Related ADRs:** [ADR-0031](0031-data-source-adapter-contract.md) (the per-capability source contract this adds a capability to — does **not** supersede), [ADR-0009](0009-rewrite-data-layer-in-house.md) (in-house read adapters), [ADR-0019](0019-external-http-adapter-resilience.md) (the resilience module this inherits), [ADR-0015](0015-claude-code-primary-control-surface.md) ("conditions are facts" — odds are a fact, not a call), [ADR-0025](0025-trade-execution-feasibility.md) (where Polymarket *trading* belongs — not here)

## Context

The user asked to integrate Polymarket. A clarification shaped the decision: Polymarket is **not** a DeFi perp or lending protocol — it is a **binary-outcome prediction market** (a CLOB on Polygon, USDC-settled, EIP-712-signed orders) where each outcome trades between 0 and 1 and the **price is the market-implied probability directly**. It can serve two unrelated purposes, and the user chose to split them: a **read-only odds/data signal now**, and a **trading venue later**.

This ADR governs only the read-only signal. A 2026-06-05 research pass (adversarially verified) established the facts that make it cheap and charter-safe: Polymarket's market-data read APIs (the Gamma API at `gamma-api.polymarket.com` and the CLOB public price/order-book endpoints) **require no authentication**, and an outcome's price *is* its implied probability — no derivation needed. So a read adapter holds no key, signs nothing, and moves no funds.

Two facts the research surfaced must be recorded so a future maintainer doesn't trip on them: (1) the older `py-clob-client` library is **archived** — Polymarket migrated to a new unified `Polymarket/py-sdk`, which is what any *future trading* work must target; and (2) a **December 2025 CFTC approval let Polymarket re-enter the US market**, so the access/geo posture differs from prior years (relevant mainly to the future trading venue; public reads are unaffected).

The remaining question is *shape*: does prediction-market odds fit the existing data-source contract, or does it need its own treatment? [ADR-0031](0031-data-source-adapter-contract.md) established per-capability source Protocols + a selector-registry so that adding a source for an *existing* capability (e.g. another sentiment feed) is one registry entry. Prediction-market odds is a **new capability** with new semantics (an event-outcome probability, not OHLCV, not NLP sentiment), so it needs a new Protocol — more than a registry entry, but squarely within ADR-0031's framework.

## Decision

We will add Polymarket as a **read-only prediction-market odds source** under the [ADR-0031](0031-data-source-adapter-contract.md) contract: a new `PredictionMarketSource` per-capability Protocol and a `PolymarketOddsAdapter` that reads public Gamma + CLOB endpoints (no auth, no key, no signing, no funds), hardened by the [ADR-0019](0019-external-http-adapter-resilience.md) resilience module. The odds it surfaces are **conditions/facts** — a market-implied probability is information the analyst and forecaster may consume and the advisor may weigh, exactly like sentiment or a regime classification; it is never a recommendation, so it stays on the [ADR-0015](0015-claude-code-primary-control-surface.md) read side. **Polymarket trading is explicitly out of scope of this ADR** — it is a separate, deferred decision belonging to the execution pillar ([ADR-0025](0025-trade-execution-feasibility.md)), and if taken it must target the maintained `py-sdk`, not the archived `py-clob-client`.

## Consequences

### Positive
- **A genuinely new signal class** — market-implied event probabilities — feeds the forecaster (as a causal feature) and the advisor (as a conviction input), at the cost of one read adapter.
- **Charter-safe and key-free.** Public reads mean no secret, no signing, no funds — none of [ADR-0025](0025-trade-execution-feasibility.md)'s execution machinery is touched. The plan is file-disjoint from the other pillars and can run in parallel.
- **Fits the existing extensibility seam.** Adding the capability is a new Protocol + adapter under [ADR-0031](0031-data-source-adapter-contract.md), not a special case — the next prediction-market source (if any) is then a registry entry.

### Negative
- **Prediction-market odds are noisy and often illiquid**, especially on long-tail markets — a thin book's "probability" can be stale or manipulated. Consuming it as a *feature* is fine; presenting it as ground truth would be misleading. The honest-uncertainty discipline that governs forecasts applies here too.
- **Historical odds time-series (for backtest features) is not guaranteed** by the public read endpoints the way live odds are. Using odds as a forecasting feature over history may require a historical-odds source that this plan does not build — a real limitation flagged as an open question, not silently assumed.
- **The venue carries a regulatory/geo dimension** (the Dec-2025 US re-entry) that, while irrelevant to public reads, will matter the moment trading is considered — a standing item the execution-pillar decision must revisit.

### Neutral
- `proposed` until Plan 0040 closes; accepts at that close, when the charter-safe/read-only framing becomes the plan's acceptance criteria — same cadence as the other plan-paired ADRs.
- Does not supersede or amend [ADR-0031](0031-data-source-adapter-contract.md); it adds a capability under it.

## Alternatives considered

### Alternative A — Fold Polymarket odds into the existing sentiment capability
Treat implied probability as another "sentiment" reading. **Rejected** because the semantics differ: sentiment is an NLP-derived mood score over text; a prediction-market price is a money-weighted probability of a discrete event. Forcing them into one capability would muddy both the Protocol's meaning and any consumer's interpretation. A distinct capability keeps each honest.

### Alternative B — Skip the Protocol; write a one-off adapter
Just fetch Polymarket directly where needed. **Rejected** because it violates [ADR-0031](0031-data-source-adapter-contract.md)'s whole point — the per-capability Protocol + registry is what keeps sources swappable and additions cheap. A one-off adapter is the 4-5-file-edit anti-pattern ADR-0031 exists to prevent.

### Alternative C — Build the trading venue now (read + write together)
Integrate odds and order placement in one go. **Rejected** because the user explicitly chose data-now/trade-later, and trading crosses every line [ADR-0025](0025-trade-execution-feasibility.md) maps (hot-wallet key, EIP-712 signing, USDC on Polygon, order state machine) — none of which a read adapter needs. Binding them would force execution's heavy invariants onto a charter-safe data feature.

## Notes
- **Causality for forecasting use:** an odds reading taken at bar `i` reflects only information available at `i`, so consuming it as a feature respects the no-lookahead grain. The risk is *availability* of historical odds, not lookahead.
- **No secrets:** public reads only. The `py-sdk`/key/signing concerns belong to the future trading decision under [ADR-0025](0025-trade-execution-feasibility.md), recorded here only so they aren't rediscovered.
