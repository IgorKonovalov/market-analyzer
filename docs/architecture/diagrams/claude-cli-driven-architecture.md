# Claude-CLI-driven architecture

Source of truth for the post-[ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) layout — Claude Code as the primary control surface, Electron as the live viewer, sidecar standalone-capable. Update whenever a transport, route, or persisted secret changes shape.

## Component map

```mermaid
flowchart LR
    User[Human at keyboard]

    subgraph CLI["Claude Code (CLI) — primary control surface"]
        Claude["Claude<br/>(MCP client built-in)"]
    end

    subgraph Sidecar["Python sidecar (standalone process)"]
        MCPRoute["/mcp<br/>Streamable HTTP<br/>(mcp-secret bearer)"]
        Routes["/healthz, /ohlcv, /annotations,<br/>/backtests, /events (SSE),<br/>/settings/mcp-secret/rotate<br/>(renderer bearer)"]
        Bus["In-process event bus<br/>(per-subscriber asyncio queue)"]
        Tools["MCP tools<br/>get_ohlcv, list/write_annotation,<br/>show_chart, update_chart,<br/>highlight_pattern, run_backtest"]
        DL["MarketDataProvider +<br/>repositories"]
        Engine["Backtest engine<br/>(pure run + persist)"]
        Cache[("SQLite cache.sqlite<br/>bars, annotations,<br/>backtest_runs")]
        MCPRoute --> Tools
        Tools --> DL
        Tools --> Bus
        Tools --> Engine
        Engine --> DL
        Engine --> Cache
        Engine --> Bus
        Routes --> DL
        Bus --> Routes
        DL --> Cache
    end

    subgraph UD["User data dir (mode 0600 files)"]
        Lockfile[("sidecar.lock<br/>pid, port, renderer_secret,<br/>process_create_time")]
        MCPSecret[("mcp-secret.json")]
    end

    subgraph Electron["Electron viewer (optional, attachable)"]
        View["Main process + Renderer<br/>(React, lightweight-charts)<br/>charts · equity curves · trade log"]
    end

    User -- "types prompts" --> Claude
    User -- "watches charts" --> View
    Claude -- "HTTP Streamable<br/>Bearer: mcp-secret" --> MCPRoute
    View -- "HTTP<br/>Bearer: renderer_secret" --> Routes
    View -. "EventSource SSE<br/>?token=<renderer_secret>" .-> Routes
    View -. "idempotent attach<br/>or spawn if no live PID" .-> Sidecar
    Sidecar -. "writes on boot<br/>(rotated per sidecar launch)" .-> Lockfile
    View -. "reads on attach" .-> Lockfile
    MCPRoute -. "reads" .-> MCPSecret
```

Boundaries:

- **CLI** is Claude Code with its built-in MCP client. The user's primary input device — symbols, timeframes, strategies, overlay choices, backtest parameters, render commands all originate here.
- **Sidecar** is the single Python process. It serves two transports on one loopback port: MCP at `/mcp` (Streamable HTTP, agent-facing, per [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md)) and the renderer HTTP routes (viewer-facing, per [ADR-0002](../adrs/0002-ipc-local-http.md)). Both transports share the same data layer, repositories, and SQLite cache. The backtest engine (pure `run` + thin `persist`, per [ADR-0018](../adrs/0018-backtest-result-schema.md)) sits behind the `run_backtest` tool, persists to the `backtest_runs` table, and emits `run.completed v1`. The event bus is in-process — MCP tools and the engine publish; the SSE handler at `/events` is the sole subscriber-dispatch.
- **User data dir** holds the two persisted secrets, each `0600`. `mcp-secret.json` is long-lived (ADR-0014); `sidecar.lock` is per-sidecar-launch (ADR-0016).
- **Electron viewer** is optional. The sidecar runs without it; opening Electron attaches to the running sidecar via the lockfile (or spawns one if none is running). Closing Electron does not stop the sidecar.

Critical invariants:

