# ADR-0103 — X (Twitter) / social sentiment as a keyed source

> **Status:** proposed
> **Date:** 2026-07-15
> **Related plan(s):** [0108](../plans/0108-social-sentiment-source.md)

## Context

We run four sentiment surfaces on one `SentimentSource` seam ([ADR-0031](0031-data-source-adapter-contract.md)): VADER-scored RSS news, StockTwits explicit tags, crypto Fear & Greed, and Reddit crowd sentiment (keyless, [ADR-0098](0098-reddit-keyless-crowd-sentiment.md) / Plan 0103). The user wants **X (Twitter) social sentiment** as another input. Adding a fifth `SentimentSource` is structurally trivial — the seam is proven. The hard part is the source, because **X is where our keyless-first posture ([ADR-0069](0069-crypto-first-asset-class-positioning.md)) breaks**:

- **X API v2** has been paid-and-locked since 2023. The free tier is effectively write-only; read access supporting sentiment starts around the Basic tier (~$200/mo) with harsh rate limits.
- **Social aggregators** (LunarCrush, Santiment, CryptoCompare social) ingest X and expose a clean sentiment/score API — but are also keyed, most behind a paid tier.
- **Scraping / Nitter** is fragile and ToS-gray — not a foundation we will build on.

The user's directive is explicit: **design the decision now, decide the spend later.** So this ADR fixes the *shape* and *recommendation* while leaving the concrete provider a deferred, reversible choice.

## Decision

We will add **X/social sentiment as a keyed `SentimentSource`**, implemented **source-agnostically behind the existing seam**, with the concrete provider chosen at spend-time:

- The adapter conforms to `SentimentSource.fetch_sentiment(symbol, window) -> SentimentSample` (identical shape to `reddit_sentiment` / `stocktwits`), so a social score is symmetric with every other surface and the consuming skills need no special case.
- The provider key resolves through `SecretsStore` ([ADR-0038](0038-third-party-api-key-storage.md), same pattern as zerion/alchemy/rpc). **Absent the key, the source is inert and returns an honest-empty result** ([ADR-0019](0019-external-http-adapter-resilience.md)) — the tool ships and degrades cleanly with no key configured.
- **Recommended provider: LunarCrush** (an aggregator that covers X) over the raw X API — one key, sentiment already aggregated, cheaper and lower-integration-cost than X v2, and it sidesteps X's rate-limit and ToS friction. The raw X API stays a documented alternative if first-party X data is later judged necessary.
- It is **crowd sentiment — a condition, not advice** ([ADR-0029](0029-advisory-recommendation-boundary.md)), wall-clock-sensitive with **no `as_of` replay**, exactly like the other sentiment tools.

The final provider pick and the spend are **deferred**: Plan 0108 builds the seam and a reference adapter; accepting *this ADR* commits us to the shape and the keyed-with-honest-empty posture, not to a specific bill.

## Consequences

### Positive
- The decision and its tradeoffs are captured now; the money question is isolated to one reversible config/key choice.
- Source-agnostic-behind-the-seam means the same code serves LunarCrush or raw X — swapping providers is an adapter change, not a redesign.
- Honest-empty-without-key means the feature can land and sit dormant until the user funds a key — no half-built branch rotting.

### Negative
- **This breaks the keyless-first posture** for the first time on the sentiment side. That is the price of X coverage; the ADR records it deliberately rather than letting it happen by accident.
- A paid dependency's coverage and quality for the **specific small-cap tokens the user holds** (e.g. AERO) is unverified — LunarCrush covers majors well; long-tail coverage must be checked before committing spend.
- Aggregator sentiment is a **black box** relative to our transparent in-house lexicon (Reddit) or VADER (news) — we consume a vendor's score and cannot fully audit it.

### Neutral
- Until a key is funded, the practical X signal is **Reddit (Plan 0103)** — the keyless retail-crowd proxy. This ADR does not change that; it makes true X coverage a funded switch-flip.

## Alternatives considered

### Alternative A — X API v2 directly
Documented, not chosen as the recommendation: first-party X data, but ~$200/mo Basic tier, harsh rate limits, and more integration/pagination work for a raw firehose we would still have to score ourselves. Kept as the fallback if aggregator coverage proves inadequate.

### Alternative B — Skip X entirely; rely on Reddit
Rejected as the *decision* (but it is the *default until funded*): Reddit (Plan 0103) already gives a keyless retail-crowd signal, but the user explicitly asked for X, which reaches a distinct audience (crypto-Twitter / KOLs) Reddit does not.

### Alternative C — Scrape X / Nitter
Rejected: fragile (Nitter instances die, markup shifts), ToS-gray, and an unstable foundation. Honest-empty-behind-a-key beats a scraper that breaks silently.

### Alternative D — Defer the whole thing (no ADR, no seam)
Rejected: the seam is cheap and proven, and building it source-agnostically now (with honest-empty) costs little and means the spend decision later is a key, not a project. Capturing the shape is exactly what "design it, decide cost later" asks for.
