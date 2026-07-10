# ADR-0066 — Short-lived SSE ticket instead of the durable bearer in the event-stream URL

> **Status:** accepted
> **Date:** 2026-07-09
> **Related plan(s):** 0072-codebase-remediation-audit-2026-07
> **Related ADRs:** refines the renderer-bearer transport of [0002](0002-ipc-local-http.md) / [0011](0011-bearer-secret-transport.md) for the one endpoint — the SSE stream of [0017](0017-live-ui-updates-via-sse.md) — that cannot carry a header

## Context

The renderer subscribes to `GET /events` via the browser `EventSource` API, which **cannot set request headers** — a documented limitation, noted both in [ADR-0017](0017-live-ui-updates-via-sse.md) and in the client:

```
// desktop/renderer/api/client.ts:339
// The renderer bearer must be passed as `?token=` because `EventSource` cannot
//   set an Authorization header …
// client.ts:346 → `…/events?token=${encodeURIComponent(secretToken)}`
```

The server accepts it there (`api/app.py`: for `/events` only, `?token=<bearer>` is honored). The value placed in the URL is the **durable per-launch renderer bearer** — the same full-power credential that gates every other renderer route.

URL-embedded secrets are structurally more exposure-prone than header-borne ones: they surface in process/heap memory, the `Referer` header, and any future access log, crash dump, or reverse-proxy trace. Today the exposure is bounded by `access_log=False`, loopback-only binding, and no proxy in the path — but the thing in the URL is the **whole** bearer, so a single leaked `/events` URL is a complete renderer-tenant credential, not a scoped one. This is a latent, not an active, exposure; the decision hardens the mechanism before a future logging/proxy change makes it active.

## Decision

We will mint a **short-lived, single-use SSE ticket** and put the *ticket* — never the durable bearer — in the `/events` query string.

- The renderer `POST`s to a new **bearer-gated** endpoint (bearer in the `Authorization` header, as normal) to exchange its bearer for an opaque, short-TTL, one-time ticket.
- It then opens `EventSource('/events?ticket=<ticket>')`.
- The server validates and **consumes** the ticket (single use) to authorize the stream; an absent, unknown, expired, or already-used ticket is rejected `401`.
- Ticket TTL is on the order of seconds — long enough to open the stream, short enough that a leaked URL is worthless almost immediately. Tickets are minted only for an already-authenticated renderer and held in an in-memory, TTL-swept store (no persistence — they die with the process, like the bearer).
- `EventSource` auto-reconnect must re-mint: the renderer wraps reconnection to fetch a fresh ticket before reopening.

The durable bearer never appears in a URL again.

## Consequences

- **Positive — the full-power bearer leaves the URL surface entirely.** A leaked `/events` URL yields at most an expired, single-use ticket.
- **Positive — scoped blast radius.** A ticket authorizes exactly one stream open, briefly, versus a credential good for every route indefinitely.
- **Positive — ADR-0017's `EventSource` choice is preserved.** No custom headers, no polyfill; the native API stays.
- **Negative — more moving parts.** A mint endpoint, an in-memory ticket store with TTL sweep, and a reconnection wrapper that re-mints. A static token needs none of that.
- **Negative — the trust root is unchanged.** The mint endpoint is itself bearer-gated, so the bearer is still the root credential; this narrows *URL* exposure, it does not add a new trust boundary. Judged worth it precisely because URLs are the leak-prone surface.

## Alternatives considered

- **Status quo — durable bearer in the URL.** Rejected: acceptable only because of `access_log=False` + loopback today; brittle to any future logging/proxy, and the credential is full-power rather than scoped.
- **A custom `EventSource` polyfill that sets an `Authorization` header** (fetch + `ReadableStream`). Rejected: it reimplements `EventSource`'s reconnection/backoff semantics in renderer code — more complexity and more risk than a ticket exchange, and it abandons the well-tested native API ADR-0017 deliberately chose.
- **Move the event stream onto a fetch-streaming transport with headers.** Rejected: a large change to the whole SSE stack for a minor-severity, currently-latent exposure — out of proportion to the risk.
