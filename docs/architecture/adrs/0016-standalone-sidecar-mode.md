# ADR-0016 — Standalone sidecar mode with idempotent attach

> **Status:** accepted
> **Date:** 2026-05-20
> **Related plan(s):** [0007-live-agent-driven-viewer](../plans/0007-live-agent-driven-viewer.md)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md) (renderer ↔ sidecar transport), [ADR-0011](0011-bearer-secret-transport.md) (per-launch bearer — **refined here**), [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (closes deferred standalone question), [ADR-0015](0015-claude-code-primary-control-surface.md) (motivates this)

## Context

[ADR-0014](0014-mcp-as-second-sidecar-protocol.md) explicitly deferred standalone sidecar mode:

> The sidecar's lifecycle is unchanged: Electron spawns it, supervises it, and kills it on app quit. MCP clients see "server unreachable" when the app is closed. Standalone-sidecar mode is explicitly out of scope of this ADR and is a future decision if the workflow demands it.

[ADR-0015](0015-claude-code-primary-control-surface.md) makes the workflow demand it. With Claude Code as the primary control surface, agent-driven workflows must remain reachable when the Electron viewer is closed. "MCP unavailability coupled to UI window state" is the exact behaviour that the role inversion forbids.

The user's stated lifecycle preference (this session): *"independent process that is also automatically started when electron starts."* Three properties live in that sentence:

1. **Independent** — the sidecar's life is not nested inside Electron's. Closing the window does not stop the sidecar.
2. **Auto-started on Electron open** — opening the viewer does not require the user to first run a separate command in a terminal. If the sidecar is already running, the viewer attaches; if not, the viewer starts it.
3. **Single instance per user** — having two sidecars open SQLite write-mode at the same time is a correctness defect (write-lock contention) and a data defect (two caches drifting). The architecture has to enforce "one or zero".

Three forces shape the mechanism:

- **The renderer bearer must remain reachable across Electron attaches.** [ADR-0011](0011-bearer-secret-transport.md) generates the renderer bearer per-launch and passes it from the Electron main process via the `MARKET_ANALYSER_SECRET` env var. That works exactly when Electron is the supervisor — it generates the secret, spawns the sidecar with it, holds the value in memory, and ends both at quit. When Electron is **not** the supervisor — when it attaches to an already-running sidecar — there is no shared in-memory channel for the secret to ride on. The bearer must be discoverable from disk by whoever attaches.
- **Single-instance enforcement is hardest in the presence of crashes.** A clean shutdown can remove a lockfile; a `SIGKILL` or a kernel panic cannot. The check has to handle "stale lockfile pointing at a dead or reused PID" without spurious "sidecar already running" failures that prevent the user from ever starting it again.
- **The platforms differ on what we can probe cheaply.** POSIX has `/proc/<pid>` (Linux) and `kill(pid, 0)` for liveness; macOS has `kill(pid, 0)` and `ps`; Windows has no `/proc`, requires `OpenProcess` semantics, and PID reuse is faster. `psutil` is the cross-platform abstraction we can trust here.

The decision is non-obvious because we are downgrading a property [ADR-0011](0011-bearer-secret-transport.md) was specifically written to provide ("never persisted to disk"), and because lockfile-based single-instance has real failure modes that the previous design (Electron supervises a single child) did not.

## Decision

We will detach the sidecar's lifecycle from Electron and enforce single-instance via a lockfile, with idempotent attach from any client.

**Lockfile.** The sidecar maintains a single lockfile at `<user-data>/sidecar.lock`, mode `0600` on POSIX (same discipline as `mcp-secret.json` per ADR-0014). The file is JSON with this shape:

```json
{
  "pid": 12345,
  "port": 53221,
  "renderer_secret": "<64 hex chars>",
  "started_at": "2026-05-20T14:23:01.500Z",
  "process_create_time": 1747749781.5,
  "sidecar_version": "0.1.0"
}
```

The file is written atomically (write to `sidecar.lock.tmp` then `os.replace`) on sidecar boot, before the FastAPI app accepts its first request. It is removed by the sidecar in a `finally` block on clean shutdown (`SIGTERM`, `SIGINT`, or normal exit).

**Single instance.** On boot, the sidecar reads the lockfile (if present) and runs a liveness probe:

1. If no lockfile, proceed (cold start).
2. If the lockfile exists and `psutil.Process(pid).create_time()` ≈ the lockfile's `process_create_time` (within ±5s, to absorb timer skew), the existing PID **is** the prior sidecar — refuse to start. Exit non-zero with stderr `sidecar already running at PID <N>, port <M>; stop it first`.
3. If the lockfile exists but the PID is gone, or the PID is alive but `create_time` doesn't match (PID reuse), the lockfile is stale — take it over. Log a one-line warning naming the prior PID.

**Idempotent attach from Electron.** Electron's main process, on app boot:

1. Reads `sidecar.lock` if present and runs the same liveness probe as above.
2. If a live sidecar matches, attach: use the lockfile's `port` and `renderer_secret` as if Electron had spawned it. Skip the spawn entirely.
3. If no live sidecar, spawn one via the existing `python -m market_analyser.api --port=0` command. Wait for the lockfile to appear and contain a `port` (the sidecar writes it before `uvicorn` accepts requests). Read the bearer from the lockfile, not from the env var the main process passed in. (The env var path remains for compatibility but is now the *fallback* for cold spawn; the lockfile is the source of truth once the sidecar has booted.)

**Electron quit does not stop the sidecar.** The Electron main process's existing `before-quit` handler must NOT signal the sidecar. The sidecar continues running until the user stops it explicitly (`Stop sidecar` button in Settings, or `python -m market_analyser.api stop` from a terminal, which reads the lockfile's PID and sends `SIGTERM`).

