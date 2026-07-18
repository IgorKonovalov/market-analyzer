# 0111 — Reddit OAuth access path

> **Status:** approved (user picked the keyed-OAuth option, 2026-07-16)
> **Created:** 2026-07-16
> **Owner skill(s):** dev, human
> **Related ADRs:** [0105](../adrs/0105-reddit-keyed-oauth-access-path.md) (paired, accepts at close), [0098](../adrs/0098-reddit-keyless-crowd-sentiment.md) (amended), [0038](../adrs/0038-third-party-api-key-storage.md), [0019](../adrs/0019-external-http-adapter-resilience.md), [0031](../adrs/0031-data-source-adapter-contract.md), [0104](../adrs/0104-mcp-tool-surface-granularity.md)

## TL;DR

Give the Reddit sentiment adapter a **keyed app-only OAuth path** so it can climb over the anti-bot wall that 403-blocks all keyless Reddit JSON from this machine (2026-07-16 finding — [ADR-0105](../adrs/0105-reddit-keyed-oauth-access-path.md) has the probe evidence). Two new `SecretsStore` keys (`reddit_client_id`, `reddit_client_secret`); both present → token via `client_credentials` + search via `oauth.reddit.com`; either absent → today's keyless path, byte-identical. Scoring, group, seam, and tool mode all unchanged — this is an access-path amendment, not a rework. The **credentialed live smoke is the point of the plan**: `oauth.reddit.com` served the block page to an *unauthenticated* probe, so whether a valid bearer passes is genuinely unknown; phase 2 answers it and its outcome routes back to architect either way.

## Amendments

- **2026-07-18 (architect) — token body via a new shared-HTTP form-body passthrough.** Phase 1's token request (`POST /api/v1/access_token`, `grant_type=client_credentials`) requires a **form-urlencoded body** (OAuth2 RFC 6749 §4.4.2 + Reddit docs), but the shared `ResilientHttpClient.post` supported only JSON bodies or query params. A dev pre-flight probe confirmed the token endpoint is reachable (structured JSON 401 under dummy creds) but could not prove Reddit tolerates `grant_type` as a query param (auth is validated before the grant), and the query-param form is non-standard enough to risk an ambiguous phase-2 smoke failure. **Decision:** add a minimal, additive `data: bytes | None = None` passthrough to `ResilientHttpClient.post` (form-urlencoded default `Content-Type`, mutually exclusive with `json=`, backward-compatible), and pull `src/market_analyser/data/_http.py` + its test suite into phase-1 scope. **No new ADR** — an incremental extension within [ADR-0019](../adrs/0019-external-http-adapter-resilience.md)'s resilient-HTTP-client remit, not a new durable tradeoff. Does not change [ADR-0105](../adrs/0105-reddit-keyed-oauth-access-path.md) (which specifies the keyed path, not the body encoding) or any other phase.

## Context & problem

`sentiment(source="reddit")` (Plan 0103, closed with its live smoke deferred) returns `sample_size=0` on every live call. The investigation proved this is Reddit's IP/client-level anti-bot wall (403 + HTML block page for every keyless request shape, including a browser User-Agent), not an adapter defect — the honest-degrade is working exactly as designed, which also means the source is silently dead. ADR-0098 rejected OAuth on an assumption the evidence now contradicts; ADR-0105 reverses that rejection.

Probe facts the design leans on: the token endpoint answers with a structured JSON 401 (reachable API), while `oauth.reddit.com` without a bearer serves the block page (keyed viability unproven until tried).

## Decision

Implement ADR-0105: key-gated OAuth inside `data/adapters/reddit_sentiment.py`, following the `SecretsStore`-injection pattern the keyed adapters already use (constructor-injected store, lazy `get()` at call time — cf. `alchemy_historical_price.py`). No new dependency, no new tool, no toolset change.

## Architecture diagram

