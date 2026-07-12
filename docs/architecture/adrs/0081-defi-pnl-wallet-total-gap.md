# ADR-0081 — Closing the DeFi P&L wallet-total gap: Alchemy historical-price fallback + bare-transfer custody-move classification

> **Status:** accepted (Plan 0087 close 2026-07-12)
> **Date:** 2026-07-12
> **Related plan(s):** [Plan 0087](../plans/0087-defi-pnl-wallet-total-gap.md) (implements this end to end)
> **Refines:** [ADR-0079](0079-defi-pnl-gauge-swaps-unclaimed.md) (DeFi P&L completeness) and, through it, [ADR-0036](0036-defi-pnl-transaction-replay.md). Does not supersede either — taxonomy, block-time pricing, average-cost lots, determinism-by-snapshot, and loud-failure all stand. This ADR closes the two residuals Plan 0084's phase-6 live smoke surfaced and left as follow-ups.

## Context

Plan 0084 / ADR-0079 shipped the DeFi P&L completeness work — gauge→pool resolution, swap booking, and a bounded `unclaimed_rewards` read — and validated the capability live on the test wallet (`0xae5b…9790`). But the plan's headline metric ("5/5 complete positions, non-null wallet total") was **not** met: under ADR-0036's "any incomplete position ⇒ null wallet total" rule, two residuals keep the wallet total `null`.

1. **No usable historical-price fallback.** ADR-0079's phase-4 fallback chained a keyless CoinGecko adapter behind DefiLlama, but the phase-6 smoke established that CoinGecko's `market_chart/range` historical endpoint returns **HTTP 401** without a key (unlike `simple/price`, which is keyless-200). So the fallback is inert: one long-tail token (`base:0xef0fd52e…`, the Wanderers position) has no DefiLlama block-time price and no working fallback, nulling the wallet total by itself.

2. **Bare position-token transfers are unclassified.** The confirming run left two events (`0x1cbbb89c…` on position `87f522…`, `0x303f8366…` on position `37023f…`) that each carry a **single** transfer of a position token to/from the wallet with **no paired value leg** — a `send`/`receive` shape the taxonomy never modeled. They fall through `map_events` to `unclassified`, which is a loud failure, nulling their positions. The wallet owner confirms these are **custody moves between their own wallets** (not external disposals).

Both are genuinely new scope — neither is a defect in what Plan 0084 built. The user chose (a) **Alchemy** as the keyed historical-price source and (b) folding both residuals into one follow-up.

Two facts shape the decision:

- **Alchemy's Prices API serves block-time historical prices by contract address on Base.** `POST /prices/v1/{apiKey}/tokens/historical` takes `network` (`base-mainnet`), `address`, `startTime`/`endTime`, and `interval` (`5m`/`1h`/`1d`, with per-request span caps 7d/30d/1yr), and returns `{value, timestamp}` points. It has no single-timestamp query, so the adapter requests a **tight window bracketing the block-time** and takes the point **nearest** the target — the same nearest-indexed approximation DefiLlama and the (inert) CoinGecko adapter already make. It requires an API key (path parameter). Verified against Alchemy's live docs, not assumed — the CoinGecko-401 was the direct cost of assuming an endpoint worked.
- **The `custody_move` primitive already exists.** ADR-0079 introduced it for gauge stake/unstake, and the engine (`pnl.py`) books it as a **no-op** — no realized P&L, basis and units carry. A bare own-wallet transfer is the same economic event (value moves custody, not markets), so it needs no new booking arithmetic — only a new **classification** path.

## Decision

We will close both residuals, keeping every ADR-0036/0079 invariant intact.

