# ADR-0019 — External HTTP adapter resilience pattern (shared module)

> **Status:** proposed
> **Date:** 2026-05-20
> **Related plan(s):** [0009-resilience-and-tradingview-screener](../plans/0009-resilience-and-tradingview-screener.md) (lands this module + the first adapter that sits on it), [0010-news-and-vader-sentiment](../plans/0010-news-and-vader-sentiment.md), [0011-fear-and-greed-indices](../plans/0011-fear-and-greed-indices.md), [0012-stocktwits-sentiment](../plans/0012-stocktwits-sentiment.md)
> **Related ADRs:** [ADR-0007](0007-market-data-provider.md) (`MarketDataProvider` Protocol — adapters that sit on this module), [ADR-0009](0009-rewrite-data-layer-in-house.md) (in-house data layer — superseded ADR-0003), [ADR-0006](0006-persistence-layout.md) (SQLite cache vs in-memory TTL cache distinction)

## Context

The first in-house adapter — `YahooAdapter` from [Plan 0003](../plans/done/0003-excise-vendored-upstream.md) — handles its own resilience inline: a retry on transient failures, basic timeout, no caching beyond the SQLite `bars` cache from [ADR-0006](0006-persistence-layout.md). That works for one adapter. The roadmap's Tier 2 names five more anonymous-or-cheap external HTTP sources we want to consume:

- **TradingView screener** (`tradingview-screener` library — POST `/america/scan`-style)
- **RSS news** (`feedparser` — heterogeneous publisher feeds)
- **Alternative.me crypto Fear & Greed** (free JSON endpoint)
- **StockTwits** (free API tier — `/streams/symbol/{ticker}.json`)
- **CoinGecko BTC macro context** (free JSON endpoints)

Each needs the same four behaviors:

1. **Short-lived in-memory TTL cache** for results that go stale fast (screener results, fear/greed value, current sentiment snapshot). Distinct from SQLite, which caches OHLCV bars across sessions — these are intra-process and ephemeral.
2. **Transient-error classification + exponential backoff retry.** `JSONDecodeError`, connection reset, empty body, HTTP 5xx, HTTP 429, and timeout are retryable; HTTP 4xx (other than 429) and DNS-failure are not.
3. **Bounded concurrency.** Each upstream has its own politeness limit; without a cap, multiple symbols fanned out from one MCP call will hit the upstream concurrently and trigger rate limits or bans.
4. **Proxy configuration from environment only.** No secrets in code. Optional Webshare-style rotating proxy support read from a documented env-var bundle, with a direct→proxy fallback chain.

The `tradingview-mcp` upstream (the project we used to vendor before [ADR-0009](0009-rewrite-data-layer-in-house.md)) added a per-service resilience layer ad-hoc in 2026-05-13 after recurring rate-limit incidents. The shapes drifted: the screener service had a 60s TTL with 4-concurrent cap; the Yahoo service had no cache but had proxy support; the news service had neither. The user-visible cost was inconsistent failure modes — one source would silently quietly return stale data, another would 429, another would throw. A maintainer fixing a bug in one had to remember which.

We have the chance to land the pattern once, before the second adapter, instead of refactoring three of them later.

A second-order question: do we adopt third-party libraries (`tenacity` for retries, `cachetools` for the TTL cache) or write the resilience module in-house? `tenacity` is excellent and well-maintained but adds a dependency for ~150 lines of net code, and the policy ([ADR-0012](0012-dependency-cooldown.md), [ADR-0013](0013-pin-direct-dependencies.md)) explicitly favors tight dependency curation. `cachetools` is similar — a TTL dict in 30 lines of stdlib. The cost of in-house is the maintenance burden; the benefit is dependency parsimony and an interface shaped for our exact needs.

## Decision

We will land a single in-house module `src/market_analyser/data/_http.py` that exposes a `ResilientHttpClient` class. All Tier 2 adapters (and, retroactively as a tidy-up, `YahooAdapter`) call into it for their external HTTP traffic. The module is package-internal (underscore prefix) — downstream code does not import it; it is reached only through the `MarketDataProvider` Protocol.

