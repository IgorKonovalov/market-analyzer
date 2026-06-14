# ADR-0053 — On-chain valuation metrics: CoinMetrics community primary, blockchain.com as MVRV cross-check

> **Status:** proposed (accepts at Plan 0057's close)
> **Date:** 2026-06-09
> **Related plan(s):** 0057-onchain-valuation-metrics (implements)
> **Related ADRs:** [ADR-0051](0051-historized-metric-series-contract.md) (the store these series land in), [ADR-0031](0031-data-source-adapter-contract.md) (adapter contract), [ADR-0019](0019-external-http-adapter-resilience.md) (HTTP resilience), [ADR-0054](0054-exogenous-forecast-features-multi-horizon.md) (the consumer)

## Context

BTC cycle analysis beyond price math wants on-chain valuation metrics — MVRV (market cap / realized cap), SOPR (spent-output profit ratio), realized cap — as historized daily series for the snapshot surface and Plan 0059's forecast features. The serious on-chain providers (Glassnode, CryptoQuant) are paid; the question is which *free* source carries these metrics with full daily history, keyless, at a usable rate limit.

The 2026-06-09 verification pass (web docs; live probing was unavailable in-session) established: **blockchain.com Charts API** is free/keyless (`api.blockchain.info/charts/<slug>?timespan=all&sampled=false&format=json`, ~1 req/10s) and **confirms MVRV** — but targeted searches surfaced **no SOPR, NUPL, or realized-cap charts there**; treat them as absent until probed. **CoinMetrics Community API** is keyless (documented: no key for community endpoints), rate-limited at a documented **10 requests / 6-second window**, and **confirms `CapMVRVCur` (MVRV) and `CapRealUSD` (realized cap)** in community data, with **SOPR likely** but its presence on the current community coverage page unverified (CoinMetrics has retired community metrics before).

The user's interview pick led with blockchain.com; the verification showed it covers one of the three target metrics. This ADR records the evidence-driven reversal.

## Decision

We will use the **CoinMetrics Community API as the primary on-chain valuation source**: one adapter fetching daily `asset-metrics` for BTC — `CapMVRVCur`, `CapRealUSD`, and `SOPR` (subject to the phase-1 coverage probe) — backfilled in full and updated incrementally into ADR-0051 series (`coinmetrics.btc.mvrv`, `coinmetrics.btc.realized_cap`, `coinmetrics.btc.sopr`). The **blockchain.com `mvrv` chart serves as a cross-check, not a second pipeline**: a comparison step asserts the two sources' MVRV agree within tolerance over an overlap window, and logged drift is a data-quality finding. Plan 0057's first phase is the **live coverage probe** — if SOPR turns out absent from community data, the series ships without it and this ADR gains a note, not a rewrite; if `CapMVRVCur` itself is gone, the decision reverts to blockchain.com-primary (MVRV-only) via an amendment.

## Consequences

### Positive
- One API covers (likely) all three target metrics with full daily history, keyless, under a *documented* rate limit — the only candidate that does.
- Metric definitions are published and versioned (CoinMetrics docs/GitHub) — when a number looks odd, there's a spec to check it against, which "free API" sources rarely offer.
- The cross-check turns "we trust one free source" into "two independent computations of MVRV agree," materially better epistemics for numbers that feed a model.

### Negative
- **Community-tier coverage is revocable.** CoinMetrics has retired community metrics before; a future trimming could remove a series we depend on mid-program. Mitigation: the points already accrued are ours (ADR-0051 store), so loss of source ≠ loss of history; the forecast's row-drop policy degrades honestly.
- SOPR remains unconfirmed until the probe — the plan is written to survive either answer, but the feature list in Plan 0059 may lose a column.
- BTC-only scope for v1 (the cycle thesis is BTC-centric); extending to ETH later is a registry entry + the same adapter, but nobody should mistake this for general on-chain coverage.

### Neutral
- Realized *price* (realized cap ÷ supply) is derivable at read time if wanted; we store what the source publishes (`CapRealUSD`) rather than deriving at ingest.

## Alternatives considered

### Alternative A — blockchain.com Charts primary (the interview's lead pick)
Free, keyless, MVRV confirmed, venerable. Rejected as primary because the verification found no SOPR/NUPL/realized-cap charts there — it covers one of three target metrics; its rate guidance (~1 req/10s) is also the most restrictive of the candidates. Retained as the MVRV cross-check, where it adds real value.

### Alternative B — Glassnode / CryptoQuant free tiers
The reference implementations for these metrics. Rejected: free tiers are API-keyed, tightly capped, and historically unstable in what they expose; building a dependency on a teaser tier of a paid product invites breakage the moment the vendor adjusts the paywall.

### Alternative C — Compute MVRV/SOPR ourselves from raw chain data
Maximum sovereignty, zero vendor risk. Rejected without hesitation: requires a full node + UTXO-level accounting (realized cap is a per-UTXO computation) — an entire infrastructure program to replace two HTTP adapters.

## Notes
- Pre-implementation probes (Plan 0057 phase 1, ~minutes): `community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMVRVCur,CapRealUSD,SOPR` (coverage + earliest ts), `api.blockchain.info/charts/mvrv?timespan=all&sampled=false&format=json` (depth + cadence).
- Rate pacing: full-history daily backfill for 3 metrics fits in a handful of paged requests — one-time cost, well inside 10/6s.

## Probe outcome (2026-06-14) — reduced to MVRV-only

Plan 0057 phase 1 ran both probes live from the user's network. The result reshapes this ADR to **MVRV-only** — the "ADR gains a note, not a rewrite" path the Decision anticipated:

- **MVRV (`CapMVRVCur`): available keyless**, full daily history to **2011-12-29** — confirmed. CoinMetrics stays primary.
- **Realized cap (`CapRealUSD`): `forbidden`** without paid credentials — NOT on the community tier (the Context above assumed it was; the live probe overturned that).
- **SOPR: `forbidden`** without paid credentials — absent from community, as the Negative consequence flagged was possible.
- **blockchain.com cross-check** (`charts/mvrv`, `charts/market-value-to-realized-value`): both `not-found` — the endpoint named in Alternative A / Notes is gone.
- Free-source sweep for the two paywalled metrics: the only keyless SOPR is **bgeometrics** `/v1/sopr` (4-year window, 10 req/hr, single small vendor); no keyless realized cap exists. The user declined bgeometrics for a metric Plan 0059 does not consume.

**Net decision (primary source unchanged, scope reduced):** implement **one series, `coinmetrics.btc.mvrv`**. Realized-cap and SOPR series are dropped; the blockchain.com cross-check is cut (no working free MVRV cross-check remains). This still satisfies the consumer — [ADR-0054](0054-exogenous-forecast-features-multi-horizon.md) / Plan 0059's exogenous feature list names MVRV, not SOPR/realized-cap. Status stays `proposed`; accepts at Plan 0057's (MVRV-only) close.
