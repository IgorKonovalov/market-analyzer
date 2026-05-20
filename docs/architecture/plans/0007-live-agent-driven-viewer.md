# 0007 — Live agent-driven viewer: standalone sidecar + SSE + `show_*` tools

> **Status:** in-progress
> **Created:** 2026-05-20
> **Approved:** 2026-05-20
> **Owner skill(s):** `dev`, `ui-builder`, `human`
> **Related ADRs:** [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) (role inversion), [ADR-0016](../adrs/0016-standalone-sidecar-mode.md) (lockfile + idempotent attach), [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md) (SSE event stream), [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md) (MCP foundation this builds on), [ADR-0011](../adrs/0011-bearer-secret-transport.md) (per-launch bearer — refined by ADR-0016), [ADR-0002](../adrs/0002-ipc-local-http.md) (renderer transport)

## TL;DR

Detach the sidecar from Electron's process tree (lockfile-based single-instance + idempotent attach), add an SSE event stream from the sidecar to the renderer, and ship three new MCP tools (`show_chart`, `update_chart`, `highlight_pattern`) that publish typed envelopes to the stream. Then wire the renderer to consume those envelopes and render live. The first user-visible behavior at the end of the plan: open Claude Code, ask "show me AAPL daily with EMA20", see the chart appear in the Electron viewer within a second; ask "now add EMA50 and zoom to the last 30 days", see the chart update live.

## Context & problem

[ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) declared Claude Code the primary control surface and Electron the live viewer. That decision is empty until two things are true:

1. The sidecar runs without Electron (so the agent's reach doesn't depend on a UI window being open).
2. The renderer reacts in real time when the agent issues a render command (so the conversational tweak loop feels live, not delayed by 500 ms polling).

[ADR-0016](../adrs/0016-standalone-sidecar-mode.md) and [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md) set the mechanism for each. This plan is the execution.

The motivating end-to-end loop is the test we are designing for:

```
User → Claude: "show me AAPL daily with EMA20"
Claude → MCP: show_chart(symbol="AAPL", timeframe="1d", range_start=..., range_end=..., overlays=[{kind:"ema", period:20}])
sidecar publishes chart.show v1 envelope to event bus
Electron renderer (subscribed via EventSource on /events) receives the envelope, dispatches to chart.show handler
Renderer mounts/switches the OhlcvView to AAPL 1d, renders candles + EMA20 overlay
User → Claude: "now add EMA50 and zoom to the last 30 days"
Claude → MCP: update_chart(symbol="AAPL", timeframe="1d", overlays=[..., {kind:"ema", period:50}], range_start=<-30d>, range_end=<today>)
sidecar publishes chart.update v1 envelope
Renderer's chart.update handler adds the EMA50 line, narrows the visible range
```

Everything outside this loop is out of scope here. The `run.completed` event type is defined in ADR-0017 so the schema is fixed when the backtester ships, but no `run.completed`-producing tools are added in this plan.

## Decision

