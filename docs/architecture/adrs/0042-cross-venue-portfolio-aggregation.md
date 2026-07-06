# ADR-0042 — Cross-venue portfolio aggregation: one read-only view, average-cost basis

> **Status:** accepted (2026-07-06, at [Plan 0041](../plans/done/0041-cross-venue-portfolio-aggregation.md)'s close)
> **Date:** 2026-06-05
> **Related plan(s):** [Plan 0041](../plans/0041-cross-venue-portfolio-aggregation.md) (aggregation), [Plan 0042](../plans/0042-defi-position-risk-forecast.md) (DeFi risk/forecast over these holdings), [Plan 0043](../plans/0043-portfolio-ui-surface.md) (UI)
> **Related ADRs:** [ADR-0036](0036-defi-pnl-reconstruction.md) (the average-cost basis this adopts venue-wide), [ADR-0031](0031-data-source-adapter-contract.md) (the Binance read adapter's contract), [ADR-0038](0038-third-party-api-key-storage.md) (where the Binance *read* key lives — not the trade keychain), [ADR-0034](0034-defi-portfolio-aggregator.md)/[ADR-0035](0035-defi-domain-placement.md) (the DeFi holdings leg), [ADR-0015](0015-claude-code-primary-control-surface.md) ("conditions are facts" — exposure/P&L report, never advise), [ADR-0029](0029-advisory-recommendation-boundary.md) (rebalance *advice* lives in the advisor, not here)

## Context

The user asked for portfolio management. Today the app can discover and value DeFi positions ([ADR-0034](0034-defi-portfolio-aggregator.md)) and reconstruct their P&L ([ADR-0036](0036-defi-pnl-reconstruction.md)), but it has **no unified view of holdings + cost-basis + P&L + exposure across venues** — and it holds **no TradFi or CEX holdings at all**. The user wants one view spanning **Binance (CEX crypto), DeFi (on-chain), and TradFi/other**.

Two facts make the *shape* a real decision. First, CLAUDE.md's **hard TradFi/DeFi skill split**: `market-analyst` never triggers for DeFi, `defi-analyst` never for TradFi — so **no existing operator skill can own a cross-venue view**. The user resolved this 2026-06-05: the portfolio is **tools-only — no operator skill** (the agent assembles the unified picture from venue-scoped tools, preserving the split). Second, a P&L number is only coherent if every venue uses the **same cost-basis method**; [ADR-0036](0036-defi-pnl-reconstruction.md) already chose **average-cost lots** for DeFi, so anything else would make the DeFi leg disagree with the rest. The user chose **average-cost venue-wide** (2026-06-05).

The holdings sources were also chosen (2026-06-05): **Binance read API** (crypto balances), the **existing DeFi discovery** ([ADR-0034](0034-defi-portfolio-aggregator.md)/[ADR-0035](0035-defi-domain-placement.md)), and a **manual positions file** (gitignored, like the `defi-analyst`'s `positions.yaml`) for equities/other. The Binance leg needs a **read-only** API key — a lower-value secret than a trade key, so it belongs in the existing [ADR-0038](0038-third-party-api-key-storage.md) third-party-key store, **not** the trade-permissioned keychain that Pillar 5 ([ADR-0044](0044-trade-secret-store.md)) builds.

## Decision

We will add a **cross-venue portfolio subsystem** in a new top-level `src/market_analyser/portfolio/` package that aggregates holdings from three sources — a **Binance read adapter** (under the [ADR-0031](0031-data-source-adapter-contract.md) contract, its read-only key in the [ADR-0038](0038-third-party-api-key-storage.md) store), the **existing DeFi discovery**, and a **manual positions file** — into a unified, boundary-validated holdings model with **average-cost basis** (reusing [ADR-0036](0036-defi-pnl-reconstruction.md)'s engine), and computes **unrealized P&L and exposure** by asset and by venue. It is **read-only and tools-only**: it reports holdings, cost-basis, P&L, and exposure as **facts** ([ADR-0015](0015-claude-code-primary-control-surface.md)); it has **no operator skill** and emits **no rebalance/exit/buy/sell** — that crossing is the advisor's ([ADR-0029](0029-advisory-recommendation-boundary.md)). Each source is swappable behind its capability seam; the package lives at the top level (not in `defi/`, which is DeFi-only, nor `analysis/`, which is TradFi-indicator-only) precisely because it is the one cross-domain consumer.

## Consequences

### Positive
- **The unified view the user asked for**, at the cost of one new package + one read adapter — DeFi and average-cost basis are reused, not rebuilt.
- **Charter-safe.** Read-only, tools-only, no recommendations — the TradFi/DeFi split is preserved (each venue still reported by its own seam) rather than relaxed.
- **Consistent P&L.** One cost-basis method (average-cost) across all venues means the DeFi leg agrees with the CEX/manual legs; no reconciliation mismatch by construction.
- **Lower-value secret stays lower-value.** The Binance *read* key uses the existing [ADR-0038](0038-third-party-api-key-storage.md) store; the high-value trade keychain ([ADR-0044](0044-trade-secret-store.md)) is reserved for Pillar 5 and not diluted.

### Negative
- **Three heterogeneous sources, three freshness/precision profiles.** Binance is live-API-fresh, DeFi is as-fresh-as-the-last-scan, and the manual file is **only as current as the user keeps it** — a stale manual file silently skews the total. The view must surface each source's as-of time, not present one blended "now."
- **Cross-venue USD valuation needs a common pricing reference**, and the three legs price differently (DeFi via DefiLlama per [ADR-0036](0036-defi-pnl-reconstruction.md), Binance via its own feed, equities via the existing OHLCV provider). Small disagreements are unavoidable; the subsystem must document which reference each leg uses rather than implying a single oracle.
- **A manual positions file is user-maintained state** — a new place for error (wrong cost basis, forgotten sale). It is the pragmatic Tier-4 choice, but it is not authoritative the way an API is.
- **Standing pressure toward "so should I rebalance?"** — the exact slide [ADR-0029](0029-advisory-recommendation-boundary.md) forbids the analyst side. Exposure facts invite advice; keeping this subsystem on the facts side is a discipline defended at review.

### Neutral
- `proposed` until [Plan 0041](../plans/0041-cross-venue-portfolio-aggregation.md) closes; accepts there, when read-only/tools-only/average-cost become its acceptance criteria.
- The DeFi **risk/forecast** engine ([ADR-0037](0037-defi-position-risk-forecast.md), [Plan 0042](../plans/0042-defi-position-risk-forecast.md)) consumes these holdings but is a distinct decision (already its own ADR); this ADR governs aggregation, not risk.

## Alternatives considered

### Alternative A — Extend `defi-analyst` to own the cross-venue view
Make the DeFi analyst the portfolio owner. **Rejected** (user decision 2026-06-05): it breaks the hard TradFi/DeFi split the skill ecosystem is built on. Tools-only keeps each venue reported by its own seam and lets the agent compose — capability without charter erosion.

### Alternative B — FIFO or specific-lot cost basis
A different accounting method. **Rejected** (user decision 2026-06-05): [ADR-0036](0036-defi-pnl-reconstruction.md) already uses average-cost for DeFi, so FIFO/specific-lot would make the DeFi leg disagree with the rest and double the determinism surface. Average-cost venue-wide is the consistent, deterministic choice; a tax-lot method can be a later, deliberate addition if needed.

### Alternative C — Binance + DeFi only, skip TradFi/manual holdings
Crypto-only portfolio. **Rejected** (user decision 2026-06-05): the user wants equities/other in the view; the manual positions file delivers that cheaply without a broker adapter + key. A live broker integration remains a deferred, heavier option.

### Alternative D — Put aggregation in `defi/`
Reuse the DeFi package. **Rejected** because `defi/` is DeFi-only by [ADR-0035](0035-defi-domain-placement.md); a cross-venue consumer that also reads Binance + equities does not belong there. A top-level `portfolio/` package names what it actually is.

## Notes
- **Rebalance reconciliation:** the `defi-analyst` skill's "rebalance suggestion" frontmatter (flagged in [ADR-0037](0037-defi-position-risk-forecast.md)) is advisor-layer, not portfolio-layer — this subsystem reports exposure; advice is [ADR-0029](0029-advisory-recommendation-boundary.md). Same reconciliation tracked in Plan 0038.
- **Determinism:** aggregation over snapshot inputs is deterministic; the only nondeterminism risk is live-price timing, handled by stamping each leg's as-of time (provenance), mirroring the rest of the repo.
- **No trade secret here:** the Binance *read* key is read-only ([ADR-0038](0038-third-party-api-key-storage.md)); no order path, no trade key — those are Pillar 5.