- **Cross-tenant bearer isolation.** MCP secret authenticates only on `/mcp/*`. Renderer secret authenticates only on the renderer routes. Each middleware uses constant-time comparison; the dispatcher routes by prefix.
- **Single sidecar instance per user.** Enforced by `sidecar.lock` + PID liveness probe with `process_create_time` cross-check (ADR-0016).
- **SQLite single-writer.** Falls out of the single-instance enforcement.
- **No lookahead in agent-facing tools.** `get_ohlcv` and bar-reading tools forward `as_of=None` (live mode). The backtest-aware path is the separate `run_backtest` tool (per [ADR-0018](../adrs/0018-backtest-result-schema.md)), not an `as_of` parameter bolted onto `get_ohlcv`; the engine enforces the next-bar-open fill seam internally (see [`strategy-execution-sequence.md`](strategy-execution-sequence.md)) and re-runs deterministically (per ADR-0014 and the [CLAUDE.md non-negotiable](../../../CLAUDE.md#cross-cutting-non-negotiables)).

## Lifecycle: cold start vs. attach vs. agent-only

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant E as Electron main
    participant LF as sidecar.lock
    participant S as Python sidecar
    participant C as Claude Code (MCP)
    participant V as Renderer

    Note over User,V: Cold start (no sidecar running)
    User->>E: open app
    E->>LF: read
    LF-->>E: not present
    E->>S: spawn(python -m market_analyser.api --port=0)
    S->>LF: atomic write {pid, port, renderer_secret, ...}
    S-->>E: PORT=53221 on stdout
    E->>LF: read renderer_secret + port
    E->>V: launch with {port, renderer_secret}
    V->>S: GET /healthz (renderer bearer)
    V->>S: open EventSource /events?token=<secret>

    Note over User,V: Agent-only (no Electron open)
    User->>C: "show me AAPL 1d with EMA20"
    C->>S: MCP show_chart(...)
    S->>S: publish chart.show v1 to event bus
    S-->>C: {event_published: true}
    Note right of S: no subscribers — event dropped<br/>(user can open Electron and re-ask)

    Note over User,V: Attach (Electron opens, sidecar already running)
    User->>E: open app
    E->>LF: read
    LF-->>E: {pid: 12345, port: 53221, renderer_secret, ...}
    E->>S: probe psutil.Process(12345).create_time()
    Note right of E: matches lockfile (Python-side)
    E->>S: GET /healthz (Authorization: Bearer renderer_secret)
    S-->>E: {ok: true, data_dir: "/.../market-analyser"}
    Note right of E: data_dir matches resolveSharedDataDir()<br/>(ADR-0020, phase 4.2)<br/>→ attach, do NOT spawn<br/>on mismatch: fall through to spawn
    E->>V: launch with {port, renderer_secret}
    V->>S: open EventSource /events
    User->>C: "now zoom to last 10 days"
    C->>S: MCP update_chart(...)
    S->>S: publish chart.update v1
    S-->>V: SSE: chart.update envelope
    V-->>User: chart re-renders live

    Note over User,V: Electron close (sidecar survives)
    User->>E: quit app
    E--xV: window closed
    Note right of E: before-quit handler does NOT<br/>signal the sidecar (ADR-0016)
    Note over S: sidecar keeps running<br/>lockfile unchanged
```

The "agent-only" branch is the visible payoff of [ADR-0016](../adrs/0016-standalone-sidecar-mode.md): Claude can drive workflows without the viewer being open. The "attach" branch is the payoff of the lockfile mechanism: opening the viewer is one step regardless of prior sidecar state.

## Recovery: renderer-initiated refresh on out-of-band sidecar restart

Standalone-mode sidecars (ADR-0016) don't restart from Electron's perspective — the supervisor decoupled lifecycle. But the sidecar *can* die out-of-band (user `python -m market_analyser.api stop` followed by a fresh start, or a manual kill). The renderer discovers and recovers via the sequence below.

```mermaid
sequenceDiagram
    autonumber
    participant V as Renderer<br/>useEventStream
    participant E as Electron main<br/>SidecarSupervisor
    participant LF as sidecar.lock
    participant S1 as Old sidecar (dead)
    participant S2 as New sidecar (alive)

    Note over V,S2: Steady state: old sidecar dies, user starts a new one
    V->>S1: EventSource open
    Note over S1: process exits<br/>finally → remove lockfile
    S2->>LF: atomic write {new pid, new port, new renderer_secret}
    V->>S1: ❌ onerror (connection lost)
    V->>S1: ❌ onerror
    V->>S1: ❌ onerror
    Note right of V: 3 errors within 10s<br/>without an intervening onopen<br/>(phase 4.4 threshold)
    V->>E: window.api.sidecar.refresh() (IPC)
    E->>E: SidecarSupervisor.refresh()
    Note right of E: re-runs attachOrSpawnSidecar:<br/>reads lockfile, runs PID + healthz check
    E->>LF: read
    LF-->>E: {new pid, new port, new renderer_secret}
    E->>S2: GET /healthz (Bearer new renderer_secret)
    S2-->>E: {ok: true, data_dir: matches}
    E-->>V: sidecar:status {kind: refreshed, port: NEW, secretToken: NEW}
    Note right of V: api/client cache updates<br/>both port + secretToken<br/>subscribeToConfigChanges fires
    V->>V: close old EventSource
    V->>S2: open EventSource /events?token=NEW
    S2-->>V: : ping
    V-->>V: onopen → state=open, error counter reset
```

The `refresh()` call coalesces concurrent invocations (phase 4.3) so a renderer-side error storm collapses to one attach cycle upstream. The "refresh fires at most once per `onopen`-bounded window" rule (phase 4.4) prevents an infinite refresh loop if the new sidecar also can't be reached.

## Companion diagrams

This file owns the **process/component map and the sidecar lifecycle**. Two companions cover narrower cuts:

- [`bootstrap-component-map.md`](bootstrap-component-map.md) — the OHLCV walking-skeleton **data flow** (renderer → sidecar → provider → cache/Yahoo) and the **SQLite schema**. Reach for it when the question is "how does a single chart request resolve" or "what shape is a table", not "how do the processes fit together".
- [`strategy-execution-sequence.md`](strategy-execution-sequence.md) — the **backtest runtime order** and the next-bar-open anti-lookahead seam. Independent of which client (agent vs viewer) requested the run.