**Renderer bearer lifecycle.** The bearer rotates on every **sidecar** boot (not on every Electron launch). Existing Electron sessions that hold a stale bearer will receive `401` on their next request after a sidecar restart; the renderer's existing reconnect handling treats this the same as a sidecar restart today (it re-reads the lockfile and re-attaches). This is a deliberate refinement of [ADR-0011](0011-bearer-secret-transport.md): the rotation property is preserved (each sidecar boot gets a fresh secret), the "lives only in process memory" property is downgraded (the secret is now also on disk in the `0600` lockfile for the lifetime of the sidecar process).

The lockfile is removed on sidecar exit, so the secret's on-disk lifetime equals the sidecar's runtime — exactly as before for the secret's in-memory lifetime. Within that window, the file's `0600` mode and user-data-dir location give the same threat-model accept that `mcp-secret.json` was given in [ADR-0014](0014-mcp-as-second-sidecar-protocol.md).

## Consequences

### Positive

- **Agent-driven workflows work without Electron.** Closing the viewer no longer breaks Claude's session. Overnight scans, scheduled analyses, headless backtests become possible without further architecture.
- **Idempotent attach makes the viewer cheap to open and close.** No "start the backend first" friction; opening Electron is a one-step act regardless of prior sidecar state.
- **Single-writer SQLite is preserved by construction.** Two sidecars cannot run; lockfile enforcement makes the "two processes contending for the cache" failure mode impossible by design.
- **The CLI entrypoint (`python -m market_analyser.api`) becomes first-class.** Users who want a fully terminal-based workflow (no Electron at all) can run the sidecar standalone and use Claude Code against it. This is a free emergent property of the lifecycle change.
- **Crash recovery is bounded.** A stale lockfile is corrected on the next start attempt (Electron or CLI); the user does not need to know the file exists.

### Negative

- **The renderer bearer is now on disk in the lockfile.** This is a downgrade from [ADR-0011](0011-bearer-secret-transport.md)'s "lives in process memory of exactly two processes" property. The threat model is unchanged from [ADR-0014](0014-mcp-as-second-sidecar-protocol.md)'s `mcp-secret.json`: a same-user process can read the `0600` file; cross-user reads are gated by POSIX permissions but not by Windows ACLs. We accept this cost as the price of detaching from Electron's lifecycle. The mitigation that does *not* apply here (rotation on every Electron launch) is replaced by a weaker but still-meaningful mitigation (rotation on every sidecar launch).
- **PID-reuse race window.** Between sidecar exit and the next attach probe, the OS may reuse the sidecar's PID for an unrelated process. The `process_create_time` cross-check closes the obvious window but not a tightly-timed adversarial one. We accept this as acceptable for a local desktop app's threat model; in the adversarial case the worst outcome is Electron attempts to attach to the wrong process, fails (no `port` field at the expected URL, no 200 from `/healthz`), and falls back to spawning a fresh sidecar.
- **No automated crash-restart.** ADR-0002's "restart once on crash" pattern in the Electron supervisor is gone in standalone mode (no supervisor). If the sidecar dies during agent use, the user must restart it manually (CLI or by re-opening Electron). A tray-app supervisor or a service-manager integration (LaunchAgent / systemd-user / Task Scheduler) is a future ADR if the manual-restart UX becomes painful.
- **Two start paths means twice the failure-mode coverage in tests.** The cold-spawn path (Electron starts a fresh sidecar) and the attach path (Electron finds an existing one) are both first-class. Tests must cover both.
- **Onboarding now has to explain "the sidecar can outlive the app".** Users who close the viewer expecting "the whole thing stopped" will be surprised. Documentation must call this out. A Settings affordance to stop the sidecar from inside the viewer is the minimum UX defence; that's part of Plan 0007.
- **The `stop` subcommand needs to be safe.** `python -m market_analyser.api stop` reads the lockfile's PID and sends `SIGTERM`. If the lockfile is stale, this could SIGTERM an unrelated PID. The `process_create_time` check on stop is the same defence as on start.

