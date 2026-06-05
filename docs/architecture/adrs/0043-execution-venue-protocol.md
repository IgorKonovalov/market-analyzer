# ADR-0043 — ExecutionVenue Protocol + persisted order/position state machine

> **Status:** proposed — accepts at the execution skeleton plan close ([Plan 0044](../plans/0044-execution-skeleton.md))
> **Date:** 2026-06-05
> **Related plan(s):** [Plan 0044](../plans/0044-execution-skeleton.md) (the venue-independent skeleton), [Plan 0045](../plans/0045-binance-futures-testnet-adapter.md) (first adapter), [Plan 0046](../plans/0046-pending-order-confirm-ux.md) (assisted-confirm UX)
> **Related ADRs:** [ADR-0025](0025-trade-execution-feasibility.md) (the six invariants this Protocol serves — ADR-0025 Notes explicitly call for this ADR), [ADR-0007](0007-market-data-provider.md) (the read-side Protocol this mirrors), [ADR-0006](0006-persistence-layout.md) (where order/position state persists), [ADR-0044](0044-trade-secret-store.md) (where the venue's credentials come from), [ADR-0029](0029-advisory-recommendation-boundary.md) (the advisor whose Recommendation feeds a prepared order)

## Context

[ADR-0025](0025-trade-execution-feasibility.md) recorded the posture for trade execution and its **six invariants** (assisted-first, testnet-first, isolated execution domain, segregated secrets, idempotency + reconciliation, risk guard + kill switch), and named the "build-once" surface: a venue-independent machinery — order/position state machine, reconciliation, idempotency, guard — sitting behind an **`ExecutionVenue` Protocol mirroring [ADR-0007](0007-market-data-provider.md)'s `MarketDataProvider`**. ADR-0025's Notes explicitly defer "the `ExecutionVenue` Protocol shape and one for the secret-store mechanism" to dedicated ADRs. This is the first of those two.

The user chose (2026-06-05) **assisted execution, testnet/paper only** — so this Protocol is exercised first against Binance USDⓈ-M Futures **testnet**, with no real funds, and submission always requires explicit human confirmation. That does not lower the correctness bar: even on testnet, an order layer that double-submits on a dropped connection, loses track of a fill, or can't be halted is the wrong shape to graduate to real funds. Three disciplines the repo has never needed become structural here: **idempotency** (a retry must never create a second order), **reconciliation** (on reconnect, the venue's truth is authoritative over local state), and an **explicit, persisted order lifecycle** (so state survives a restart and is auditable).

## Decision

We will define an **`ExecutionVenue` Protocol** (place order, cancel, query, stream fills/positions, reconcile) in an isolated `src/market_analyser/execution/` package, with a **persisted order/position state machine** as the venue-independent core. The order lifecycle is an explicit finite-state machine — `intended → submitted → acknowledged → (partially_)filled → closed | canceled | rejected` — persisted in SQLite ([ADR-0006](0006-persistence-layout.md)). Every order carries a **client-generated `clientOrderId`**; a retry after a dropped connection re-sends the *same* id and the venue (or local reconciliation) rejects the duplicate, so a retry never double-submits. On reconnect, the adapter **reconciles** the venue's order/position truth against local state, with the venue authoritative. The Protocol abstracts the venue so the state machine, idempotency, and reconciliation are built **once** (the "build-once" surface), and only the adapter changes per venue. The execution package imports **no analyst-internal modules** ([ADR-0025](0025-trade-execution-feasibility.md) invariant 3); it consumes the advisor's `Recommendation` ([ADR-0029](0029-advisory-recommendation-boundary.md)) as an *input* to a prepared order, never the analysts' internals.

## Consequences

