# ADR-0103 — X (Twitter) / social sentiment as a keyed source

> **Status:** proposed
> **Date:** 2026-07-15
> **Amended:** 2026-07-17 — live probe: the $0 LunarCrush tier has zero v4 API access; minimum spend is Individual (see §Amendment)
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

## Amendment 2026-07-17 — the free tier has zero API surface; minimum spend is Individual

Plan 0108 phases 1–2 shipped (`4a98cbe`, `104b8ac`) and the first live probe ran against a valid $0-tier LunarCrush key. Findings:

- **Every documented v4 endpoint family probed returns HTTP 402** — `topic/{t}/v1`, `topic/{t}/whatsup/v1`, `topic/{t}/time-series/v2`, `topics/list/v1`, `coins/{c}/v1`, `coins/list/v1`, `coins/{c}/meta/v1`, `searches/list`, `system/changes` (nine endpoints, 2026-07-17): `"You must have an active Individual or higher subscription to use this endpoint."` The key authenticates (a bad key 401s); the tier simply carries **no v4 API access at all**. The sign-up-time "free tier: 100 requests/day, 4/min" framing corresponds to no usable endpoint.
- **Individual is the minimum sufficient tier** for every probed endpoint — the 402 text names it, including for the `topic` endpoint the Plan 0108 adapter consumes. Public pricing as of July 2026 (search-sourced; the pricing page is a client-rendered SPA): Individual $90/mo (limited endpoint set, 10 req/min, 2,000/day), Builder $300/mo (all endpoints, 100 req/min, 20,000/day). Individual's budget comfortably covers the adapter's footprint (concurrency 1, 15-minute response cache).
- **The shipped seam behaved exactly as designed** on the live 402s: `sentiment(source="x")` for BTC and AERO degraded honest-empty with no fabricated data and no note (key present), proving the ADR-0019 path end to end.

Consequences for this ADR's decision:

- **The shape stands unchanged** — source-agnostic seam, `SecretsStore` key, honest-empty degrade. No code change follows from this amendment.
- **The cost basis is corrected.** "Cheaper than raw X v2" holds ($90/mo vs ~$200/mo X Basic) but there is **no free on-ramp**: the spend decision is a concrete $90/mo minimum, and Plan 0108 phase 3 is executable only at Individual or higher.
- **The default-until-funded posture gains weight.** A 2026-07-17 spot-check of the nearest alternative aggregator (Santiment) shows the same shape: its free API tier (1,000 calls/month) lags restricted metrics — social included — by 30 days, and real-time social access requires a paid Max plan; CryptoCompare's legacy social-stats API is retired. There is no $0 real-time X/social aggregate anywhere surveyed. Reddit ([ADR-0098](0098-reddit-keyless-crowd-sentiment.md) / [ADR-0105](0105-reddit-keyed-oauth-access-path.md), Plan 0111 — free) is therefore the only $0 crowd-sentiment path, and Plan 0111 rises in practical priority if the LunarCrush spend is declined.
- **Alternative A (raw X v2, ~$200/mo) remains the documented fallback**, still dominated by LunarCrush on cost and integration effort.
