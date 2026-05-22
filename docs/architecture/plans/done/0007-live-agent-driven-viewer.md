# 0007 — Live agent-driven viewer: standalone sidecar + SSE + `show_*` tools

> **Status:** done
> **Created:** 2026-05-20
> **Approved:** 2026-05-20
> **Closed:** 2026-05-22
> **Owner skill(s):** `dev`, `ui-builder`, `human`
> **Related ADRs:** [ADR-0015](../../adrs/0015-claude-code-primary-control-surface.md) (role inversion), [ADR-0016](../../adrs/0016-standalone-sidecar-mode.md) (lockfile + idempotent attach), [ADR-0017](../../adrs/0017-live-ui-updates-via-sse.md) (SSE event stream), [ADR-0014](../../adrs/0014-mcp-as-second-sidecar-protocol.md) (MCP foundation this builds on), [ADR-0011](../../adrs/0011-bearer-secret-transport.md) (per-launch bearer — refined by ADR-0016), [ADR-0002](../../adrs/0002-ipc-local-http.md) (renderer transport), [ADR-0020](../../adrs/0020-shared-data-dir-contract.md) (data-dir contract; introduced by Amendment 2026-05-22)

## Amendment 2026-05-22 — Phase 5 smoke surfaced four defects with one root cause

Phase 5 smoke ran on 2026-05-21 and paused mid-debug. The MCP envelope acks (`event_published: true`) but the Electron viewer never re-renders. Detailed handoff at `.claude/smoke-handoff-plan-0007.md`. Diagnosis:

- **Root cause 1 — shared data dir is implicit, not contractual.** Python (`src/market_analyser/config.py:31-51`) resolves `<APPDATA>/market-analyser/`. Electron (`desktop/electron/main.ts:38-40`) calls `app.getPath('userData')`, which in dev mode derives from `desktop/package.json#name = "@market-analyser/desktop"` → `<APPDATA>/@market-analyser/desktop/`. Packaging hides this (`build.productName = "market-analyser"` aligns the paths); dev mode exposes it. Three separate lockfile candidates ended up on disk, each pointing at a different "the sidecar"; Claude Code, Electron, and the manual sidecar all disagreed on which was canonical.
- **Root cause 2 — tests defend local correctness, not cross-process integration.** Phase 1's `desktop/tests/main/sidecar-supervisor.spec.ts` injects `dataDir: '/tmp/test-data'` (line 91) — never exercises the real `app.getPath('userData')`. Phase 4's `desktop/tests/live-chart.spec.ts` injects `MARKET_ANALYSER_DATA_DIR = app.getPath('userData')` (lines 87, 118-120) — explicitly *documents the divergence as a known workaround* (JSDoc lines 50-57) and sidesteps it. Phase 4's `__test_chart_state__` snapshot asserts on the reducer's output, not on `<OhlcvView>`'s rendered props.
- **Direct consequences (the four defects from the handoff):**
  - **Defect 1 (Phase 1).** `userData ≠ default_app_data_dir()` in dev. Per ADR-0020 the fix is structural, not the proposed one-liner.
  - **Defect 2 (Phase 1).** Plan line 247 required the attach path to confirm identity via `/healthz`; the requirement was dropped silently. `desktop/electron/sidecar.ts:131-141` attaches without probing.
  - **Defect 3 (Phase 4).** `overlays` lives in the reducer but never reaches `<OhlcvView>` (App.tsx:114-124 omits the prop). Live-chart spec passes anyway because it reads from the reducer.
  - **Defect 4 (Phase 4).** `desktop/renderer/api/client.ts:22-28` refreshes only `secretToken` on `sidecar:status` restart events, not `port`. `desktop/renderer/hooks/useEventStream.ts:75-120` has an empty-deps `useEffect`, so the `EventSource` is never re-opened anyway. Two bugs compose to "renderer cannot survive a sidecar restart".

The architect close ceremony for this plan does NOT happen until the smoke is green. The four defects + the ADR-0020 contract land as four new phases (4.1–4.4) inserted between Phase 4 and Phase 5. Phase 5 (human smoke) re-fires after they ship.

**Convention deviation noted.** The plans README says *"in-progress plans are append-only on substance; structural amendments — adding phases — happen via a new followup plan, not in-place."* This amendment violates that rule. Rationale: the new phases are guardrails fixing what Phase 4 promised but failed to deliver — they fall inside Phase 4's blast radius rather than introducing new scope. A followup plan would have the same execution sequence, the same owners, the same close ceremony, and an extra cross-link to maintain. The cost-benefit favours in-place amendment this once; the next time an in-flight plan needs structural change, default back to the convention. The `Owner skill(s):` line is unchanged because all new owners (`dev`, `ui-builder`) already appear.