```mermaid
flowchart LR
    subgraph sidecar [Python sidecar]
        T["sentiment tool<br/>source=reddit (ADR-0104)"]
        A["reddit_sentiment.py<br/>lexicon + upvote weighting (unchanged)"]
        K["SecretsStore (ADR-0038)<br/>reddit_client_id + reddit_client_secret"]
        R["resilient HTTP (ADR-0019)"]
        T --> A --> K
        A --> R
    end
    R -- "keys set: POST basic-auth" --> TOK[(www.reddit.com<br/>/api/v1/access_token)]
    R -- "keys set: GET bearer" --> OAUTH[(oauth.reddit.com<br/>/r/group/search)]
    R -. "keys absent: today's keyless GET<br/>(currently 403-walled)" .-> WWW[(www.reddit.com<br/>search.json)]
```

## Implementation phases

### Phase 1 — Secrets keys + keyed adapter path + tests
- **Owner skill:** dev
- **What:**
  - `data/_http.py` (shared infra — added to scope by the 2026-07-18 amendment; see [Amendments](#amendments)): `ResilientHttpClient.post` today derives its body **only** from `json=` (JSON-encoded, `application/json`). An OAuth2 `client_credentials` token request needs a **form-urlencoded body**, so add a minimal, additive `data: bytes | None = None` passthrough: when `data` is provided, use it verbatim as the request body and `setdefault` the `Content-Type` to `application/x-www-form-urlencoded` (caller may override via `headers=`); `json=` and `data=` are mutually exclusive (raise `ValueError` if both are passed). Backward-compatible — every existing call omits `data` and is unchanged; `_request`/`_perform_request` already thread `body: bytes | None`, so this is a one-method change.
  - `persistence/secrets.py`: add `reddit_client_id` and `reddit_client_secret` to the `SecretKey` Literal and `SecretsFile` fields (env overrides `MARKET_ANALYSER_REDDIT_CLIENT_ID` / `..._SECRET` come free from the prefix convention; the settings status route picks the keys up from `KNOWN_SECRET_KEYS`).
  - `data/adapters/reddit_sentiment.py`: accept `secrets_store: SecretsStore | None = None` (constructor-injected by the default provider; `None` preserves keyless-only behavior for existing tests). At fetch time, if **both** keys are set, use the keyed path: obtain an app-only token (`POST https://www.reddit.com/api/v1/access_token`, HTTP Basic `client_id:client_secret`, form body `data=b"grant_type=client_credentials"` via the new `post(data=…)` passthrough — **not** a query param; see [Amendments](#amendments), descriptive User-Agent kept), cache `(token, expires_at)` on the instance with a ~60s expiry margin, then `GET https://oauth.reddit.com/r/{group}/search` with the **same params, window filter, lexicon, and upvote weighting** plus `Authorization: bearer <token>`. On a 401 from the search, refresh the token once and retry; any further failure (token or search) degrades to the existing honest-empty. If either key is absent, run today's keyless path untouched.
  - Secret hygiene: the Basic/bearer headers ride `headers=` (already excluded from the response-cache key and from failure logs, which are path-only); token responses must not enter the shared TTL response cache (fetch the token with caching disabled — e.g. a `cache_ttl_seconds=0` client or an explicit bypass).
  - Tests: **(a) `tests/data/test_resilient_http_client.py`** — the new `post(data=…)` passthrough sends the raw bytes verbatim as the body, `setdefault`s a `application/x-www-form-urlencoded` Content-Type (overridable via `headers=`), and rejects `json=`+`data=` together. **(b) the adapter suite** (`tests/data/test_reddit_sentiment_adapter.py`, transport-seam monkeypatch per its existing pattern): no-key → keyless URL unchanged; keyed → basic-auth token POST then bearer GET on the oauth host with identical search params; expiry → re-auth; search-401 → exactly one refresh-and-retry; token failure → honest-empty; secrets never appear in logs or cache keys. **(c) `tests/persistence/test_secrets.py`** — the two new keys are settable/gettable/env-overridable/default-unset (mirror the existing `alchemy_prices_key` test).
  - Docs: the `sentiment` tool description currently says the reddit source is "keyless" — update the wording to "keyless, or keyed OAuth when configured" and **regenerate** the API reference (`apiref` in-process, per Plan 0070 — never hand-edit generated docs).
- **Done when:** `uv run pytest` green (including the new `post(data=…)` passthrough test); with no keys configured **both** the adapter's request behavior and every existing `ResilientHttpClient.post` caller are byte-identical to today; apiref regenerated with no other diff.

### Phase 2 — Register app, configure secrets, live smoke (the de-risk gate)
- **Owner skill:** human
- **What:** Register a Reddit app at `reddit.com/prefs/apps` (type "script" is fine for `client_credentials`), put `reddit_client_id`/`reddit_client_secret` into `secrets.json` (or the env vars). Smoke: `sentiment(source="reddit", symbol="BTC", window="7d")` — **success = `sample_size > 0`**; repeat for `ETH` to confirm it isn't a one-off. Record the outcome in this plan file.
- **Done when:** either (a) the smoke passes → close ceremony accepts ADR-0105, or (b) `oauth.reddit.com` still serves the block page to a valid bearer → record the failure honestly and route back to `architect` for the ADR-0105 fallback decision (accept-degraded vs supersede-and-drop). **Both outcomes complete the phase** — the plan exists to answer the question, not to guarantee a yes.

## Risks & open questions

- **The wall may not respect bearers.** `oauth.reddit.com` block-paged an unauthenticated probe; a valid token *should* pass (that host is the documented API surface), but this is the plan's central uncertainty and phase 2 is deliberately cheap to reach.
- **Registration friction.** Reddit app creation may require a verified email / aged account; if the user has no Reddit account this stalls at phase 2 (surface early).
- **Quota:** app-only OAuth is ~100 QPM — far above our volume (one request per tool call, 5-minute TTL cache); no throttling work needed.
- **ToS posture:** app-only read of public search results through the official API surface, descriptive User-Agent kept — the compliant lane, unlike alternative E's scraping.
- **Token body must be form-encoded, not a query param (resolved 2026-07-18).** OAuth2 (RFC 6749 §4.4.2) and Reddit's docs both mandate a form body; a dummy-cred probe could not prove Reddit tolerates `grant_type` as a query param (auth is checked first), and the non-standard form would risk phase 2's smoke failing for a reason unrelated to the anti-bot wall. Resolved by the new `ResilientHttpClient.post(data=…)` passthrough — see [Amendments](#amendments).
- **LunarCrush `x` source has a parallel wiring gap (out of scope — follow-up).** Dev found that the `x`/LunarCrush source (Plan 0108) gates the key at the *tool* layer (`make_x_source`) but its provider adapter (`DefaultMarketDataProvider._social`) is default-constructed **without** a `SecretsStore`, so `provider.get_sentiment(source="x")` reaches a store-less adapter and can never use a key in production — masked today only because the $0 LunarCrush tier 402s every call. This plan deliberately avoids the gap for Reddit by injecting the store into the **adapter** via the provider. Auditing/fixing the LunarCrush wiring is a separate follow-up against Plan 0108 / [ADR-0103](../adrs/0103-social-x-sentiment-source.md), not part of 0111.

## What this plan does NOT do

- No `degrade_reason` diagnosability field (ADR-0105 alternative B — an independent follow-up decision for all sentiment sources).
- No user-context OAuth, no PRAW, no new dependency.
- No scoring changes (lexicon/upvote weighting stand), no per-category subreddit groups, no `top_posts` payload.
- No tool or toolset change (`sentiment(source="reddit")` mode is untouched; `EXPECTED_FULL_TOOLSET` stays 51).
- No renderer work — there is no secrets settings form; configuration is the `secrets.json`/env path used by `base_rpc_url`.