Five phases, ordered so the first one (standalone sidecar + lockfile) is independently valuable (Claude Code can drive workflows even before SSE lands — events just have no subscriber, the agent's MCP calls still complete and persist data). Phases 1–3 are `dev`-owned and run as a contiguous block in one session. Phase 4 is `ui-builder`-owned — handoff at the dev↔ui-builder boundary via the [cross-skill handoff protocol](../../../.claude/skills/architect/references/templates/cross-skill-handoff.md). Phase 5 is `human` — user-only smoke and config; the implementer stops at the boundary and surfaces it.

We rejected two alternatives at planning time: (a) "bundle everything into one phase so Electron and sidecar land together" — rejected because the standalone-sidecar change touches process supervision and is testable on its own without UI work, and a one-commit-everything plan loses the integration check at phase boundaries; (b) "ship `show_chart` first, then standalone mode" — rejected because the `show_chart` tool's value is the live render in Electron, which requires SSE which requires phase 2 which is much easier with phase 1's lifecycle decoupling already in place.

## Architecture diagram

```mermaid
flowchart LR
    subgraph CLI["Claude Code"]
        Agent["Agent + MCP client"]
    end

    subgraph Sidecar["Python sidecar (standalone)"]
        MCP["/mcp Streamable HTTP"]
        ShowTools["show_chart<br/>update_chart<br/>highlight_pattern"]
        ExistingTools["get_ohlcv<br/>list/write_annotation<br/>(Plan 0006)"]
        Bus["asyncio event bus<br/>(per-subscriber bounded queue)"]
        SSE["GET /events (SSE)<br/>?token=&lt;renderer_bearer&gt;"]
        Lockfile[("sidecar.lock<br/>0600, written on boot")]
        DL["repositories +<br/>MarketDataProvider"]
        Cache[("SQLite")]
        MCP --> ShowTools
        MCP --> ExistingTools
        ShowTools --> Bus
        ShowTools --> DL
        ExistingTools --> DL
        Bus --> SSE
        DL --> Cache
        Sidecar -.writes on boot.-> Lockfile
    end

    subgraph Viewer["Electron viewer (attaches or spawns)"]
        Main["Main process"]
        Renderer["Renderer<br/>useEventStream → handlers"]
        Main --> Renderer
    end

    Agent -- "MCP bearer" --> MCP
    Renderer -- "EventSource" --> SSE
    Renderer -- "HTTP renderer bearer" --> DL
    Main -. "read lockfile,<br/>attach or spawn" .-> Lockfile
```

The seam between MCP tools and the renderer is the in-process event bus — exactly the seam that ADR-0017 sets up. The dotted arrows are lifecycle (lockfile attach), the solid arrows are runtime traffic.

## Implementation phases

Each phase is one commit. Owner tags hand off between `dev`, `ui-builder`, and `human`. Done-when conditions name the behavioral claim each test defends, not the file paths — the implementer picks paths consistent with the existing codebase ([`feedback_tests_are_acceptance_criteria`](file://.) applies).

### Phase 1 — Standalone sidecar mode: lockfile, idempotent attach, detached lifecycle

- **Owner skill:** `dev`
- **What:** Implement the lifecycle change from [ADR-0016](../adrs/0016-standalone-sidecar-mode.md). The Python sidecar writes `<user-data>/sidecar.lock` atomically on boot with `{pid, port, renderer_secret, started_at, process_create_time, sidecar_version}`, mode `0600` on POSIX; removes it on clean shutdown via a `finally` block on SIGTERM/SIGINT/normal-exit. On boot it runs the PID liveness probe against any existing lockfile (`psutil.Process(pid).create_time()` within ±5s of `process_create_time` → refuse to start; otherwise take over). The bearer is rotated on every sidecar boot and persisted in the lockfile. A new CLI subcommand `python -m market_analyser.api stop` reads the lockfile's PID, cross-checks `process_create_time`, sends SIGTERM, and exits. The Electron main process's sidecar-supervisor is rewritten: on app boot it runs the same lockfile liveness check; on hit, it attaches by reading port + bearer from the lockfile (no spawn); on miss, it spawns `python -m market_analyser.api --port=0` as today, then reads back the lockfile once the sidecar writes it. The `before-quit` handler is updated so the supervisor does NOT signal the sidecar (it lives on). The Settings page (Plan 0006 phase 5) gains a "Stop sidecar" button that POSTs to a new renderer-bearer-gated endpoint `POST /settings/stop` which schedules a graceful sidecar shutdown.
- **Files touched:** `src/market_analyser/api/__main__.py` (lockfile write/remove, liveness probe, `stop` subcommand routing); new `src/market_analyser/api/lockfile.py` (the lockfile read/write/probe primitives, isolated for testability); `src/market_analyser/api/app.py` (register new `POST /settings/stop` route under renderer-bearer middleware); new `src/market_analyser/api/routes/settings_stop.py`; `desktop/electron/sidecar.ts` (or whatever the existing supervisor module is — `dev` checks at phase start) for the attach-vs-spawn path and the no-kill-on-quit change; `desktop/renderer/views/SettingsView.tsx` (Stop button + handler); `pyproject.toml` (add `psutil` if not already present, respecting [ADR-0012](../adrs/0012-dependency-cooldown.md) cooldown); new `tests/api/test_sidecar_lockfile.py`; new `desktop/tests/main/sidecar-supervisor.spec.ts` (replacing or extending the existing Plan 0001 supervisor spec).
- **Done when:**
  - `tests/api/test_sidecar_lockfile.py` asserts each of the following with a concrete `assert`:
    - Cold start writes `sidecar.lock` containing all six required fields with correct types (`pid` int, `port` int, `renderer_secret` 64-hex-char string, `started_at` ISO-8601 UTC datetime, `process_create_time` float, `sidecar_version` string).
    - On POSIX the file mode is `0o600` after creation (skipped on Windows, with the skip reason "Windows file modes don't map per Plan 0006 phase 1").
    - Starting a second sidecar while the first is alive exits non-zero within 2 s with stderr containing `sidecar already running at PID` and the existing PID. The first sidecar is unaffected and its lockfile is unchanged.
    - On `SIGTERM` to the first sidecar, the lockfile is removed before the process exits (assert `not lockfile_path.exists()` after `proc.wait()`).
    - When the lockfile points at a `pid` for a different process (simulated by writing a lockfile whose `process_create_time` does not match `psutil.Process(pid).create_time()`), a new sidecar starts successfully and overwrites the lockfile; stderr contains a one-line warning naming the prior PID.
    - Two consecutive cold starts produce different `renderer_secret` values (assert inequality).
  - `desktop/tests/main/sidecar-supervisor.spec.ts` asserts (using test doubles for `child_process` and `fs`, not just process-listing spies — per `feedback_tests_are_acceptance_criteria`):
    - With no live lockfile, `attachOrSpawnSidecar()` calls `spawn(...)` exactly once with the existing command shape (`python -m market_analyser.api --port=0`) and then reads the lockfile once it appears. Returns `{port, renderer_secret, pid}` matching the lockfile contents.
    - With a live lockfile (PID alive, `process_create_time` matches), `attachOrSpawnSidecar()` does NOT call `spawn(...)` (assert call count is 0) and returns `{port, renderer_secret, pid}` matching the lockfile.
    - With a stale lockfile (PID dead), `attachOrSpawnSidecar()` does call `spawn(...)`.
    - The Electron app's `before-quit` handler does NOT call `process.kill` or `tree-kill` against the sidecar PID (assert the kill spy was not invoked for the sidecar pid during `before-quit`). The lockfile is unchanged after Electron quit.
  - Manual smoke (captured in the close handoff): run `python -m market_analyser.api --port=0`, observe `PORT=...` on stdout AND the lockfile present; from a second terminal run `python -m market_analyser.api stop`, observe the sidecar exits and the lockfile disappears. Then run the sidecar again; open Electron; observe the sidecar PID is unchanged (Electron attached, did not respawn); close Electron; confirm the sidecar PID is still alive.
  - **Secret-leak defence (extending the Plan 0006 close-followup pattern for `mcp-secret*.json`):**
    - `.gitignore` includes `sidecar.lock` and `sidecar.lock.tmp` (the atomic-write temp file). The repo `.gitignore` rule for `mcp-secret*.json` (commit `4ac6796`) is the pattern to mirror.
    - The CI guard added in commit `f0a6c52` (`.github/workflows/ci.yml` step "Guard against committed mcp-secret*.json") is extended to also fail the build if `git ls-files` contains any path matching `sidecar.lock*` anywhere in the tree. Verified by a deliberate-failure case in the PR description (stage the file locally, observe the guard fires, unstage). The rotation property (`renderer_secret` is per-sidecar-launch, stale within seconds of leak) is defence-in-depth, not the front line — keeping the file out of the repo is.

### Phase 2 — SSE event stream: `/events` endpoint + typed envelope schema

- **Owner skill:** `dev`
- **What:** Implement the SSE transport from [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md). Add a `GET /events` route gated by the renderer bearer (accepting the bearer from the `Authorization` header as usual AND from a `?token=<bearer>` query parameter to support `EventSource`; the query-string path is *only* enabled on `/events` and the access log is suppressed for that route). The route returns `text/event-stream` and yields envelopes from a per-subscriber bounded `asyncio.Queue` (cap 256). The sidecar issues a `: ping` comment every 15 s and `retry: 5000` once at stream start. Define the envelope shape (`Envelope(type, version, ts, payload)`) and the per-type Pydantic payload models for the initial vocabulary: `chart.show v1`, `chart.update v1`, `chart.highlight v1`, `run.completed v1`, and the synthetic `chart.update_dropped v1` (no payload). The event bus is a small in-process pub/sub: subscribers register a callback (or a queue); publishers call `bus.publish(envelope)` which validates against the registered payload model, fans out to subscriber queues, and applies drop-oldest on overflow (enqueueing a `chart.update_dropped` envelope after the drop). Cross-tenant tests assert the MCP bearer does NOT authenticate against `/events`.
- **Files touched:** new `src/market_analyser/api/events/__init__.py` (envelope + bus + payload models); new `src/market_analyser/api/routes/events.py`; `src/market_analyser/api/app.py` (register `/events`; if `sse-starlette` is taken as a dep, that goes in `pyproject.toml` under the cooldown policy from [ADR-0012](../adrs/0012-dependency-cooldown.md)); new `tests/api/test_events_sse.py`.
- **Done when:**
  - `tests/api/test_events_sse.py` asserts each of the following with `expect`-equivalents:
    - `GET /events?token=<valid_renderer_bearer>` returns 200 with `Content-Type: text/event-stream`. The stream stays open; the test reads a `: ping` comment within 16 s.
    - `GET /events` with no bearer returns 401. `GET /events?token=<wrong>` returns 401. `GET /events?token=<mcp_secret>` returns 401 (cross-tenant escalation blocked).
    - `GET /events` with the renderer bearer in the `Authorization` header (instead of the query) also returns 200 — header path is preserved for non-`EventSource` callers (e.g. `curl`).
    - The route's access log entry does NOT contain the `token=` query string (the access log is suppressed for `/events`; assert by inspecting the captured log handler).
    - Subscribing two clients, publishing one `chart.show v1` envelope via the event bus, then reading from both clients: both receive the same envelope (same `ts`, same `payload`).
    - Publishing with `type="not.registered"` raises an error at publish time (boundary validation; agent-facing tools never see this since they go through registered tool functions; the assertion is on the bus's `publish()` directly).
    - Publishing with `payload` that fails the per-type Pydantic model (e.g. `chart.show` payload missing `symbol`) raises a `ValidationError` at publish time.
    - Subscriber that disconnects mid-stream (close the client's connection) does NOT prevent a second subscriber from receiving the next event (the failed subscriber's queue is cleaned up).
    - With the queue cap set to 2 in test config, publishing 5 envelopes to a subscriber that hasn't drained: the subscriber receives the latest 2 envelopes and one synthetic `chart.update_dropped v1` envelope ahead of them.
  - The envelope `version` field is asserted to be an integer in every published envelope and matches the per-type model's declared version (`assert envelope.version == ChartShowPayloadV1.VERSION`).

### Phase 3 — MCP `show_*` tools: `show_chart`, `update_chart`, `highlight_pattern`

- **Owner skill:** `dev`
- **What:** Add three new MCP tools to the existing MCP app from Plan 0006. Each tool validates its inputs at the MCP boundary (Pydantic), calls `bus.publish(...)` with the appropriate envelope, and returns a minimal ack to the agent (`{event_published: true, type: "...", version: 1}`). The tools do NOT depend on a renderer being connected — publishing to a bus with no subscribers is a no-op. `highlight_pattern` additionally writes to the annotations table (Plan 0006's `AnnotationsRepository`) for persistence; the live event covers the immediate render, the row covers the "I closed Electron and reopened it later" case. The existing three tools from Plan 0006 (`get_ohlcv`, `write_annotation`, `list_annotations`) are unchanged.
- **Files touched:** `src/market_analyser/api/mcp_app.py` (add three tools); new `tests/api/test_show_tools.py`.
- **Done when:**
  - `tests/api/test_show_tools.py` asserts:
    - Calling `show_chart(symbol="AAPL", timeframe="1d", range_start=<2026-04-20T00:00:00Z>, range_end=<2026-05-20T00:00:00Z>, overlays=[{"kind": "ema", "period": 20}])` via an MCP client (using the existing test fixture from Plan 0006's `test_mcp_tools.py`) results in exactly one `chart.show v1` envelope being published to the bus. The envelope's payload equals the tool args (modulo serialization).
    - The tool returns `{event_published: True, type: "chart.show", version: 1}`.
    - Calling `update_chart(symbol="AAPL", timeframe="1d", overlays=[{"kind": "ema", "period": 50}])` (no range fields) publishes exactly one `chart.update v1` envelope with a payload that contains only the supplied fields (no `range_start`/`range_end` keys, not nulls).
    - Calling `highlight_pattern(symbol="AAPL", timeframe="1d", event_ts=<2026-05-15T00:00:00Z>, kind="bullish_marker", label="hammer at support")` does BOTH: publishes one `chart.highlight v1` envelope, AND inserts one row into the annotations table visible to `AnnotationsRepository.list_for(...)` for the matching window. (Reuses Plan 0006's repository — no new table needed.)
    - Each tool rejects invalid inputs at the MCP boundary: `timeframe="5m"` (not in `SUPPORTED_TIMEFRAMES`, addressing one of Plan 0006's open followups in passing for the new tools), `symbol=""`, `range_end < range_start`, `overlays=[{"kind": "unknown"}]`. Rejection surfaces as an MCP-level error to the agent, not a 500.
    - The three pre-existing tools (`get_ohlcv`, `write_annotation`, `list_annotations`) still pass their Plan 0006 tests (regression check by importing and re-running the existing suite).

### Phase 4 — Electron SSE subscriber + chart-render handlers

- **Owner skill:** `ui-builder`
- **What:** Add the renderer-side event consumption. New hook `useEventStream` opens an `EventSource` to `/events?token=<renderer_bearer>` on mount (where the bearer comes from the existing typed fetch client config), dispatches envelopes to handlers by `type`, reconnects on disconnect (the browser's built-in `EventSource` retry handles this; the hook surfaces connection state for UI). New handlers for `chart.show v1`, `chart.update v1`, `chart.highlight v1`; the `run.completed v1` handler logs to console and surfaces a toast (the toast component already exists from Plan 0006's Settings page; if not, `ui-builder` adds a minimal one). The OhlcvView gains imperative-handle hooks so the `chart.show` handler can switch its symbol/timeframe/range without remounting, and so the `chart.update` handler can add/remove overlay series in place. The polling for annotations from Plan 0006 (`useAnnotationsPoll`) is unchanged — converting it to a `chart.highlight` subscriber is out of scope per the ADR-0017 note.
- **Files touched:** new `desktop/renderer/hooks/useEventStream.ts`; new `desktop/renderer/handlers/chartHandlers.ts` (the per-type dispatch); `desktop/renderer/views/OhlcvView.tsx` (imperative API for symbol/timeframe/range/overlay changes); `desktop/renderer/App.tsx` (mount the hook at top level); generated TS types for the envelope shape (e.g. from the Pydantic models — if the existing typegen pipeline doesn't cover non-route models, add a manual type file matching the schema and a regression test that imports both and asserts shape parity); new `desktop/tests/useEventStream.spec.tsx` (Jest); extend `desktop/tests/e2e/ohlcv-view.spec.ts` OR add new `desktop/tests/e2e/live-chart.spec.ts`.
- **Done when:**
  - `desktop/tests/useEventStream.spec.tsx` (Jest) asserts:
    - On mount, the hook constructs an `EventSource` with URL of the form `http://127.0.0.1:<port>/events?token=<bearer>` (assert the exact URL via a `mock` of the `EventSource` constructor).
    - On receiving a message with valid `chart.show v1` envelope JSON, the hook invokes the registered `chart.show` handler with the parsed payload. (Use `EventSource`'s mock to fire a `MessageEvent` with the envelope JSON.)
    - On receiving an envelope with `type="chart.update"` and `version=1`, the registered `chart.update` handler is invoked.
    - On receiving an envelope with `version=2` for a known type (e.g. simulating future schema evolution), the hook calls the version-1 handler with a warning logged (forward-compatible default) — defending the ADR-0017 versioning discipline.
    - On `EventSource.onerror`, the hook updates its exposed connection state to `"reconnecting"`. (Built-in reconnection is the browser's responsibility; the hook surfaces the state.)
    - On unmount, the hook closes the `EventSource` (assert `close()` was called).
  - `desktop/tests/e2e/live-chart.spec.ts` (Playwright) asserts each behavioral claim with concrete `expect(...)` lines:
    - With the app open on the default view, pushing a `chart.show v1` envelope through the sidecar's event bus (via a test-only sidecar fixture endpoint OR by calling `show_chart` over MCP within the test setup) causes the renderer to display the requested symbol's name in the chart header within 1 s.
    - The same `chart.show v1` push with `overlays=[{kind:"ema", period:20}]` results in an EMA series being added to the chart. The assertion uses a renderer-exposed test hook (`window.__test_chart_state__.overlays`) rather than canvas-pixel inspection — the canvas-pixel ceiling from Plan 0006 phase 6 applies here too and we accept it.
    - A subsequent `chart.update v1` push with `overlays=[{kind:"ema", period:50}]` results in `window.__test_chart_state__.overlays` containing both EMA20 and EMA50 series (i.e. update merges with existing state, doesn't replace).
    - A `chart.update v1` push with a new `range_start`/`range_end` results in the chart's visible range matching within 100 ms.
  - Manual smoke (captured in the phase-4 commit message and re-confirmed in the handoff to phase 5): open the Electron app while a sidecar is running; from a script call `show_chart` over MCP (using the MCP bearer); observe the chart switches to the requested symbol within ~1 s.

### Phase 5 — Claude Code MCP config + end-to-end smoke

- **Owner skill:** `human`
- **What:** Configure Claude Code to talk to the running market-analyser sidecar's MCP endpoint and run the motivating end-to-end loop to confirm the whole stack works. Document the configuration so future users (and the project's own onboarding) can repeat it.
- **Files touched:** new `docs/onboarding/claude-code-setup.md` (the config snippet, the smoke procedure, the troubleshooting list); `README.md` (one short section linking to the onboarding doc and naming Claude Code as the primary interface, per ADR-0015).
- **Done when:**
  - `docs/onboarding/claude-code-setup.md` contains:
    - The exact `.mcp.json` snippet (project-local) or `~/.claude.json` snippet (global) for adding the market-analyser sidecar as an MCP server. Snippet uses the Streamable HTTP transport, points at `http://127.0.0.1:<port>/mcp`, and shows where to paste the bearer from `mcp-secret.json` (NOT the renderer bearer from `sidecar.lock`).
    - A note that the sidecar must be running before Claude Code starts (either via `python -m market_analyser.api --port=<n>` in a separate terminal, or by opening Electron at least once and letting it auto-start).
    - The lifecycle behaviour: closing Electron does NOT stop the sidecar; to stop it, run `python -m market_analyser.api stop` or click "Stop sidecar" in Settings.
    - Troubleshooting: 401 from Claude Code → bearer wrong (re-copy from Settings); MCP server unreachable → sidecar not running (check `sidecar.lock` exists and `psutil.Process(pid).is_running()`).
  - `README.md`'s top section names Claude Code as the primary interaction surface and links to `docs/onboarding/claude-code-setup.md`.
  - Manual end-to-end smoke (logged in the phase-5 commit message and surfaced in the handoff to architect close):
    - Sidecar running (Electron-started OR CLI-started).
    - Electron viewer open.
    - In Claude Code: ask "show me AAPL daily candles with a 30-day window and EMA20". The chart appears in the viewer within 1 s, showing AAPL 1d for the requested window with an EMA20 overlay. Confirmed visually.
    - Continue: "now add EMA50 and zoom to the last 10 days". The chart updates in place; EMA50 appears alongside EMA20; visible range narrows. Confirmed visually within 1 s.
    - Continue: "highlight the bullish engulfing pattern from 2026-05-12". A marker appears on the 2026-05-12 candle; hovering it shows the label. Confirmed visually.
    - Close Electron. Continue the Claude Code dialog: "what was the close price on 2026-05-15?" — Claude calls `get_ohlcv` and answers from the cached bars. Sidecar still running. Confirmed by checking `sidecar.lock` and re-opening Electron (chart re-renders with current state).
  - At the boundary, `human` stops and hands back to the user for the architect close ceremony (no auto-handoff for `human` phases).

## Data shapes

```python
# illustrative — final shape locked in phase 2

class Envelope(BaseModel):
    type: str         # e.g. "chart.show"
    version: int      # per-type version; starts at 1
    ts: datetime      # UTC, ms precision, set at publish time
    payload: dict     # validated against per-type model

class ChartShowPayloadV1(BaseModel):
    VERSION: ClassVar[int] = 1
    symbol: str
    timeframe: str   # one of SUPPORTED_TIMEFRAMES per ADR-0007
    range_start: datetime
    range_end: datetime
    overlays: list[OverlaySpec] | None = None

class ChartUpdatePayloadV1(BaseModel):
    VERSION: ClassVar[int] = 1
    symbol: str
    timeframe: str
    overlays: list[OverlaySpec] | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    focus_bar: datetime | None = None

class ChartHighlightPayloadV1(BaseModel):
    VERSION: ClassVar[int] = 1
    symbol: str
    timeframe: str
    markers: list[Marker]   # {event_ts, kind: "bullish_marker"|"bearish_marker", label?}

class RunCompletedPayloadV1(BaseModel):
    VERSION: ClassVar[int] = 1
    kind: Literal["backtest", "analysis", "defi"]
    run_id: str
    artifact_path: str       # relative to runs/

class OverlaySpec(BaseModel):
    kind: Literal["ema", "sma", "rsi", "macd", "bbands"]   # extend as indicators land
    period: int | None = None   # required for ema/sma; etc.
    # other per-kind params as the indicators module ships
```

```json
// sidecar.lock (user data dir, mode 0600)
{
  "pid": 12345,
  "port": 53221,
  "renderer_secret": "<64 hex chars>",
  "started_at": "2026-05-20T14:23:01.500Z",
  "process_create_time": 1747749781.5,
  "sidecar_version": "0.1.0"
}
```

The `OverlaySpec` enum is intentionally narrow at MVP — the indicators module is its own future plan. Adding a new overlay kind is additive (new literal value, new optional fields, no version bump on `chart.show`).

## Risks & open questions

- **Risk: PID-reuse race window on lockfile attach.** Within ~5 s of a sidecar exit, the OS may reuse the PID for an unrelated process. The `process_create_time` cross-check (±5 s tolerance) closes the obvious window. A tightly-timed adversarial reuse defeats it; the worst outcome is Electron attaches to the wrong process, gets no `/healthz` response, and falls back to spawn. Mitigation: the attach path always confirms the sidecar's identity with a `GET /healthz` before considering itself attached. Add this as an explicit check in phase 1's supervisor code.
- **Risk: bearer in SSE query string leaks via process listings or logs.** Suppressing the access log is the front-line defence; query strings are not in environment or argv. The fallback is the per-sidecar-launch rotation: a leaked bearer is bounded to one sidecar lifetime. If a contributor pushes back, the migration to `fetch` + `ReadableStream` is documented in ADR-0017 — a phase-4 follow-up if needed.
- **Risk: `update_chart` arrives before `show_chart` (out-of-order or first message of a session).** ADR-0017's decision: the renderer treats `chart.update` with no matching open chart as `chart.show` with the available fields. Done-when in phase 4 should include a Playwright assertion for this exact case.
- **Risk: agent issues `show_chart` while no Electron viewer is connected.** Per ADR-0017 events are ephemeral; the event is dropped. The agent gets `event_published: true` and proceeds — but the user, on opening Electron later, sees nothing. Acceptable for MVP. Future: a "missed events" toast or a small replay buffer (decided by demand, not by speculation).
- **Risk: two Electron viewers open at once.** Both subscribe; both render the same commands; user sees duplicated UI. Plan 0006 didn't single-instance Electron either; we accept the same behaviour here.
- **Risk: `mcp` Python SDK or `sse-starlette` dependency churn within the cooldown window.** Pin exact versions per [ADR-0013](../adrs/0013-pin-direct-dependencies.md); cooldown per [ADR-0012](../adrs/0012-dependency-cooldown.md). Phase 1 commit message names the SDK version pinned.
- **Risk: `psutil` is platform-specific and adds install cost.** `psutil` is widely-used and cross-platform; the cost is acceptable. If avoiding the dep is desired, a per-platform shim (`os.kill(pid, 0)` on POSIX + `ctypes.windll.kernel32.OpenProcess(...)` on Windows) is possible at the cost of more code. Default to `psutil`; flip if review finds the dep weight excessive.
- **Risk: `chart.highlight` overlapping with `write_annotation` confuses agents.** Both result in a marker on the chart. The doc string for `highlight_pattern` must explain: `highlight_pattern` is for patterns the agent detected NOW (persisted + live); `write_annotation` is the lower-level primitive (persisted only). Phase 3 includes this in the tool docstring; phase 5 confirms it reads well in Claude Code's tool surface.
- **Open question: should the `OverlaySpec` literal set include indicators we haven't built yet?** Pre-declaring `rsi`, `macd`, `bbands` in the literal means an agent can request them; the renderer-side renders only what it knows; unknown kinds are logged and ignored. Pre-declaring is forward-friendly; the alternative is to add overlay kinds one at a time. Default to a small pre-declared set; the renderer's "ignored unknown overlay" path is part of phase 4's test surface.
- **Open question: `show_chart` `range_start`/`range_end` semantics — inclusive both ends?** Default to inclusive on both, matching how Plan 0006 specifies `/annotations` and `/ohlcv`. Phase 3 done-when includes a boundary test.

## What this plan does NOT do

- **New MCP tools beyond `show_chart`, `update_chart`, `highlight_pattern`.** Specifically: no `show_equity_curve` (deferred until backtester lands), no `run_backtest` (deferred to the backtester plan), no `scan_patterns` (deferred to the analyst's MCP integration plan).
- **Adaptive backpressure or per-type queueing policy.** Drop-oldest with synthetic notice is MVP; a future plan can introduce coalesce-by-key if backtest-progress events at high rate land.
- **Tray-app or OS-service supervisor for the sidecar.** Manual restart on crash is acceptable for now; ADR-0016 documents this as the deferral.
- **Single-instance enforcement for the Electron viewer.** Two viewers open at once is allowed (and harmless).
- **Conversion of `useAnnotationsPoll` (Plan 0006) to an SSE subscriber.** Polling stays; the seam is documented; a future plan or PR converts when convenient.
- **System notifications for `run.completed` while the viewer is closed.** Toast inside the viewer only; OS-level notifications are out of scope.
- **Renderer→agent feedback (e.g. "user dragged the chart, new range is X").** SSE is server→client only by design; if bidirectional is needed, WebSocket is a future ADR.
- **`docs/architecture/diagrams/bootstrap-component-map.md` refresh.** Plan 0006's followup tracks this; this plan introduces a new diagram (`claude-cli-driven-architecture.md`) which supersedes the bootstrap map for the post-ADR-0015 architecture, but the legacy diagram is not touched here.
- **Agent-written strategy code (Plan 0006's "A" tier).** Still deferred indefinitely; sandboxing model not yet decided.
- **Auto-update for the desktop app or the sidecar.** Same packaging-plan deferral as everywhere else.

## Followups (after this lands)

Empty at draft time. Architect populates from review findings + implementer notes during the close ceremony.
