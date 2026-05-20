# ADR-0017 — Live UI updates via SSE event stream

> **Status:** accepted
> **Date:** 2026-05-20
> **Related plan(s):** [0007-live-agent-driven-viewer](../plans/0007-live-agent-driven-viewer.md), [0006-annotations-via-mcp](../plans/done/0006-annotations-via-mcp.md) (closes deferred SSE follow-up)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md) (renderer ↔ sidecar transport), [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (dual-bearer middleware applies), [ADR-0015](0015-claude-code-primary-control-surface.md) (motivates this)

## Context

[Plan 0006](../plans/done/0006-annotations-via-mcp.md) chose 1 Hz polling for the renderer's annotation refresh and explicitly deferred push:

> Polling at 1 Hz is the MVP refresh mechanism. Real-time push is a follow-up if and when Plan B's higher-frequency write patterns make polling lossy. The current code structure (a single `useAnnotationsPoll` hook) is the seam where that change lands.

[ADR-0015](0015-claude-code-primary-control-surface.md) (role inversion: Claude Code primary, Electron viewer) makes polling insufficient for the dominant interaction pattern. The motivating flow is:

1. User to Claude: "visualize an AAPL setup with EMA20 and a 30-day window"
2. Agent calls `show_chart(symbol="AAPL", timeframe="1d", range=..., overlays=[{kind:"ema", period:20}])`
3. The viewer renders.
4. User to Claude: "now add EMA50 and zoom to the last 10 days"
5. Agent calls `update_chart(overlays=[..., {kind:"ema", period:50}], range=...)`
6. The viewer updates in place.

This loop has three properties polling cannot serve well:

- **Sub-second perceived latency.** 1 Hz polling adds ~500 ms average latency before each render. For conversational tweaks this reads as laggy. Pushing the cadence to 200 ms wastes bandwidth at idle (which is most of the time) and still buys only a 100 ms average win.
- **Ephemeral commands have no polled representation.** "Focus on bar 47", "blink this candle for 2 seconds", "show this overlay temporarily" are *instructions*, not *state changes*. Polling fetches state; instructions need delivery.
- **The agent expects acknowledgement.** An MCP tool call returns immediately; the agent's next step depends on knowing the prior render landed. Polling does not give the agent any signal about the renderer's state.

Three transport choices were genuinely in contention:

- **Server-Sent Events (SSE).** Server→client one-way over HTTP, simple reconnection (`Last-Event-ID`), supported natively by `EventSource` in Chromium, simple to implement in FastAPI (an async generator returning `EventSourceResponse` from `sse-starlette` or hand-rolled).
- **WebSocket.** Bidirectional over HTTP-upgrade, more code on both ends, supports custom headers in the initial handshake (which makes bearer auth easier than SSE's header limitation).
- **HTTP long-poll.** Server→client over HTTP, server holds the request open until an event is available, client reconnects after each event. Pre-HTML5 fallback pattern; no real reason to choose it today.

Two schema-discipline choices were also in contention:

- **Free-form JSON envelopes** (`{type, payload}`) — fastest to write, hardest to evolve safely.
- **Versioned typed envelopes** (`{type, version, ts, payload}` with per-type Pydantic models) — slightly more ceremony, schema evolution stays additive without breakage.

The decision is non-obvious because SSE has a real Achilles heel — `EventSource` cannot send custom request headers — and because the event schema decision sets the cost of every future event-type change.

## Decision

We will add a **Server-Sent Events** stream from the sidecar to the renderer at `GET /events`, gated by the renderer bearer (the same middleware that gates `/ohlcv`, `/annotations`, etc.). The renderer opens one persistent SSE connection on app boot and dispatches events to handlers by `type`.

Events use **versioned typed envelopes**:

```python
class Envelope(BaseModel):
    type: str         # e.g. "chart.show", "chart.update", "run.completed"
    version: int      # per-type version; starts at 1; bumped on breaking changes
    ts: datetime      # UTC, ms precision, set at publish time
    payload: dict     # type-specific; validated against a per-type Pydantic model at publish AND at handler
```

Per-type payload models live alongside the type registration in `src/market_analyser/api/events/`. The publish path validates the payload against the model; an unrecognised type or a payload that fails validation raises at publish time and the bug is the sidecar's, not the renderer's. The renderer-side TS types are generated from the Python models the same way `/ohlcv`'s types are.

**Initial event vocabulary** (each at version 1; vocabulary grows monotonically as MCP tools are added):

- `chart.show v1` — render this chart. Payload: `{symbol, timeframe, range_start, range_end, overlays?}`.
- `chart.update v1` — apply delta to the chart matching `symbol + timeframe`. Payload: `{symbol, timeframe, overlays?, range_start?, range_end?, focus_bar?}`. If no matching chart is open, the renderer treats it as `chart.show` with the available fields.
- `chart.highlight v1` — render markers on the chart matching `symbol + timeframe`. Payload: `{symbol, timeframe, markers: [{event_ts, kind, label?}]}`. Persistent highlights also write to the annotations table from Plan 0006; ephemeral ones do not.
- `run.completed v1` — a backtest or analysis artifact is ready. Payload: `{kind: "backtest" | "analysis" | "defi", run_id, artifact_path}`. Consumer (renderer routing to a results view) lands when the backtester ships; the event is defined here so the contract is fixed.

**Bearer transport for `EventSource`.** `EventSource` cannot send a custom `Authorization` header. The sidecar accepts the renderer bearer from a query parameter on the `/events` route only (`GET /events?token=<bearer>`), and only on `/events`. The route's access log is suppressed (already the sidecar's default per `uvicorn.Config(access_log=False)`) so the bearer does not land in stdout. This is the standard SSE-with-bearer workaround; the alternative (use `fetch` + `ReadableStream` to consume the SSE protocol manually, which does support headers) is a viable upgrade path but adds complexity we defer.

**Reconnection.** The renderer uses the browser's built-in `EventSource` reconnection (the `retry:` field on server-sent events sets the backoff; the sidecar issues `retry: 5000`). The connection re-authenticates with the same bearer in the query on every reconnect. Events are **ephemeral** — no replay history is kept. If the renderer disconnects during an event burst, those events are lost; the renderer reconciles state from artifacts (SQLite rows for annotations, `runs/` files for completed runs) on next mount.

**Heartbeat.** The sidecar sends a `: ping` comment line every 15 s on each open stream to defeat intermediate proxies that might close idle connections. (Loopback-only deployment makes this nearly cosmetic, but it costs nothing and aids debugging.)

**Backpressure.** Each subscriber has a bounded `asyncio.Queue` (cap 256 envelopes). If the queue is full when the sidecar publishes, the **oldest** envelope is dropped and a `chart.update_dropped v1` synthetic envelope is enqueued so the renderer knows to reconcile. The cap is generous for the MVP load (single user, single viewer, agent producing low-single-digit events per second); revisit if backtest-progress events at high rate land.

**Cross-tenant isolation.** The MCP bearer (`mcp-secret.json`, per ADR-0014) must NOT authenticate against `/events`. The renderer bearer (per-sidecar-launch, persisted in `sidecar.lock` per ADR-0016) must NOT authenticate against `/mcp`. The existing dual-bearer middleware design extends to `/events` with no structural change — just one more renderer-bearer-gated route.

## Consequences

### Positive

- **Conversational tweaks render in single-digit milliseconds.** "Now add EMA50" → the renderer paints the overlay before the user finishes processing the agent's text reply.
- **Ephemeral instructions become first-class.** Commands that don't make sense as persisted state (focus, zoom, transient overlay) flow through the same path as render commands.
- **Reconnection is built-in.** `EventSource` handles drops; no retry logic to write or maintain.
- **Lower idle load than polling.** When nothing is happening, zero traffic flows. The 15 s heartbeat is the only steady cost.
- **The event vocabulary is the contract.** Adding a new MCP tool that wants to render involves defining a new envelope type and a new renderer handler — both versioned. Future maintainers see exactly what was promised and when.
- **`run.completed` becomes the wire for backtester UI** when that ships, without retroactive design.

### Negative

- **Bearer in URL query is a regression from header-only.** `EventSource`'s lack of custom headers forces the bearer onto the query string. Mitigations: access log suppressed; bearer rotated per sidecar launch (ADR-0016); the alternative (`fetch` + `ReadableStream`) is a documented upgrade path if the query-string leakage becomes a finding (e.g. via a third-party process inspecting the network namespace on Linux).
- **Events are ephemeral.** A renderer that's closed when a backtest completes loses the `run.completed` event. The artifact under `runs/` is the durable record; the renderer reconciles on mount. This is acceptable — the user can re-open the viewer and the agent can re-issue `show_*` commands. It is not acceptable as a notification mechanism for "the agent just finished a 20-minute backtest while you weren't looking"; that's a future feature (system notifications, persistent inbox) and not in scope here.
- **Versioning discipline is new ceremony.** Each event type carries an integer version. Bumping it is a multi-step migration: the sidecar publishes both versions for a transition window; the renderer accepts both; the old version is dropped in a follow-up. We accept this as the price of safe schema evolution. The cost is a few lines per migration, not architectural.
- **Backpressure is bounded but lossy.** The drop-oldest behaviour means a burst of `chart.update` events could lose intermediate frames. We accept this for MVP (the final frame is what matters for the user; the dropped intermediates are just animation steps). If backtest-progress events at high rate become a use case, a per-type policy (drop vs coalesce vs replace-by-key) is a future refinement.
- **No mechanism to push to the agent.** Events flow sidecar→renderer only. If the agent needs to know about a renderer-side state change (e.g. "user just dragged the chart to a new range"), there is no path. We accept this — the agent-side control of the conversation is the design intent; renderer→agent feedback is a future WebSocket plan if needed.

### Neutral

- **The annotation polling from Plan 0006 is unchanged in MVP.** `useAnnotationsPoll` continues to fetch `/annotations` at 1 Hz. The natural follow-up — convert annotations to a `chart.highlight` subscriber — is straightforward but not blocking on this ADR and not in scope of Plan 0007. It can be a small cleanup PR or a dedicated phase in a later plan.
- **The dual-bearer middleware design extends naturally.** `/events` is one more renderer-bearer-gated route; `/mcp` is unchanged. The cross-tenant assertion shape is the same as Plan 0006's done-when conditions.
- **The renderer's existing connection-loss handling already covers SSE drops.** The viewer is already prepared to show a "sidecar unreachable" state from prior plans; SSE disconnection is a special case that re-uses that surface.

## Alternatives considered

### Alternative A — WebSocket

Bidirectional, supports headers in the upgrade handshake (cleaner bearer auth), well-supported in Electron/Chromium.

Rejected because we don't need bidirectional traffic in MVP — the agent writes via MCP, the renderer reads via HTTP, only push needs to be added. SSE matches the directionality exactly with less code. WebSocket becomes attractive if/when the renderer needs to publish events back to the sidecar; a future ADR can promote it. Migrating from SSE→WebSocket later is a viable path (the event-envelope schema is transport-agnostic).

### Alternative B — HTTP long-poll

Reject because the reconnect-on-every-event semantics fight the rapid-dialog use case, the code we'd write is what SSE gives us for free, and the only reason to choose it over SSE is `EventSource`-unavailable environments — which we are not in.

### Alternative C — Keep polling, increase cadence

Bump the existing annotation poll to 200 ms and add new poll loops for chart state.

Rejected because the bandwidth waste at idle is significant (5 req/s × N renderers × always), ephemeral commands still have no representation, and the agent has no acknowledgement signal. Polling is a state-sync primitive, not a command-delivery primitive — the role-inversion needs the latter.

### Alternative D — Free-form (untyped) JSON envelopes

`{type: "chart.show", payload: {…}}` with no version field; renderer interprets ad-hoc.

Rejected because a breaking change to a payload shape would silently break renderers in the field with no clear migration path. Versioning is one int field of ceremony for a permanent safety property; cheap.

### Alternative E — Push events through the agent's MCP session

MCP supports server-to-client notifications on the transport. The sidecar could push render commands back through the agent's MCP connection, and the agent could forward them to the renderer.

Rejected because the renderer is the consumer of these events, not the agent. Routing through the agent's MCP connection is a topology pretzel: agent → sidecar → MCP-notification → agent → ??? → renderer. The direct sidecar→renderer path is shorter, simpler, and doesn't require the agent to be a router. MCP server-to-client notifications remain available for agent-facing notifications (e.g. progress updates on long-running tools); we just don't repurpose them as a renderer transport.

## Notes

- The `sse-starlette` package is the typical FastAPI integration; whether to take a new direct dependency or hand-roll `EventSourceResponse` is decided in Plan 0007 phase 2, not here. The dependency cooldown policy in [ADR-0012](0012-dependency-cooldown.md) applies if `sse-starlette` is chosen.
- The `?token=<bearer>` workaround mirrors the standard SSE+auth pattern in client-side environments without custom-header support. The `fetch` + `ReadableStream` alternative is more code on the renderer side (manual SSE parsing) but avoids the query-string leak; it is a documented upgrade path, not a v1 requirement.
- The `chart.highlight` envelope overlaps semantically with Plan 0006's `write_annotation` MCP tool: both render markers on a chart. The distinction is *persistence*. Annotations live in SQLite and survive restart; `chart.highlight` events are ephemeral by default. The MCP tool surface in Plan 0007 (`highlight_pattern`) emits both — persists via the annotations table AND publishes the live event — so the agent doesn't have to make the persistence call twice.
- An MCP server-to-client notification (e.g. `tools/list_changed`) is unrelated to this event stream. The MCP transport's own notifications stay scoped to the MCP-client conversation; renderer events are a separate concern.