1. **Alchemy Prices as the historical-price fallback behind DefiLlama — a new source and secret, not a new package.** We add an `AlchemyHistoricalPriceAdapter` implementing the existing `HistoricalPriceSource` Protocol over the in-house `ResilientHttpClient` (ADR-0019) — a plain REST call, so **no vendor SDK and no new pinned dependency** (the ADR-0012/0013 cooldown does not apply). It brackets the requested block-time with a short window inside the interval's span cap and returns the nearest point, snapshot-cached into the **same** `PriceSnapshotRepository` keyed by the **same** `token_key`, so the merged result stays deterministic (ADR-0036). It replaces the inert keyless CoinGecko adapter as the fallback in `ChainedHistoricalPriceSource` (primary=DefiLlama → fallback=Alchemy); the chain's existing asymmetric error posture (primary error propagates; fallback error / missing key degrades to `None` = no coverage) is unchanged. The Alchemy key is a new secret in the secrets store (`%APPDATA%/market-analyser/secrets.json`), read live, never logged or persisted to the repo — absent the key, the fallback is simply inert (honest incomplete), exactly as today.

2. **A bare single-transfer position-token move classifies as `custody_move`.** In `map_events`, a non-gauge transaction that joins a position by a **single** transfer of that position's token, with a `send`/`receive` shape and **no** add/remove/swap/reward method hint, is classified `custody_move` — booked by the existing no-op path (no realized P&L, basis-neutral). This is the least-wrong default: it neither fabricates a gain/loss nor nulls the position. Precision-first still holds — an ambiguous multi-transfer shape, or any tx that fits nothing, remains an honest `unclassified`.

## Consequences

**Positive:**
- The DeFi P&L wallet total can finally return **non-null** for the test wallet: the `0xef0fd52e…` leg prices via Alchemy and the two custody transfers book as no-ops, so all five positions complete.
- The fallback is a real coverage improvement (verified endpoint), not another inert leg, and adds **no dependency** — only a secret — so it carries no cooldown/pinning burden.
- The taxonomy reuses an existing, tested booking primitive; the change is classification-only, a small blast radius.

**Negative / the price we pay:**
- **A `custody_move` no-op can misstate a genuine *external* send.** If a bare outbound transfer is truly a disposal to a third party (not an own-wallet move), booking it as a no-op carries basis on units the wallet no longer holds, overstating the remaining position. We accept this because (a) the wallet owner confirms these are own-wallet moves, and (b) the alternative — booking a disposal at zero proceeds — fabricates a 100% loss, which is worse under ADR-0036's "an honest gap beats invented arithmetic." The future refinement is a **known-own-address registry** that distinguishes a custody move from an external disposal; until then, a bare transfer is assumed custody. This limitation is documented, not silent.
- **A new external data source and secret** widen the trust surface. Contained: the key lives only in the secrets store, the adapter is behind the resilient client, and a key-absent / transport-error path degrades to honest incomplete rather than crashing (the ADR-0079 best-effort posture).
- **Alchemy's per-request span caps** (7d/30d/1yr by interval) mean the adapter must bracket the target timestamp with a bounded window rather than query a single point; a mis-sized window could miss coverage. Mitigated by choosing a tight bracket at a fine interval and validating real coverage in the human smoke before claiming the leg closed.

## Alternatives considered

- **CoinGecko demo key (`x-cg-demo-api-key`) instead of Alchemy.** Viable and the smallest change (the adapter exists), but the user chose Alchemy — one vendor already on the RPC shortlist, and the REST-over-resilient-client shape means neither option adds a package. Rejected in favor of the user's provider choice.
- **The `alchemy-sdk` package instead of a REST adapter.** Rejected: it would add a pinned dependency under the ADR-0012/0013 cooldown for no benefit — the single REST endpoint is trivially reachable through the existing `ResilientHttpClient`.
- **Book a bare transfer as a disposal (realize against basis at zero/last price).** Rejected: fabricates a large realized loss on what is a custody move, violating ADR-0036's precision-over-plausibility stance.
- **Leave the two transfers `unclassified` (accept the null total).** Rejected: it is the exact failure this ADR exists to close, and the shape is now understood well enough to classify honestly.
- **Build the known-own-address registry now.** Rejected as scope creep: the confirmed own-wallet semantics make the custody-move default correct for the real data today; the registry is a documented followup, not a blocker.