### Positive
- **Swappability preserved at the write boundary**, exactly as [ADR-0007](0007-market-data-provider.md) preserves it at the read boundary — a second venue (a DeFi perp, Polymarket trading) is a new adapter, not a rewrite.
- **The hard correctness disciplines are structural, not aspirational.** Idempotency and reconciliation live in the venue-independent core and are tested against a stub venue before any real adapter exists — so they are proven once, for all venues.
- **The order lifecycle is explicit and persisted**, so state survives restarts and a dropped stream, and every transition is auditable — the precondition for ever trusting this with real funds.
- **Clean seam to the advisor and the confirm UX.** The advisor's `Recommendation` feeds a *prepared* order ([ADR-0029](0029-advisory-recommendation-boundary.md)'s anticipated handoff); the assisted-confirm step ([Plan 0046](../plans/0046-pending-order-confirm-ux.md)) gates submission — composing exactly as [ADR-0025](0025-trade-execution-feasibility.md) invariant 1 intends.

### Negative
- **This is genuinely hard, stateful, real-money-adjacent code** — the most correctness-sensitive in the repo. Testnet-first and assisted-first bound the blast radius, but the state machine + reconciliation are a standing maintenance and review burden, not a one-time build.
- **A new persistence surface** (order/position tables) means migrations — and this plan therefore **serializes with any other migration-touching plan** (the single-Alembic-chain rule), not worktree-parallel.
- **Reconciliation is subtle**: partial fills, races between a local cancel and a venue fill, and stream gaps are the failure modes that lose money in production. They must be tested against adversarial stub-venue scenarios, and even then carry residual risk that only real (testnet) traffic exposes.
- **The pressure to drop the confirm step** ("just auto-submit") is foreseeable; this Protocol deliberately keeps submission gated by [Plan 0046](../plans/0046-pending-order-confirm-ux.md)'s human-confirm step. Autonomous mode is a separate, later ADR ([ADR-0025](0025-trade-execution-feasibility.md) invariant 1).

### Neutral
- `proposed` until [Plan 0044](../plans/0044-execution-skeleton.md) closes; the full intent→submit→fill→reconcile→close loop running green on testnet is what graduates the broader [ADR-0025](0025-trade-execution-feasibility.md) toward `accepted` ([Plan 0046](../plans/0046-pending-order-confirm-ux.md) close).
- Determinism (the repo-wide non-negotiable for backtests/forecasts) does **not** apply to live order execution — wall-clock and venue responses are inherently non-reproducible; the analogue here is **auditability** (an append-only log), not byte-identical replay.

## Alternatives considered

### Alternative A — No Protocol; a Binance-specific order layer
Build straight against Binance. **Rejected** because it discards [ADR-0025](0025-trade-execution-feasibility.md) invariant 3's swappability — the whole point of the "build-once" surface is that the state machine/reconciliation/guard are venue-independent and a perp DEX or Polymarket is a later adapter, not a fork. A Binance-shaped order layer would have to be rewritten to add a second venue.

### Alternative B — Reuse the `MarketDataProvider` Protocol
Extend the read Protocol with write methods. **Rejected** because reading bars and placing orders are categorically different (one is idempotent and stateless, the other is stateful, idempotency-critical, and money-moving); overloading [ADR-0007](0007-market-data-provider.md) would couple the read and write trust boundaries that [ADR-0025](0025-trade-execution-feasibility.md) invariant 3 deliberately isolates.

### Alternative C — In-memory order state
Hold order/position state in memory only. **Rejected** because state must survive a sidecar restart and a dropped stream, and reconciliation needs a persisted local truth to reconcile *against*. In-memory state loses the audit trail and cannot recover after a crash mid-order — unacceptable for anything that graduates to real funds.

## Notes
- **Testnet-first is load-bearing:** the first adapter ([Plan 0045](../plans/0045-binance-futures-testnet-adapter.md)) targets the Binance USDⓈ-M Futures **testnet** (`testnet.binancefuture.com` — verified at build; a 2026-06-05 research pass flagged that the *spot* testnet `testnet.binance.vision` is a different host and must not be used for futures).
- **No secret in this ADR:** credentials are injected from the trade-secret store ([ADR-0044](0044-trade-secret-store.md)); the Protocol never holds or logs a key.
- The `intended` state is where the advisor's `Recommendation` and the human-confirm step ([Plan 0046](../plans/0046-pending-order-confirm-ux.md)) attach — an order is `intended` until a human confirms it into `submitted`.