```python
# src/market_analyser/data/_http.py — illustrative; final shape locked in Plan 0009 phase 1.

class ResilientHttpClient:
    """A blocking HTTP client with TTL cache, retry, backoff, and concurrency cap.

    Instantiated once per source (one per adapter): each source has its own
    politeness budget. Cache, retry policy, and concurrency cap are configured
    at construction; the client itself is thread-safe and re-entrant.
    """

    def __init__(
        self,
        *,
        source_name: str,                            # for telemetry + cache namespacing
        cache_ttl_seconds: float = 0.0,              # 0 disables the cache
        cache_max_entries: int = 256,
        max_retries: int = 3,
        backoff_initial_seconds: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_max_seconds: float = 30.0,
        max_concurrency: int = 4,
        request_timeout_seconds: float = 10.0,
        user_agent: str = "market-analyser/<version>",
        proxy: ProxyConfig | None = None,            # None = direct; from env helper
    ) -> None: ...

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,                # if None, derived from (url, params)
    ) -> HttpResponse: ...

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int | float] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_key: str | None = None,
    ) -> HttpResponse: ...

    def classify(self, exc: BaseException, response: HttpResponse | None) -> ErrorKind:
        """Override hook for adapters that need to extend the transient/permanent split."""
        ...
```

```python
class HttpResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: bytes
    elapsed_seconds: float
    # convenience
    def json(self) -> Any: ...
    @property
    def text(self) -> str: ...

class ErrorKind(StrEnum):
    TRANSIENT = "transient"   # retry with backoff
    PERMANENT = "permanent"   # raise to caller
    RATELIMIT = "ratelimit"   # retry with backoff; longer floor

class ProxyConfig(BaseModel):
    http_url: str
    https_url: str
    rotation_session_id: str | None = None
    # constructed via ProxyConfig.from_env() — never hand-built in code
```

**Behaviors:**