## TL;DR

Detach the sidecar from Electron's process tree (lockfile-based single-instance + idempotent attach), add an SSE event stream from the sidecar to the renderer, and ship three new MCP tools (`show_chart`, `update_chart`, `highlight_pattern`) that publish typed envelopes to the stream. Then wire the renderer to consume those envelopes and render live. The first user-visible behavior at the end of the plan: open Claude Code, ask "show me AAPL daily with EMA20", see the chart appear in the Electron viewer within a second; ask "now add EMA50 and zoom to the last 30 days", see the chart update live.

## Context & problem

[ADR-0015](../../adrs/0015-claude-code-primary-control-surface.md) declared Claude Code the primary control surface and Electron the live viewer. That decision is empty until two things are true:

1. The sidecar runs without Electron (so the agent's reach doesn't depend on a UI window being open).
2. The renderer reacts in real time when the agent issues a render command (so the conversational tweak loop feels live, not delayed by 500 ms polling).

[ADR-0016](../../adrs/0016-standalone-sidecar-mode.md) and [ADR-0017](../../adrs/0017-live-ui-updates-via-sse.md) set the mechanism for each. This plan is the execution.

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

Five phases, ordered so the first one (standalone sidecar + lockfile) is independently valuable (Claude Code can drive workflows even before SSE lands — events just have no subscriber, the agent's MCP calls still complete and persist data). Phases 1–3 are `dev`-owned and run as a contiguous block in one session. Phase 4 is `ui-builder`-owned — handoff at the dev↔ui-builder boundary via the [cross-skill handoff protocol](../../../../.claude/skills/architect/references/templates/cross-skill-handoff.md). Phase 5 is `human` — user-only smoke and config; the implementer stops at the boundary and surfaces it.

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
- **What:** Implement the lifecycle change from [ADR-0016](../../adrs/0016-standalone-sidecar-mode.md). The Python sidecar writes `<user-data>/sidecar.lock` atomically on boot with `{pid, port, renderer_secret, started_at, process_create_time, sidecar_version}`, mode `0600` on POSIX; removes it on clean shutdown via a `finally` block on SIGTERM/SIGINT/normal-exit. On boot it runs the PID liveness probe against any existing lockfile (`psutil.Process(pid).create_time()` within ±5s of `process_create_time` → refuse to start; otherwise take over). The bearer is rotated on every sidecar boot and persisted in the lockfile. A new CLI subcommand `python -m market_analyser.api stop` reads the lockfile's PID, cross-checks `process_create_time`, sends SIGTERM, and exits. The Electron main process's sidecar-supervisor is rewritten: on app boot it runs the same lockfile liveness check; on hit, it attaches by reading port + bearer from the lockfile (no spawn); on miss, it spawns `python -m market_analyser.api --port=0` as today, then reads back the lockfile once the sidecar writes it. The `before-quit` handler is updated so the supervisor does NOT signal the sidecar (it lives on). The Settings page (Plan 0006 phase 5) gains a "Stop sidecar" button that POSTs to a new renderer-bearer-gated endpoint `POST /settings/stop` which schedules a graceful sidecar shutdown.
- **Files touched:** `src/market_analyser/api/__main__.py` (lockfile write/remove, liveness probe, `stop` subcommand routing); new `src/market_analyser/api/lockfile.py` (the lockfile read/write/probe primitives, isolated for testability); `src/market_analyser/api/app.py` (register new `POST /settings/stop` route under renderer-bearer middleware); new `src/market_analyser/api/routes/settings_stop.py`; `desktop/electron/sidecar.ts` (or whatever the existing supervisor module is — `dev` checks at phase start) for the attach-vs-spawn path and the no-kill-on-quit change; `desktop/renderer/views/SettingsView.tsx` (Stop button + handler); `pyproject.toml` (add `psutil` if not already present, respecting [ADR-0012](../../adrs/0012-dependency-cooldown.md) cooldown); new `tests/api/test_sidecar_lockfile.py`; new `desktop/tests/main/sidecar-supervisor.spec.ts` (replacing or extending the existing Plan 0001 supervisor spec).
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
- **What:** Implement the SSE transport from [ADR-0017](../../adrs/0017-live-ui-updates-via-sse.md). Add a `GET /events` route gated by the renderer bearer (accepting the bearer from the `Authorization` header as usual AND from a `?token=<bearer>` query parameter to support `EventSource`; the query-string path is *only* enabled on `/events` and the access log is suppressed for that route). The route returns `text/event-stream` and yields envelopes from a per-subscriber bounded `asyncio.Queue` (cap 256). The sidecar issues a `: ping` comment every 15 s and `retry: 5000` once at stream start. Define the envelope shape (`Envelope(type, version, ts, payload)`) and the per-type Pydantic payload models for the initial vocabulary: `chart.show v1`, `chart.update v1`, `chart.highlight v1`, `run.completed v1`, and the synthetic `chart.update_dropped v1` (no payload). The event bus is a small in-process pub/sub: subscribers register a callback (or a queue); publishers call `bus.publish(envelope)` which validates against the registered payload model, fans out to subscriber queues, and applies drop-oldest on overflow (enqueueing a `chart.update_dropped` envelope after the drop). Cross-tenant tests assert the MCP bearer does NOT authenticate against `/events`.
- **Files touched:** new `src/market_analyser/api/events/__init__.py` (envelope + bus + payload models); new `src/market_analyser/api/routes/events.py`; `src/market_analyser/api/app.py` (register `/events`; if `sse-starlette` is taken as a dep, that goes in `pyproject.toml` under the cooldown policy from [ADR-0012](../../adrs/0012-dependency-cooldown.md)); new `tests/api/test_events_sse.py`.
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

### Phase 4.1 — Shared data-dir contract (implements ADR-0020)

- **Owner skill:** `dev`
- **What:** Codify [ADR-0020](../../adrs/0020-shared-data-dir-contract.md). The data directory is `<platform-base>/market-analyser/` computed by a single canonical algorithm on both sides — Python and Electron — independent of `app.getName()`, `package.json#name`, or `build.productName`. The literal directory name `"market-analyser"` is the contract. Add a new TypeScript resolver `desktop/shared/data-dir.ts::resolveSharedDataDir()` that computes the canonical path directly (mirroring `default_app_data_dir()`), honouring `MARKET_ANALYSER_DATA_DIR` as a verbatim override. Update `desktop/electron/main.ts::resolveDataDir()` to call the new resolver (NOT `app.getPath('userData')`). Keep `app.setName('market-analyser')` (added early in `main.ts`, before any other Electron API touches it) so OS-level surfaces (window title, taskbar, recent-files) read correctly — but the data dir does NOT depend on it. Extend `GET /healthz`: when the renderer bearer is present in `Authorization`, the response gains a `data_dir` field (absolute path string); the auth-exempt path keeps returning `{ok, version}` only. Document the bearer-gated field in the route's docstring and on ADR-0020.
- **Files touched:** new `desktop/shared/data-dir.ts`; `desktop/electron/main.ts` (replace `resolveDataDir`, add `app.setName('market-analyser')` early); `src/market_analyser/api/app.py` (extend `/healthz` to include `data_dir` when bearer is present); new `tests/api/test_data_dir_contract.py`; new `desktop/tests/main/data-dir.spec.ts`.
- **Done when:**
  - `tests/api/test_data_dir_contract.py` asserts:
    - On Windows-emulated env (`os.environ["APPDATA"] = "C:\\Users\\test\\AppData\\Roaming"`, monkeypatch `sys.platform = "win32"`), `default_app_data_dir()` returns `Path("C:/Users/test/AppData/Roaming/market-analyser")`. Same shape for darwin (`~/Library/Application Support/market-analyser`) and linux (`$XDG_DATA_HOME/market-analyser` or `~/.local/share/market-analyser`).
    - The literal string `"market-analyser"` appears in the path under every platform branch (assert via `str(path).endswith("market-analyser")` on each).
    - `MARKET_ANALYSER_DATA_DIR=/tmp/foo` makes `default_app_data_dir()` return `Path("/tmp/foo")` verbatim (no suffix appended).
    - `GET /healthz` with the renderer bearer returns a JSON body whose `data_dir` field equals `str(default_app_data_dir())`. `GET /healthz` without the bearer returns `{ok, version}` only — assert `"data_dir" not in response.json()`.
    - `GET /healthz` with the MCP bearer (cross-tenant) does NOT include `data_dir` (the `data_dir` disclosure is renderer-bearer-only; MCP clients have no need for this field).
  - `desktop/tests/main/data-dir.spec.ts` (Jest, main-process) asserts:
    - `resolveSharedDataDir()` returns a path ending in `market-analyser` on every platform branch (parametrised over `process.platform` and the relevant env vars; the test mocks `process.platform` and `process.env`).
    - With `MARKET_ANALYSER_DATA_DIR=/tmp/specific`, `resolveSharedDataDir()` returns `/tmp/specific` verbatim.
    - `resolveSharedDataDir()` does NOT call `app.getName()` or `app.getPath('userData')` (assert by checking the implementation has no `electron` import; a regex check on the file's imports is sufficient — the resolver lives under `desktop/shared/` so it must be electron-free anyway).
  - Cross-resolver consistency check (a single test, runnable from either side): a test harness spawns a Python subprocess that prints `default_app_data_dir()` and runs the TypeScript resolver in the same process env; asserts the two strings are equal. Lives at `desktop/tests/main/data-dir-cross-resolver.spec.ts` with a node-side helper invoking the Python one-liner. Acceptable to skip on CI if the harness is too fragile; the per-side tests above are the primary defence.
  - Manual verification (logged in the phase commit message): run `python -c "from market_analyser.config import default_app_data_dir; print(default_app_data_dir())"` and `node -e "console.log(require('./desktop/dist/main/...').resolveSharedDataDir())"` (or equivalent); confirm string equality on the developer's machine.
  - The `MARKET_ANALYSER_DATA_DIR` env var continues to be passed by `desktop/electron/sidecar.ts:149` to the spawned sidecar — but the value now comes from `resolveSharedDataDir()`, not `app.getPath('userData')`. No change to the call site is required if `SidecarSupervisor` is constructed with `resolveDataDir()`'s return value, which is the case in `main.ts`.

### Phase 4.2 — Attach path confirms identity via `/healthz` (closes Plan 0007 line 247)

- **Owner skill:** `dev`
- **What:** Wire the previously-skipped `/healthz` identity check into `attachOrSpawnSidecar`. After reading the live lockfile and BEFORE returning `{info, attached: true}`, the attach path must `GET /healthz` (with the renderer bearer from the lockfile, so `data_dir` is in the response) and confirm `response.data_dir === <the path the lockfile was read from>`. On mismatch: log the discrepancy, treat the lockfile as stale, fall through to the spawn path. On `/healthz` timeout or non-200: same — treat as stale. The existing `waitForHealthz` helper (sidecar.ts:200-215) covers the response-shape side; this phase extends it (or adds a sibling helper) to also parse and compare `data_dir`. The healthz fetch needs the bearer; the existing healthz seam in `AttachOrSpawnDeps` (`healthz?: (url) => Promise<{ok: boolean}>`) expands to `healthz?: (url, opts?: {bearer?: string}) => Promise<{ok: boolean, data_dir?: string}>` so tests can stub it.
- **Files touched:** `desktop/electron/sidecar.ts` (extend the attach path; expand `AttachOrSpawnDeps.healthz` signature and the default impl); `desktop/tests/main/sidecar-supervisor.spec.ts` (add the new test cases).
- **Done when:**
  - `desktop/tests/main/sidecar-supervisor.spec.ts` asserts (in addition to the existing four tests, which continue to pass with the expanded healthz signature):
    - With a live lockfile AND `healthz` returning `{ok: true, data_dir: "<matches the dataDir>"}`: `attachOrSpawnSidecar` returns `{attached: true}` and does NOT call `spawn` (existing behaviour preserved). The `healthz` mock was called exactly once with the renderer bearer from the lockfile (assert the second arg's `bearer` value).
    - With a live lockfile AND `healthz` returning `{ok: true, data_dir: "/some/other/path"}`: `attachOrSpawnSidecar` falls through to spawn — `spawn` is called exactly once, `attached: false` in the result. A `console.warn` (or equivalent log seam) records the mismatch with both paths.
    - With a live lockfile AND `healthz` returning `{ok: false}` (e.g. 401): falls through to spawn (the lockfile is treated as stale; the bearer in the lockfile doesn't match the running sidecar's expected bearer, which is itself diagnostic of a stale lockfile).
    - With a live lockfile AND `healthz` throwing (network error): falls through to spawn after one retry within the existing `HEALTHZ_TIMEOUT_MS` budget.
  - The new failure modes (mismatch, 401, throw) produce a structured log line (single `console.warn` per attempt) so the user sees *why* the attach was rejected, not just that a new sidecar got spawned. The log includes the lockfile path, the expected `data_dir`, and the observed `data_dir` (or the error message).
  - No change to the spawn-path behaviour — `waitForHealthz` already runs on the spawn path (sidecar.ts:172) and stays as-is; the new identity check is attach-path-only because spawn-path's healthz target is the sidecar that *we just spawned*, whose data_dir is by definition the dataDir we passed it via `MARKET_ANALYSER_DATA_DIR`.

### Phase 4.3 — Supervisor refresh API for renderer-triggered re-attach

- **Owner skill:** `dev`
- **What:** Standalone-mode sidecars don't "restart" from Electron's perspective (ADR-0016 — no crash supervision), so the existing `sidecar:status` event with `kind: 'restarted'` no longer fires. But the sidecar *can* die out-of-band (user runs `python -m market_analyser.api stop`, or kills the process, then starts a fresh one with a new port and bearer). The Electron viewer needs a way to discover the new identity. Add `SidecarSupervisor.refresh(): Promise<SidecarInfo>` which re-runs `attachOrSpawnSidecar({dataDir})` and emits a new `sidecar:status` event with `kind: 'refreshed'` carrying both `port: number` and `secretToken: string` in the payload. The existing IPC handler in `desktop/electron/ipc.ts` gains a new channel `sidecar:refresh` that calls `supervisor.refresh()` and returns the new info. The renderer trigger lands in phase 4.4; this phase is the main-process substrate.
- **Files touched:** `desktop/electron/sidecar.ts` (add `refresh()` method; extend the emitted status type); `desktop/electron/ipc.ts` (register `sidecar:refresh` handler); `desktop/shared/schemas/sidecar.ts` (extend `SidecarStatus` to include `refreshed`); `desktop/preload/index.ts` (expose `window.api.sidecar.refresh()`); `desktop/tests/main/sidecar-supervisor.spec.ts` (new test cases).
- **Done when:**
  - `desktop/tests/main/sidecar-supervisor.spec.ts` asserts:
    - After calling `supervisor.refresh()` against a live lockfile (PID alive, /healthz returns matching data_dir): `getInfo()` returns the same `{port, secretToken, pid}` as before (no-op refresh case). A `sidecar:status` event was emitted with `kind: 'refreshed'` and a payload that includes both `port` and `secretToken`.
    - After calling `supervisor.refresh()` when the prior sidecar is dead and a new one has been started (different port and bearer in the new lockfile): `getInfo()` returns the new `{port, secretToken, pid}`. The `sidecar:status` event includes the new port and bearer.
    - Concurrent `refresh()` calls coalesce — a second `refresh()` started while the first is in-flight returns the same promise (assert via two parallel `refresh()` calls and `expect(result1).toBe(result2)`). This prevents thundering-herd if the renderer fires multiple recovery attempts at once.
    - `SidecarStatus`'s zod schema (or equivalent — wherever the existing `SidecarStatus` is validated) accepts `kind: 'refreshed'` with `{port: number, secretToken: string}`.
  - The new IPC channel `sidecar:refresh` is registered exactly once (no double-registration); invoking it returns the same shape as `getInfo()`. Tested in `desktop/tests/main/ipc.spec.ts` (or wherever IPC registration is currently tested; if no such spec exists, add one).

### Phase 4.4 — Renderer port+secret refresh + EventSource re-open on stream failure

- **Owner skill:** `ui-builder`
- **What:** Close the renderer-side half of defect 4. (a) `desktop/renderer/api/client.ts` updates the cached `{port, secretToken}` on `sidecar:status` events with `kind: 'refreshed'` — both fields, not just `secretToken`. The existing `kind === 'restarted'` branch (client.ts:22-28) can be retained for legacy or removed (the supervisor no longer emits `restarted` per ADR-0016; safest is to keep the branch as a no-op-tolerant fallback and add the `refreshed` branch alongside it). (b) `desktop/renderer/hooks/useEventStream.ts` re-opens its `EventSource` when the cached `{port, secretToken}` change. Implementation: track the URL via `useState` and recompute it in a `useEffect` that subscribes to a small client-side event the api/client emits on cache update (a new `subscribeToConfigChanges(cb)` exported from `client.ts`); when the URL changes, close the old `EventSource` and open a new one. (c) On persistent connection failure (the hook's `onerror` has fired N=3 times within W=10 seconds without an intervening `onopen`), the hook invokes `window.api.sidecar.refresh()` to trigger the main-process re-attach (phase 4.3 makes that IPC channel exist).
- **Files touched:** `desktop/renderer/api/client.ts` (add `refreshed` branch; export `subscribeToConfigChanges`); `desktop/renderer/hooks/useEventStream.ts` (subscribe; recompute URL; reopen on change; failure-driven refresh); `desktop/preload/index.ts` may already expose what's needed from phase 4.3 — verify and extend if not; new tests added below.
- **Done when:**
  - New Jest spec `desktop/tests/useEventStream.spec.tsx` (or extension of the existing one) asserts:
    - When the api/client emits a `refreshed` config change with a new `{port, secretToken}`, the hook closes the previous `EventSource` (assert `close()` called on the prior instance) and constructs a new one with the URL containing the new port and token. (Use `EventSource` mock; capture both constructions.)
    - After 3 `onerror` events within a 10-second window with no `onopen` between them, the hook calls `window.api.sidecar.refresh()` exactly once. (Mock `window.api.sidecar.refresh`; mock `Date.now` for the time window; fire `onerror` 3× synthetically.)
    - After 3 `onerror` events but with an `onopen` interleaved, the hook does NOT call `refresh()` — successful reconnections reset the counter.
  - New Jest spec `desktop/tests/api/client.spec.ts` (or extension) asserts:
    - On `sidecar:status` event `{kind: 'refreshed', port: 9999, secretToken: 'newbearer'}`, a subsequent `sidecarFetch('/anything')` uses `http://127.0.0.1:9999/anything` with `Authorization: Bearer newbearer`. (Mock `fetch`; capture the call's URL and headers.)
    - `subscribeToConfigChanges(cb)` invokes `cb` synchronously when the cache updates. Returns an unsubscribe function that prevents future calls.
  - Manual verification (in the phase commit message): with Electron running and connected to a live sidecar, kill the sidecar via `python -m market_analyser.api stop`, start a fresh sidecar with `python -m market_analyser.api --port=0` (different port assigned), observe within ~30 s that the renderer's chart resumes receiving SSE envelopes when the agent issues a `show_chart` (the refresh fires; the new bearer + port are picked up; the `EventSource` reopens).

### Phase 4.5 — Wire overlays prop into OhlcvView + non-tautological live-chart spec

- **Owner skill:** `ui-builder`
- **What:** Close defect 3. (a) `desktop/renderer/App.tsx` passes `overlays={chartState.overlays}` to `<OhlcvView>` (currently omitted at lines 114-124). (b) `desktop/renderer/views/OhlcvView.tsx` accepts the new `overlays: ReadonlyArray<OverlaySpec>` prop and renders one indicator series per overlay. Indicator math (EMA, SMA) lives in a new helper module — keep it small; per `OverlaySpec.kind`'s literal set in Plan 0007's data shapes, MVP renders `ema` and `sma` only and logs-and-skips `rsi`/`macd`/`bbands` (the schema permits them; the renderer's "ignored unknown overlay" branch is already promised in phase 4's done-when). (c) Add a new test hook `window.__test_chart_render__` (NOT `__test_chart_state__`) that exposes `{seriesCount: number, seriesKinds: ReadonlyArray<{kind: string, period?: number | null}>}` reflecting what `lightweight-charts` actually has on the chart — read from the chart instance after each render, not from the reducer. (d) Replace the `live-chart.spec.ts` overlay assertions to use the new render-side hook AND remove the `MARKET_ANALYSER_DATA_DIR` injection workaround (lines 87, 118-120 of `live-chart.spec.ts`) — the spec must pass *without* overriding the data dir, which it can now do because phase 4.1 makes Electron's resolver agree with Python's. The JSDoc block at lines 50-57 documenting the workaround gets deleted.
- **Files touched:** `desktop/renderer/App.tsx` (pass overlays prop); `desktop/renderer/views/OhlcvView.tsx` (accept overlays, render series); new `desktop/renderer/lib/indicators.ts` (or similar) for EMA/SMA math — pure functions, no dependencies; `desktop/tests/live-chart.spec.ts` (use the new render hook; remove dataDir injection; delete the workaround JSDoc); possibly a new `desktop/tests/views/OhlcvView.spec.tsx` for the prop-rendering unit test if one doesn't already exist.
- **Done when:**
  - `desktop/tests/live-chart.spec.ts` asserts (post-amendment):
    - The `show_chart` test reads from `window.__test_chart_render__.seriesKinds` AND asserts `seriesCount >= 2` (one candlestick + one EMA overlay). The assertion that previously read from `__test_chart_state__.overlays` is REMOVED (or kept only as a reducer-side sanity check, but the rendering claim is now defended by the render hook).
    - The overlay-merge test (`chart.update merges overlays in place`) asserts `seriesKinds.filter(s => s.kind === 'ema').length === 2` after the second update — i.e. the chart actually has two EMA lines drawn, not just two entries in the reducer.
    - The spec file no longer contains `MARKET_ANALYSER_DATA_DIR` overrides (`grep -c 'MARKET_ANALYSER_DATA_DIR' desktop/tests/live-chart.spec.ts` returns 0). The Python subprocess in `callMcpTool` no longer needs the env-var override because Electron now writes to (and the subprocess reads from) the same canonical path.
    - The JSDoc workaround block at the head of `callMcpTool` is deleted.
  - New unit spec `desktop/tests/views/OhlcvView.spec.tsx` (or equivalent) asserts:
    - Rendering `<OhlcvView overlays={[{kind: 'ema', period: 20}]} ...>` with a known bars set produces exactly one EMA-series entry in `window.__test_chart_render__.seriesKinds` with `period: 20`.
    - Rendering `<OhlcvView overlays={[{kind: 'rsi', period: 14}]} ...>` does NOT produce a series (rsi is MVP-unsupported) and logs a warning. `seriesCount` reflects the candlestick only.
  - Manual verification (in the phase commit message): open the Electron app via `pnpm dev`; via Claude Code call `show_chart(symbol="AAPL", overlays=[{kind:"ema", period:20}])`; observe one EMA line drawn on the chart, distinct from the candlesticks.

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
- **Risk: `mcp` Python SDK or `sse-starlette` dependency churn within the cooldown window.** Pin exact versions per [ADR-0013](../../adrs/0013-pin-direct-dependencies.md); cooldown per [ADR-0012](../../adrs/0012-dependency-cooldown.md). Phase 1 commit message names the SDK version pinned.
- **Risk: `psutil` is platform-specific and adds install cost.** `psutil` is widely-used and cross-platform; the cost is acceptable. If avoiding the dep is desired, a per-platform shim (`os.kill(pid, 0)` on POSIX + `ctypes.windll.kernel32.OpenProcess(...)` on Windows) is possible at the cost of more code. Default to `psutil`; flip if review finds the dep weight excessive.
- **Risk: `chart.highlight` overlapping with `write_annotation` confuses agents.** Both result in a marker on the chart. The doc string for `highlight_pattern` must explain: `highlight_pattern` is for patterns the agent detected NOW (persisted + live); `write_annotation` is the lower-level primitive (persisted only). Phase 3 includes this in the tool docstring; phase 5 confirms it reads well in Claude Code's tool surface.
- **Open question: should the `OverlaySpec` literal set include indicators we haven't built yet?** Pre-declaring `rsi`, `macd`, `bbands` in the literal means an agent can request them; the renderer-side renders only what it knows; unknown kinds are logged and ignored. Pre-declaring is forward-friendly; the alternative is to add overlay kinds one at a time. Default to a small pre-declared set; the renderer's "ignored unknown overlay" path is part of phase 4's test surface.
- **Open question: `show_chart` `range_start`/`range_end` semantics — inclusive both ends?** Default to inclusive on both, matching how Plan 0006 specifies `/annotations` and `/ohlcv`. Phase 3 done-when includes a boundary test.
- **Risk (added by Amendment 2026-05-22): the cross-resolver consistency test in phase 4.1 is platform-conditional.** A spec that spawns a Python subprocess from a Node test runner is non-trivial across Windows / macOS / Linux. Mitigation: the per-side tests are the primary defence; the cross-resolver test is a belt-and-braces check that may end up `it.skipIf(process.platform !== "win32")` in practice. Acceptable if the per-side tests both pass.
- **Risk (added by Amendment 2026-05-22): phase 4.4's "3 errors in 10 seconds" heuristic may misfire on a flaky network.** The renderer would invoke `sidecar:refresh` unnecessarily, generating churn but no incorrect state (refresh is idempotent for a healthy sidecar). If it becomes a nuisance in practice, the threshold can be tuned without an ADR change.
- **Risk (added by Amendment 2026-05-22): `app.setName('market-analyser')` interaction with Electron's single-instance lock.** Electron's single-instance behaviour can key on `app.getName()`. We don't currently enforce single-instance for the viewer (Plan 0007 explicitly accepts two viewers). If we add single-instance later (a future plan), the lock will key on `'market-analyser'`, which is the right behaviour anyway. No mitigation needed; flagged for the next reader.

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
- **Migration of existing data from old paths to the canonical path (Amendment scope).** If a developer's machine has stale state at `%APPDATA%\@market-analyser\desktop\` from a pre-amendment dev run, this plan does NOT migrate it. The dev hand-cleans (per the recovery commands in `.claude/smoke-handoff-plan-0007.md`). Production-installed users were never on the divergent path because packaged builds use `productName = "market-analyser"`. A migration helper would be over-engineering for a one-time dev-side issue.
- **A new ADR superseding ADR-0016's data-dir guidance (Amendment scope).** ADR-0020 *refines* ADR-0016's data-dir half rather than superseding it. The lockfile + idempotent-attach mechanism from ADR-0016 stays exactly as written; only the path-resolution algorithm is made contractual. ADR-0016 remains `accepted`.

## Followups (after this lands)

- **Hardening rationale (Amendment 2026-05-22).** Phases 4.1-4.5 were added after the Phase 5 smoke surfaced four defects on 2026-05-21 — see `.claude/smoke-handoff-plan-0007.md` for the original handoff and [ADR-0020](../../adrs/0020-shared-data-dir-contract.md) for the contract the hardening codifies. The close ceremony rolled all post-amendment review findings AND smoke bugs into this section per the amendment plan.

### Close-review findings (architect, 2026-05-22)

- **Diagram refresh (minor).** `docs/architecture/diagrams/claude-cli-driven-architecture.md` doesn't yet show the `/healthz` data-dir identity check (phase 4.2) on the cold-start sequence, nor the renderer-initiated `refresh()` sequence (phase 4.3+4.4). Refreshed as part of close ceremony. Owner: `architect` (done at close).
- **Warn-message structure (minor) — `desktop/tests/main/sidecar-supervisor.spec.ts:359, 388`.** The non-200 and throw fall-through tests only assert `warnSpy.toHaveBeenCalled()`; the mismatch test at `:335-337` asserts both paths in the warn body. Extend the same pattern to the other two cases so the diagnostic-quality claim is symmetric. Owner: `dev`.
- **Lazy `cached` window (nit) — `desktop/renderer/api/client.ts:67`.** A `refreshed` status event arriving before the very first `sidecarFetch` would be silently dropped by the `if (cached)` guard. Window is microscopic in practice (`useEventStream` calls `buildEventsUrl` on mount). Document, don't fix. Owner: `ui-builder` (document in code comment if it ever bites).
- **Refresh-storm gate is open-driven (nit) — `desktop/renderer/hooks/useEventStream.ts:155-161`.** Once `refresh()` fires, no further refresh attempts until `onopen` succeeds. If the new sidecar also can't be reached, the renderer stays in `reconnecting` indefinitely. Defensible (avoids infinite loop). If it becomes a UX issue, an exponential-backoff retry can be added without an ADR change. Owner: `ui-builder` (if/when reported).
- **`computeOverlayData` silent skip (nit) — `desktop/renderer/components/CandlestickChart.tsx:201-209`.** Returns `[]` for ema/sma when `period` is null/undefined. The supported-kind path warns; the missing-period path doesn't. Add a `console.warn` to surface agent omissions. Owner: `ui-builder`.

### Smoke bugs (user, 2026-05-22)

- **Chart clipped when zoomed in (minor).** Likely a CSS overflow / `lightweight-charts` `autoSize` interaction with the chart container's parent layout. Reproducible at any zoom level. Owner: `ui-builder`.
- **SymbolPicker doesn't sync when agent changes symbol (major).** The reducer correctly updates `chartState.symbol` on `event/chart.show`; App.tsx passes it through to `<OhlcvView symbol={chartState.symbol}>`; `<SymbolPicker symbol={symbol}>` receives it as a prop. The picker UI still displays the old symbol — almost certainly `SymbolPicker` holds local state and doesn't sync via `useEffect` when its `symbol` prop changes. One-line fix in `desktop/renderer/components/SymbolPicker.tsx`. Owner: `ui-builder`.
- **KeyboardInterrupt traceback on sidecar Ctrl+C (nit) — `src/market_analyser/api/__main__.py:206`.** `asyncio.run(_serve(...))` raises `KeyboardInterrupt` on Ctrl+C; the `finally` block still removes the lockfile (verified by the user), so functionally correct, but the traceback is noise. Wrap in `try/except KeyboardInterrupt: pass` at the top of `_run_serve` or `main`. Owner: `dev`.
- **Smoke procedure missing `--dev-origin` (procedural nit).** The manual-start smoke command in the architect's instructions didn't include `--dev-origin=http://localhost:5173`. Electron's spawn path adds it automatically (`desktop/electron/sidecar.ts:183`); a manually-started sidecar needs it explicit, otherwise the renderer's `/ohlcv` calls fail with CORS-rejected "Failed to fetch". Owner: `architect` (folded into any future smoke-procedure doc; if `docs/onboarding/claude-code-setup.md` from Phase 5 is written, name the flag).
- **Auto-backfill on cache miss (architectural, needs its own plan).** Today `get_ohlcv` reads from SQLite only; cache miss returns empty bars and the agent has to invoke a separate `/dev` path to populate. Cache-first / fetch-on-miss has design questions (when does fetch fire, partial misses, concurrency, backpressure, failure handling) — out of Plan 0007 scope. Queued as **Plan 0013** (architect to design, then `dev` to implement).
- **Interactive chart with agent-mode toggle (architectural, needs its own plan).** User wants pan/zoom plus drag-to-select-range with notification back to the agent, gated by a user-toggleable agent mode. Bidirectional protocol (renderer → sidecar → agent) was explicitly out of scope per "What this plan does NOT do" — needs a new ADR (WebSocket vs reverse-HTTP vs polling) and a plan covering interactive mode + mode toggle + the new event flow. Queued as **Plan 0014** (architect to design after Plan 0013 lands).
