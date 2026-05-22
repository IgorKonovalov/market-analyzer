# ADR-0021 — Renderer→agent feedback via MCP resources + notifications, gated by agent-mode toggle

> **Status:** proposed
> **Date:** 2026-05-22
> **Related plan(s):** [0014-interactive-chart-and-agent-mode](../plans/0014-interactive-chart-and-agent-mode.md)
> **Related ADRs:** [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (MCP is the agent surface; Streamable HTTP transport), [ADR-0015](0015-claude-code-primary-control-surface.md) (Claude Code is the primary control surface), [ADR-0017](0017-live-ui-updates-via-sse.md) (sidecar→renderer is SSE — explicitly noted that renderer→agent was *not* designed and would need its own ADR), [ADR-0002](0002-ipc-local-http.md) (renderer ↔ sidecar transport; bearer auth pattern)

## Context

[ADR-0017](0017-live-ui-updates-via-sse.md) intentionally left renderer→agent feedback out of scope: "Events flow sidecar→renderer only. … We accept this — the agent-side control of the conversation is the design intent; renderer→agent feedback is a future WebSocket plan if needed." Plan 0007's close ceremony (2026-05-22) recorded a concrete user demand for that path: the user wants to drag-to-select a date range on the chart, click on individual candles to "ask the agent about this bar", and flip an *agent mode* on/off so they decide when their UI gestures are visible to the agent. The work was scoped out as [Plan 0014](../plans/0014-interactive-chart-and-agent-mode.md), with this ADR as the gating decision.

Three forces shape the choice:

- **Direction matters for transport.** The existing infrastructure is asymmetric on purpose: the sidecar pushes to the renderer via SSE (ADR-0017); the renderer fetches from the sidecar via authenticated HTTP. Adding a new "renderer→agent" axis is not the same problem as "renderer→sidecar". The renderer can already POST to the sidecar over the existing HTTP transport with the renderer bearer. The *new* problem is the second leg: sidecar→agent. The agent reaches the sidecar via the MCP Streamable HTTP transport (ADR-0014); there is no obvious "agent gets pushed to" channel in MCP that's universally supported by clients today.
- **MCP client capabilities are uneven.** The MCP spec defines server-to-client notifications (e.g. `notifications/tools/list_changed`, `notifications/resources/updated`) on the Streamable HTTP transport. Whether a given MCP client *surfaces* those notifications to the model (vs just consuming them internally) is implementation-defined. Claude Code (the primary control surface per ADR-0015) is the target client; its behaviour around server-initiated content notifications is the gating uncertainty. A design that relies on push-only fails when the client doesn't propagate the notification to the model.
- **The user wants explicit consent.** "Agent mode" is a UX requirement: the user does *not* want pan/zoom and click gestures silently broadcast to the agent. The default is OFF; the user toggles it ON when they want a feedback loop. This shapes both the renderer (visible toggle, persisted state) and the sidecar (server-side enforcement so a buggy or malicious renderer can't bypass the toggle).

Four mechanisms were genuinely in contention for the sidecar→agent leg:

- **MCP resources + `notifications/resources/updated`.** Model UI events as a server-side resource the agent can read on demand; emit a resource-update notification when the buffer grows. The agent's MCP client subscribes to resource updates and, if it surfaces them, the model is told to consider re-reading the resource. Falls back gracefully — even without notification surfacing, the agent can poll the resource (or a sibling tool that drains the same buffer).
- **Custom MCP notification method.** The sidecar emits a notification like `notifications/market-analyser/ui_event` carrying the payload inline. Most MCP clients ignore namespaced custom methods; not robust.
- **Blocking wait tool.** A tool `wait_for_ui_event(timeout_seconds)` that the agent calls when it expects a gesture. Pins an MCP session slot for the duration of the wait; agent UX depends on the agent remembering to call it. Workable as a complement but not a primary mechanism.
- **WebSocket bidirectional channel.** A new full-duplex socket between renderer and agent (via the sidecar). Highest mechanism cost; the renderer doesn't talk to the agent directly anyway, so the "WebSocket from agent to renderer" topology is exactly what ADR-0017 Alternative E rejected as "a topology pretzel".

The cost asymmetry between these is large. WebSocket means a new transport across the whole stack; resources+notifications layers onto what already exists in MCP.

## Decision

We will surface renderer-originated UI events to the agent via **two complementary MCP affordances on the existing Streamable HTTP transport**: an **MCP tool** `get_pending_ui_events(since=None, drain=True)` that returns the buffered events synchronously (the reliable mechanism), and an **MCP resource** `ui-events://recent` whose contents are the same buffer, with `notifications/resources/updated` published on every buffer append (the best-effort, low-latency mechanism for clients that surface resource updates to the model). The agent reads whichever it has confidence in; both drain the same in-memory ring buffer in the sidecar.

The renderer→sidecar leg uses the existing HTTP transport. A new renderer-bearer-gated route `POST /ui_events` accepts a typed envelope (`ui.range_selected v1` | `ui.bar_clicked v1` | `ui.agent_mode_toggled v1`) and appends to the buffer. Server-side enforcement: the route rejects with 403 when **agent mode is OFF** (defence in depth — the renderer shouldn't be POSTing in that case anyway, but a buggy renderer or a hostile process with the renderer bearer can't bypass the user's choice).

**Agent mode** is sidecar-resident state, persisted to `<data-dir>/agent_mode.json` (mode 0600 on POSIX). Two new renderer-bearer-gated routes: `GET /agent_mode` returns `{enabled: bool}`, `PUT /agent_mode` sets it (body: `{enabled: bool}`). The renderer's chart header shows a toggle button reflecting the current state; flipping it PUTs and persists. Default is OFF (no events buffered, no notifications fired). The toggle action itself synthesises a `ui.agent_mode_toggled v1` event into the buffer (so an agent watching the resource sees the user opt in or out).

**The buffer** is a bounded `collections.deque(maxlen=100)` of typed UI-event envelopes (`{type, version, ts, payload}` — same envelope shape as ADR-0017 for consistency, with `event_id: str` added so the agent can dedupe across reads). Drop-oldest on overflow. Empty when agent mode is OFF (or whenever a fresh sidecar boots — no persistence; UI events are ephemeral). The buffer is in-memory; sidecar restart clears it.

**The MCP resource** is implemented via FastMCP's `@server.resource("ui-events://recent")` decorator. `resources/read` returns the current buffer as JSON. `resources/list` includes the resource so clients that enumerate available resources find it. **Resource-update notifications**: every successful append to the buffer fires `notifications/resources/updated` for the `ui-events://recent` URI via the FastMCP server's notification API. The client may or may not surface the notification to the model; the tool path is the contract that holds either way.

**The MCP tool** `get_pending_ui_events(since=None, drain=True)`: returns events from the buffer with timestamp `> since` (or all when `since=None`); if `drain=True` (default), removes the returned events from the buffer. Agent loop is: call the tool when ready to process; tool returns 0..N events; agent acts; repeats. With `drain=False`, the tool returns events without consuming them — useful for an agent that wants to peek before deciding to act.

**Single-instance enforcement for the Electron viewer.** This decision implicitly reverses Plan 0007's stated "two viewers OK" allowance, because agent-mode state is sidecar-resident and a single toggle applies to all viewers. Electron's `app.requestSingleInstanceLock()` is the standard mechanism. Second-instance launches focus the existing window and exit. Plan 0014 phase 3 lands this change.

**What this is not.** This ADR does not introduce a WebSocket. It does not introduce a new MCP transport. It does not introduce a custom MCP method outside the `resources` / `notifications/resources/*` standard namespace. The novelty is entirely in *which MCP-standard affordances we use to model UI events*, not in inventing new wire-level mechanisms.

## Consequences

### Positive

- **No new transport.** The renderer→sidecar leg reuses the existing authenticated HTTP. The sidecar→agent leg reuses MCP's existing Streamable HTTP. Wire-level we add zero new sockets.
- **The polling tool is the contract.** Whatever the client does or doesn't do with notifications, the agent can always call `get_pending_ui_events()` and get the buffer. Push is opportunistic.
- **Agent-mode toggle is a single source of truth.** Sidecar-resident state means every viewer, every agent, every probe sees the same state. The toggle button in any viewer's chart header reflects (and writes) the same boolean.
- **Buffer ephemerality matches user expectation.** UI gestures are now-things. The user dragging a range *now* shouldn't be queued for an agent that connects three days later. Buffer caps at 100 events and drops oldest; sidecar restart clears it. No SQLite, no migrations.
- **Bidirectional MCP via standard affordances.** Future renderer-originated signals (a "show me details for this trade" click on a backtest's trade-log row, for example) plug into the same buffer + same resource + same tool. Vocabulary grows; mechanism doesn't.
- **Server-side enforcement of agent mode.** A buggy renderer that POSTs without checking the toggle gets a 403. The user's consent is enforced at the seam, not just trusted at the source.

### Negative

- **Notification surfacing depends on the MCP client.** The "push" half of the design is best-effort. If Claude Code (or a future MCP client) doesn't surface `notifications/resources/updated` to the model in a way that triggers a tool call, the latency falls back to "agent decides to poll" — which may be never. The agent's documentation has to spell this out: "if you've enabled agent mode and want to react to user gestures, call `get_pending_ui_events()` periodically." This is a real UX cost; we accept it because the alternatives (WebSocket, blocking tool, custom method) cost more.
- **Bounded buffer can lose events.** At cap 100 with drop-oldest, a fast user (rapid bar clicks) outpacing a slow agent (slow polling cadence) loses the oldest gestures. We accept this — UI events are best-effort; the agent doesn't need byte-exact replay of every drag. If lossiness becomes a complaint, the cap is tunable without an ADR change.
- **Single-instance is a behaviour change.** Plan 0007 explicitly allowed two viewers. We're closing that allowance now. Cost: a small UX surprise for anyone who got used to "open another window". Mitigation: the second-instance launch focuses the existing window; the user gesture is still acknowledged.
- **`agent_mode.json` is one more file in the data dir.** Tiny (one key), but it joins `sidecar.lock`, `mcp-secret.json`, the SQLite file, and the lockfile temp file. The data-dir contract from ADR-0020 covers it (the file lives at `<data-dir>/agent_mode.json`); we extend the data-dir docs accordingly.
- **The agent has to learn three new things.** A tool, a resource, and a notification-surfacing assumption. Mitigations: the tool's docstring spells it out; the resource's documentation field describes the same. The agent's mental model is "if you want to know about UI gestures, call the tool when agent_mode is on".
- **Resource semantics blur "state" and "stream".** Strictly, MCP resources are meant for stable state the agent can re-read deterministically. We're using them as a draining buffer; two consecutive reads with `drain=True` won't return the same thing. We accept the mild semantic stretch; the alternative (custom MCP method or a non-MCP affordance) is worse on every other axis. The resource's description field explicitly says "this is a draining buffer; consecutive reads return disjoint sets".

### Neutral

- **The renderer's UI gesture detection** (range-select drag, bar click, mode-toggle button) is straightforward `lightweight-charts` API + plain React. No new dependencies.
- **The buffer's envelope shape mirrors ADR-0017's** (`type, version, ts, payload`, plus an `event_id` for dedup). Familiar to anyone who's read the sidecar→renderer side.

## Alternatives considered

### Alternative A — Custom MCP notification method (`notifications/market-analyser/ui_event`)

The sidecar emits a non-standard notification carrying the UI-event payload inline. The agent's client either understands it or doesn't.

Rejected because the MCP spec's standard namespace covers what we need (resources + their update notifications) and clients implement the standard surface. Custom methods are likely to be silently ignored by most clients (they only handle methods they know about). Worse, custom methods make us non-portable: an MCP server should look like an MCP server.

### Alternative B — Blocking `wait_for_ui_event(timeout)` tool

A tool the agent calls when it expects a gesture; the tool blocks server-side until a buffer append happens or the timeout fires.

Rejected as the *primary* mechanism because it pins an MCP session slot for each outstanding wait, the agent has to actively choose to wait (vs the resource-update path that nudges the agent without an outstanding call), and the agent's MCP client may have its own timeout shorter than the server's. We keep it on the table as a follow-up if the polling + notifications design proves too slow in practice; adding it later is additive (new tool, no schema change).

### Alternative C — WebSocket between renderer and sidecar (or renderer and agent directly)

Full-duplex socket; renderer pushes events, agent subscribes.

Rejected because (1) the renderer→sidecar leg is already a one-way HTTP POST that doesn't need WebSocket; (2) the renderer→agent direct path is a topology pretzel (the agent talks to the sidecar via MCP, not to the renderer directly); (3) adding a WebSocket means a new transport, new auth surface, new lifecycle handling, new disconnect/reconnect logic, new tests. The cost dwarfs the benefit. ADR-0017 Alternative A already rejected WebSocket for the *forward* direction; the reverse direction has even less reason to take it.

### Alternative D — Persistent buffer (SQLite-backed)

UI events persist across sidecar restarts; the agent can replay history days later.

Rejected because UI gestures are ephemeral by nature — "the user clicked this bar at 14:32:01" is uninteresting at 19:00 the next day. Persistence adds a migration, a table, a retention policy, an index, and a clearance UX. The buffer's in-memory model with drop-oldest matches the data's actual lifespan. If a future use case demands replay (recorded user-study sessions, support diagnostics), it can layer on without changing this ADR.

### Alternative E — File-watching as the agent-side mechanism

The renderer (or sidecar) writes UI events to a file in the data dir; the agent's MCP client watches the file and surfaces changes.

Rejected because file-watching is fragile across platforms (inotify on Linux, FSEvents on macOS, ReadDirectoryChangesW on Windows), MCP clients don't generally expose a "watch this file" affordance to the model, and the same problem (client-side surfacing) afflicts file-based events as much as MCP notifications. We'd be adding a worse-supported mechanism for the same gating uncertainty.

## Notes

- The MCP spec's resource model is the natural fit even though its idiomatic use is for *stable* resources (a file, a database row, a piece of documentation). Our use case stretches that semantic slightly — `ui-events://recent` is more like a queue than a noun. The pragmatic argument: the existing standard affordances are what clients support; the alternative is a non-standard mechanism that no client supports. We accept the stretch.
- The buffer's cap (100) is a guess. Real usage will tell us whether 10 or 1000 is right. The cap is a configurable constant in the buffer module; tuning it does not require an ADR change.
- FastMCP's notification surface: as of the SDK version pinned by the current `pyproject.toml`, the server-initiated notification API is reachable via `Context.report_progress(...)` (for tool-call progress) and `server.session_manager` lifecycle hooks. For a buffer-update notification we need a path that fires *outside* an active tool call. Plan 0014 phase 2 will pin down the exact API and document it in the phase commit message — if the SDK pinned version doesn't expose what we need, the dep bump goes through the cooldown policy (ADR-0012) and lands in a single follow-up commit.
- Plan 0014 also lands the *single-instance* viewer change. ADR-0008 (Electron shell conventions) does not currently mandate single-vs-multi-instance; we are choosing single-instance here because agent-mode is a single state. If a future user actually wants two viewers, the right move is to make agent-mode per-viewer — a much bigger redesign — not to revert single-instance.
- The renderer's UI-event POST uses the **renderer bearer** (per-launch, in `sidecar.lock`). The MCP tool and resource use the **MCP bearer** (long-lived, in `mcp-secret.json`). Cross-tenant isolation is preserved: the agent's bearer cannot POST `/ui_events`, and the renderer's bearer cannot read `/mcp`. The dual-bearer middleware design from ADR-0014 carries through unchanged.
