# ADR-0105 — Reddit keyed OAuth access path

> **Status:** proposed (Plan 0111 accepts at close)
> **Date:** 2026-07-16
> **Related plan(s):** [0111](../plans/0111-reddit-oauth-access-path.md)
> **Amends:** [ADR-0098](0098-reddit-keyless-crowd-sentiment.md) (access path only; reverses its Alternative A rejection on new evidence)

## Context

A 2026-07-16 live investigation found `sentiment(source="reddit")` returning `sample_size=0` on every call. An out-of-process probe replaying the adapter's exact request — plus four controls (single-subreddit search, `old.reddit.com`, a plain `/r/Bitcoin/new.json` listing, a full browser User-Agent) — got **HTTP 403 with Reddit's HTML block page on all five**. The wall is IP/client-fingerprint-level anti-bot enforcement, not request shape: keyless Reddit JSON is unreachable from this machine, and the adapter's honest-degrade ([ADR-0019](0019-external-http-adapter-resilience.md)) correctly converts that into the neutral empty sample.

Two facts sharpen this from "flaky upstream" to "decision required":

1. **Plan 0103 closed with its phase-3 live smoke deferred.** The investigation was effectively that smoke, and it failed — the keyless path has plausibly never returned live data from this machine.
2. **ADR-0098 rejected OAuth as its Alternative A** on the stated assumption that "the keyless JSON endpoint suffices for our read volume". The evidence now contradicts that assumption, so the rejection must be revisited rather than silently worked around.

A follow-up probe of the OAuth surface (no credentials) found the token endpoint (`www.reddit.com/api/v1/access_token`) answering with a **structured JSON 401** — reachable, behaving as an API — while `oauth.reddit.com` without a bearer still served the block page. So the keyed path is *plausible but unproven* until tried with real credentials.

## Decision

We amend ADR-0098's access path: the Reddit adapter gains a **keyed app-only OAuth path** (the `client_credentials` grant of a user-registered Reddit app), used whenever both of two new `SecretsStore` keys — `reddit_client_id` and `reddit_client_secret` — are configured ([ADR-0038](0038-third-party-api-key-storage.md): env-override-first, server-side injection, values never logged). Keyed flow: POST the token endpoint with HTTP Basic (client_id:client_secret), cache the bearer in-process with an expiry margin, and issue the same search request against `oauth.reddit.com` instead of `www.reddit.com`. **Absent either key, the existing keyless path stands unchanged** (honest-empty when blocked), so no-key behavior regresses nothing — the same ships-inert posture as [ADR-0103](0103-social-x-sentiment-source.md)/Plan 0108. Keyed-path failures (token or search) degrade to the same honest-empty, never fabricate. Everything else in ADR-0098 stands: the single fixed multi-subreddit group, the keyword lexicon, upvote weighting, the `SentimentSource` seam, the `sentiment(source="reddit")` tool mode ([ADR-0104](0104-mcp-tool-surface-granularity.md)).

**Honest uncertainty:** whether a valid bearer passes the anti-bot wall on `oauth.reddit.com` from this network is unproven until Plan 0111's live smoke. The smoke is the point of the plan; if it fails, the fallback decision — accept a permanently degraded source, or supersede ADR-0098 and drop it — comes back here as a new ADR, with real evidence in hand.

## Consequences

### Positive
- The most likely restoration of the feature: the official keyed quota (~100 requests/minute) replaces a hard block.
- Zero regression without keys — the keyless attempt (and its honest-empty) is exactly today's behavior.
- Consistent with the established keyed-source precedent (ADR-0038 storage, ADR-0103 ships-inert-without-key), so nothing structurally new.

### Negative
- The **second deliberate break of keyless-first** ([ADR-0069](0069-crypto-first-asset-class-positioning.md)) in the sentiment family, and this one for a signal ADR-0098 itself rates the noisiest of our sources.
- Two new secrets plus a one-time human registration step (Reddit app at `reddit.com/prefs/apps`).
- A token lifecycle is a new moving part (cached bearer, expiry, refresh-once-on-401) inside a previously stateless adapter.
- Viability stays unproven until the credentialed smoke — this ADR may be back within a week for the drop decision.

## Alternatives considered

### A — Keep keyless only
Rejected: proven blocked from this machine at the network level, across every endpoint and User-Agent tried.

### B — Only make the degrade visible (a `degrade_reason` on the payload)
Rejected *as this decision*: it restores diagnosability, not the signal. It remains a worthwhile independent follow-up for all sentiment sources, but it is not an access path.

### C — Drop the Reddit source (supersede ADR-0098)
Rejected as premature: the official keyed path is untried, and the WSB-style retail crowd signal has no substitute among our other sources. Becomes the live option if Plan 0111's smoke fails.

### D — User-context OAuth (password grant / PRAW)
Rejected: requires the user's account credentials (a heavier secret) and a new dependency; app-only auth suffices for reading public search results.

### E — Scraping mirrors / alternate frontends
Rejected: fragile, ToS-hostile, and exactly the traffic class Reddit's wall exists to block.