- **Cache.** Keyed on `(method, url, sorted(params), cache_key_override)`. Cached entries hold the full `HttpResponse` and expire after `cache_ttl_seconds`. Bounded by `cache_max_entries` with LRU eviction. Implemented in-house with `collections.OrderedDict` and a per-instance lock; ~30 lines.
- **Retry.** Up to `max_retries` attempts on `ErrorKind.TRANSIENT` or `ErrorKind.RATELIMIT`. Backoff is `min(backoff_max, backoff_initial * backoff_factor ** attempt)` plus jitter (`random.uniform(0, 0.25 * backoff)`) — jitter prevents synchronized retries across adapters running in parallel. `ErrorKind.PERMANENT` raises immediately. On final retry exhaustion, raises a `ResilientHttpError(source_name, last_response, last_exception)`.
- **Concurrency cap.** Per-instance `threading.Semaphore(max_concurrency)`. Calls to `get`/`post` block until a slot is available. Backpressure is the caller's problem to surface (e.g. the MCP tool's request shows latency) — we do not queue or drop; we block.
- **Default classifier.** `JSONDecodeError`, `ConnectionError`, `TimeoutError`, empty body (when JSON is expected; opt-in via `expect_json=True` argument), HTTP 5xx, HTTP 408, HTTP 503 → `TRANSIENT`. HTTP 429 → `RATELIMIT` (longer initial backoff). DNS errors, HTTP 4xx (other than 429), invalid TLS → `PERMANENT`. The classifier is a method (`self.classify`) so adapters can extend (e.g. StockTwits' 403-on-rate-limit needs to be reclassified as `RATELIMIT`).
- **Proxy chain.** When `proxy=None`, requests go direct. When `proxy=ProxyConfig(...)`, the first attempt goes through the proxy; if the proxy returns a connection error before any HTTP response, the next attempt falls back to direct. The fallback is per-request, not per-client — flaky proxies do not poison the client. `ProxyConfig.from_env()` reads `MARKET_ANALYSER_PROXY_HTTP_URL`, `MARKET_ANALYSER_PROXY_HTTPS_URL`, and an optional `MARKET_ANALYSER_PROXY_ROTATION_SESSION_ID` (for Webshare-style sticky sessions); env vars not set → `from_env()` returns `None` and the client operates direct-only.
- **Telemetry.** A counter increments per request, per cache hit, per retry, per final-exhaustion. Counters are exposed via `client.stats() -> HttpClientStats` (pydantic model) so tests can assert behavior and a future observability plan can scrape them. No logging of request bodies or response bodies — only URL paths (query string redacted via the same rules as the sidecar's access log).

**Synchronous, not async.** The Protocol from [ADR-0007](0007-market-data-provider.md) is sync. Two parallel interfaces from day one is over-engineering. When (if) a real concurrency need surfaces (e.g. fan-out screening across hundreds of symbols), we revisit with an `AsyncResilientHttpClient` and a parallel `AsyncMarketDataProvider`.

**Underlying transport: stdlib `urllib.request`.** No new dependency. `urllib.request` is verbose but well-typed and has zero supply-chain surface. The verbosity is hidden inside `ResilientHttpClient`; adapters never see `urllib` directly. If a future plan introduces `httpx` for streaming bodies or HTTP/2 (none of the Tier 2 sources need either), the swap is one-module-internal.

**Retrofit of `YahooAdapter`.** Plan 0009 phase 3 (the cleanup phase) ports `YahooAdapter` onto `ResilientHttpClient`. This is the proof that the abstraction works for a pre-existing adapter; it also clears the only known "two retry policies in the codebase" drift before it spreads.

## Consequences

### Positive

- **One consistent failure model.** Every Tier 2 adapter raises the same exception type on exhaustion, retries the same kinds of errors, and respects the same concurrency budget. Maintainers fixing a bug in one fix it in all.
- **One place to instrument.** Per-source request counters, cache hit-rate, retry counters all live behind `ResilientHttpClient.stats()`. A future observability plan picks up zero-effort metrics.
- **Cost is bounded.** ~250 lines of in-house code plus tests. No new direct dependencies. The cooldown / pinning policies from [ADR-0012](0012-dependency-cooldown.md) and [ADR-0013](0013-pin-direct-dependencies.md) don't add friction.
- **Proxy support is opt-in.** Users without proxies pay nothing. Users with Webshare-style rotating proxies set three env vars and inherit the rotation everywhere.
- **The classifier extension hook (`self.classify`)** lets adapters teach the client about their upstream's quirks without copying retry/backoff logic. StockTwits' 403-as-rate-limit is the canonical example.
- **Synchronous semantics match the Protocol.** No premature async, no double-Protocol burden, no event-loop integration to maintain. The MCP tool surface from Plan 0007's `run_backtest`-shaped tools is already sync.

### Negative

- **Per-instance state.** `ResilientHttpClient` is not a singleton — each adapter constructs its own. That is the intent (each source has its own concurrency and cache budgets) but it means tests setup is heavier: every adapter test that exercises the client mocks the underlying `urllib` transport, not a global service.
- **Stdlib `urllib` is verbose.** Mostly hidden, but the implementation is a few hundred lines longer than a `httpx`-based equivalent. We accept the verbosity in exchange for the zero-dependency property; the trade is reversible if `httpx` justifies its supply-chain weight later.
- **Blocking concurrency cap means slow-fan-out is slow.** Fanning out a 200-symbol screen with `max_concurrency=4` is serialized in batches of 4. This is intentional — the alternative is rate-limit bans — but the slowness is visible at the MCP tool level. The MCP tool's done-when conditions in Plan 0009 phase 2 include a timing assertion so the latency is on the record.
- **The TTL cache is intra-process.** Multiple sidecar restarts within the TTL window each fetch fresh. Acceptable because the sidecar is long-lived per [ADR-0016](0016-standalone-sidecar-mode.md); the SQLite cache from [ADR-0006](0006-persistence-layout.md) is the cross-session story for the data shapes that warrant it (OHLCV bars). Screener/F&G/sentiment snapshots are intentionally fresh-per-restart — they are wall-clock-sensitive in a way bars are not.
- **In-house cache and retry code are a maintenance surface.** A `tenacity` or `cachetools` dependency would shed the surface in exchange for two dependency rows. The cost-benefit went the other way for now; revisit if either piece bloats past ~100 lines.

### Neutral

- **Underscore-prefixed module name (`_http.py`).** Signals "package-internal to `data/`". Downstream code reaches the data layer through the Provider Protocol only; the choice of HTTP client is an implementation detail.
- **No connection pool.** `urllib.request` does not pool by default. For our load (single user, single sidecar), connection re-establishment is dominated by other latencies. Revisit when fan-out screening becomes a hot path.

## Alternatives considered

### Alternative A — Per-adapter inline resilience (status quo, extrapolated)

Each Tier 2 adapter writes its own retry + cache + concurrency code. The Yahoo adapter already does this.

Rejected because four-to-five adapters writing the same logic guarantees drift. The `tradingview-mcp` upstream took this path and ended up with three different retry curves and one source that quietly cached stale data for a week. The avoidance is the entire point of having an ADR here.

### Alternative B — Third-party libraries (`tenacity` + `cachetools`)

Use `tenacity` for retry-with-backoff and `cachetools` for the TTL cache. Both are mature, well-tested, and would shave ~150 lines of in-house code.

Rejected on the dependency-budget grounds from [ADR-0012](0012-dependency-cooldown.md) and [ADR-0013](0013-pin-direct-dependencies.md). The policies are deliberately restrictive — every direct dependency is a supply-chain attack surface and a cooldown-window maintenance burden. The in-house cost is ~80 lines of cache + ~80 lines of retry; for that size, in-house wins. Revisit if the in-house code bloats past ~200 lines combined or if we discover a behavior we cannot easily express (e.g. complex retry decision trees).

### Alternative C — `httpx` as the underlying transport

`httpx` supports HTTP/2, streaming, connection pooling, and has a sync API alongside its async one.

Rejected for v1 because no Tier 2 source needs HTTP/2 or streaming, and `httpx` is a new direct dependency. The classifier + cache + retry layer is transport-agnostic; if a future plan needs `httpx`'s features, the swap is one-module-internal. The decision is reversible.

### Alternative D — Async client from day one

Build `AsyncResilientHttpClient` and run adapter calls in an event loop.

Rejected because the Provider Protocol is sync per [ADR-0007](0007-market-data-provider.md) and the MCP tool surface is sync. Maintaining two parallel interfaces (sync + async) doubles the surface area for zero current benefit. The fan-out concurrency need is also bounded by upstream politeness, which a semaphore-around-threads handles fine. When a real async benefit surfaces (large-scale screening, websocket-bearing sources), we add async then.

### Alternative E — Singleton client (one client across all adapters)

A single `ResilientHttpClient` instance shared by every adapter.

Rejected because each upstream has its own politeness budget, cache lifetime, and quirk-classifier. A shared singleton flattens these into one config; the workaround (per-call overrides) re-introduces the per-adapter customization the singleton was supposed to avoid. Per-instance-per-adapter is the right granularity.

## Notes

- **Yahoo adapter retrofit.** Plan 0009 phase 3 ports `YahooAdapter` onto `ResilientHttpClient`. The done-when asserts the existing OHLCV fetch tests still pass byte-for-byte against a fixture — no observable behavior change for the existing user.
- **Cache key derivation.** For requests with bearer-style auth headers, the cache key must not include the auth header value. The default derivation (`(method, url, sorted(params))`) excludes headers entirely; adapters that need auth-aware caching pass an explicit `cache_key`.
- **Jitter is deterministic for tests.** The client takes an optional `random.Random` instance at construction; tests pass a seeded one to make backoff timings reproducible.
- **The classifier hook is the integration point for adapter-specific quirks.** Documented per-adapter in each plan's risk section: StockTwits 403-as-rate-limit, RSS adapter's accept-anything-content-type rule, TradingView's "no JSON body on 200" edge case.
- **Future ADR territory:** when a paid third-party API key is needed (some news feeds, the CNN F&G), a Secrets-schema ADR (already on the open-ADR backlog) decides where keys live. `ResilientHttpClient` does not own secret storage — it accepts already-injected auth headers from the adapter.
- **The retrofit + the four-new-adapters scope is one ADR.** A separate ADR per adapter would inflate; the resilience module is one decision, the adapters are implementation under it.
