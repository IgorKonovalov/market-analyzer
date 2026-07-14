# ADR-0098 — Reddit as a keyless crowd-sentiment source

> **Status:** proposed
> **Date:** 2026-07-13
> **Related plan(s):** [0103](../plans/0103-reddit-crowd-sentiment.md)

## Context

We already run three sentiment surfaces — VADER-scored RSS news, StockTwits explicit Bullish/Bearish labels, and crypto Fear & Greed. Reddit adds a **distinct** signal: retail-crowd discussion volume and tone from communities (r/wallstreetbets, r/CryptoCurrency, …) that neither headline news nor StockTwits captures.

Reddit exposes a keyless JSON endpoint (`reddit.com/r/{sub}/search.json`) — no API key, consistent with our keyless-first data posture ([ADR-0069](0069-crypto-first-asset-class-positioning.md)). But it comes with real constraints: Reddit is noisy (memes, sarcasm, brigading), the public JSON endpoint rate-limits aggressively, and its User-Agent/ToS expectations are stricter than the RSS feeds we already pull. The decision — how, and whether, to add it as a first-class source under the [ADR-0031](0031-data-source-adapter-contract.md) adapter contract and the [ADR-0019](0019-external-http-adapter-resilience.md) resilience posture — could reasonably go several ways.

## Decision

We will add Reddit as a keyless `SentimentSource` adapter (`data/adapters/reddit_sentiment.py`) mirroring the shape of `stocktwits.py`: query a configured set of subreddit groups (crypto / stocks / all) for a symbol, score posts with a **keyword lexicon** (bullish/bearish word lists → a −1..+1 aggregate + a label), weight by upvotes, and return the top posts. It rides the ADR-0019 resilient HTTP path (retry / backoff / timeout) with a descriptive User-Agent, **degrades to an honest empty result** on rate-limit or failure (never fabricates), and is exposed via a `reddit_sentiment` MCP tool. It is **crowd sentiment — a condition, not advice** ([ADR-0029](0029-advisory-recommendation-boundary.md)). We deliberately keep the simple keyword lexicon (not VADER/ML) for v1, matching the reference's transparent, dependency-free approach.

## Consequences

### Positive
- A distinct retail-crowd signal, keyless, **no new dependency** (our resilient HTTP path + an in-house lexicon).
- Consistent adapter/tool shape with StockTwits — one more `SentimentSource`, nothing structurally new.
- Honest-degrade under Reddit's flaky public endpoint keeps the failure mode safe (empty, not fabricated).

### Negative
- Reddit's public JSON **rate-limits hard** — heavy use will see empty results. This is documented and surfaced as an honest empty, but it is a real ceiling on how much the signal can be leaned on.
- Keyword scoring is **crude and gameable** — memes and sarcasm mislabel; the signal is lower-quality than StockTwits' explicit tags and must be read as one input among several.
- Reddit content is noisier than the other feeds, so the signal-to-noise is the worst of our sentiment sources.

### Neutral
- The subreddit-group config (which subs per category) becomes a maintained list, exactly like the RSS feed list.

## Alternatives considered

### Alternative A — The authenticated Reddit API (PRAW / OAuth)
Rejected: it adds a keyed dependency and a new secret to store ([ADR-0038](0038-third-party-api-key-storage.md)) for a low-quality signal. The keyless JSON endpoint suffices for our read volume and keeps the keyless-first posture.

### Alternative B — Skip Reddit, rely on StockTwits + news
Rejected: the user explicitly wanted the added crowd breadth, and Reddit captures a community (WSB-style retail) that StockTwits and headline news don't.

### Alternative C — VADER / ML scoring of Reddit posts
Rejected for v1: the transparent keyword lexicon matches the reference and avoids over-fitting a noisy source. VADER-over-Reddit is a plausible later refinement once the raw feed proves useful — but it is not the thing to prove first.