### Neutral

- **The MCP secret (`mcp-secret.json`) is unchanged.** It was already designed for cross-process-lifetime persistence per ADR-0014. The renderer bearer's lifetime now extends similarly. The two persisted secrets live in the same user data directory under the same `0600` discipline.
- **Sidecar startup with `PORT=<n>` on stdout stays.** In Electron-spawn mode, the main process still captures it for the "wait for ready" gate. In standalone mode the user sees it directly. The lockfile is the *durable* discovery channel; stdout is the *transient* one.
- **The threat model for `127.0.0.1` binding is unchanged.** The sidecar still binds loopback only; other machines on the LAN cannot reach it. Local processes still can, gated by the bearers; that hasn't moved.

## Alternatives considered

### Alternative A — Electron remains the supervisor (status quo)

Reject the lifecycle change. Claude Code only works while Electron is open. The renderer bearer stays in env-var-only form per [ADR-0011](0011-bearer-secret-transport.md).

Rejected because it contradicts the role-inversion decision in [ADR-0015](0015-claude-code-primary-control-surface.md). Tying the primary control surface to a UI window being open is the exact constraint the role inversion removes.

### Alternative B — Dedicated supervisor process (tray-app or OS service)

A separate small process owns the sidecar's lifecycle: a tray app (cross-platform via Electron-itself or a smaller framework), a macOS LaunchAgent, a Linux systemd-user unit, a Windows Task Scheduler entry. Electron and Claude both attach as clients.

Rejected because the platform-specific install / startup integration is non-trivial work for a property (no manual restart on crash) that is desirable but not blocking. Lockfile-based single-instance + idempotent attach gets us 80% of the value with much less code and no platform-specific installers. A future ADR can promote a supervisor if manual restart becomes a real pain.

### Alternative C — Sidecar is HTTP-server-only; Claude Code spawns a stdio MCP wrapper that proxies

Keep Electron-supervises-sidecar; add a thin Python MCP wrapper that Claude Code spawns via stdio; the wrapper proxies tool calls to the sidecar over HTTP.

Rejected because the existing MCP-over-HTTP transport at `/mcp` (per ADR-0014) already works with Claude Code as-is — Claude Code's MCP client supports HTTP transports natively. Adding a stdio wrapper would duplicate the auth surface (the wrapper needs to know the MCP secret), increase the process count, and add latency, all for no improvement over what's already shipped. It also leaves Alternative A's "MCP requires Electron" property in place, which is the thing we want to fix.

### Alternative D — Lockfile carries only `pid + port`; renderer learns the bearer via a `/handshake` endpoint

Avoid persisting the bearer to disk by making the renderer call `POST /handshake` after attach. The handshake endpoint issues a fresh short-lived token.

Rejected because the handshake endpoint must itself be authenticated — otherwise any local process can call it and obtain a token. The chicken-and-egg dissolves only if the handshake is on a separate authenticated channel (which doesn't exist) or if it's accessible unauthenticated (which gives the same threat-model exposure as putting the bearer in the lockfile, plus a new endpoint to maintain). Persisting the bearer in the lockfile is the simpler outcome with the same security properties.

### Alternative E — No single-instance enforcement; let the user shoot themselves in the foot

Skip the lockfile; let two sidecars run if the user opens two Electrons or runs the CLI while Electron is open.

Rejected because SQLite write contention from concurrent writers manifests as confusing errors at unpredictable moments. The cost of the lockfile (a few dozen lines) is much smaller than the cost of one user-reported "the chart is showing wrong data" incident traced to a second sidecar quietly running.

## Notes

- The exact stop semantics (CLI subcommand, Settings button, both) is decided in Plan 0007 phase 1, not here. This ADR sets the lifecycle and discovery model; the UX surface for "stop" follows.
- The `process_create_time` cross-check uses `psutil.Process(pid).create_time()` and tolerates ±5s skew because some platforms round to integer seconds and others provide sub-second precision. The tolerance is a defence against false negatives; PID reuse within 5s of an unrelated process death and an attach attempt would defeat it. We accept this residual window.
- The "stop sidecar" Settings button should warn the user when it would interrupt an active MCP session ("Claude Code is connected and may have queued work"). The mechanism for detecting "Claude Code is connected" is "there is an MCP session active" — the sidecar already tracks this internally for cleanup.
- A future ADR may introduce a "PID file convention" shared with other in-house Python services if more accrete in this user data directory. Today the lockfile is internal and the schema can evolve freely.
