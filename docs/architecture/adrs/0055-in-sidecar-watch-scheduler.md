# ADR-0055 — Alerting runs as an in-sidecar scheduler; alerts are edge-triggered conditions

> **Status:** accepted (Plan 0060 close, 2026-07-03)
> **Date:** 2026-06-09
> **Related plan(s):** 0060-watchlist-alerting-loop (implements)
> **Related ADRs:** [ADR-0016](0016-standalone-sidecar-mode.md) (the long-lived process this rides), [ADR-0017](0017-live-ui-updates-via-sse.md) (the delivery channel), [ADR-0021](0021-renderer-to-agent-feedback.md) (the agent-pollable pending-events path alerts reuse), [ADR-0029](0029-advisory-recommendation-boundary.md) (the boundary alerts must not cross)

## Context

Every signal-shaped capability in the app is pull-on-demand: `analyze_symbol`, `evaluate_signals`, `forecast` run when the agent or viewer asks. Nothing can say "BTC daily RSI just crossed 30" unprompted. The user has asked for a watch/alert loop as part of the crypto program.

The architectural question is **where the clock lives**. The sidecar is already a standalone long-lived process that outlives the viewer (ADR-0016), owns the data cache, and has a working event fan-out (SSE, ADR-0017) plus an agent-pollable event queue (ADR-0021). The alternatives — Electron-main timers, OS scheduler, or an agent-side polling loop — all put the clock in a process that may not be running or that costs tokens to keep alive.

A second question is **what an alert is allowed to say**. The analyst non-negotiable ("conditions are facts, decisions are the user's") and ADR-0029's advisory boundary both predate alerting; an alert that says "buy" from a background loop would be the advisor's job done without the advisor's basis discipline.

## Decision

We will run alerting as an **asyncio background task inside the sidecar's lifespan** (started/stopped with the app, like the ADR-0022 lifespan pattern). Watch definitions and alert history persist in SQLite (one migration). Each watch names a symbol, timeframe, an evaluation kind that reuses an existing read-only primitive — condition threshold (indicator vs level), pattern occurrence, or strategy signal via `evaluate_signals` — and a check interval defaulting to the watch's bar period. **Alerts are edge-triggered:** a watch fires when its predicate transitions false→true across consecutive evaluations, not on every poll while true; each fire records an alert row and emits an `alert.triggered v1` SSE event, delivered to the viewer (live panel/toast) and to the agent via the existing pending-events poll. **Alert payloads state conditions only** — the triggering fact, values, timestamps; never a directive. Evaluation calls the same cached-bars path as the on-demand tools; a watch evaluation must never silently fetch beyond what the existing backfill/coordinator rules allow.

## Consequences

### Positive
- The clock lives in the one process that's already always-on, already has the data, and already has both delivery channels — no new infrastructure, no token cost while idle.
- Edge-triggering plus persisted alert history gives "what fired while I was away" for free (the agent polls pending events on session start).
- Reusing `evaluate_signals` / snapshot primitives means a watch is exactly as lookahead-safe and tested as the on-demand call it wraps.

### Negative
- **The sidecar grows a duty cycle.** A formerly purely-reactive process now does periodic work: scheduled upstream fetches (rate-limit exposure scales with watch count), background CPU, and a new failure mode (a wedged scheduler kills alerting silently). Plan 0060 must include a scheduler heartbeat/health surface.
- Wall-clock scheduling is inherently non-deterministic; it must stay **outside** the financially-meaningful path. The evaluation itself (bars in → predicate out) stays pure and testable; only the trigger timing is wall-clock.
- Alert latency is bounded by check interval — this is a bar-close alerting system, not a tick-level one. Sub-interval moves are missed by design.

### Neutral
- Forecast-based watches ("alert when prob_up > 0.7") are deliberately deferred: forecast calls are heavyweight (walk-forward per call) and the marginal-edge qualifier story (Plan 0050) is still settling. The watch vocabulary is extensible; that kind can come later without schema change.

## Alternatives considered

### Alternative A — Agent-side loop (Claude Code `/loop` or cron polling the tools)
Zero sidecar change. Rejected as the *primary* mechanism: it only works while an agent session is alive and paying tokens per poll; an always-on watch belongs in the always-on process. (Agent loops remain fine ad hoc on top.)

### Alternative B — Electron-main timers
The viewer schedules checks via the existing IPC. Rejected: the viewer is the one component documented as optional and closeable (ADR-0016) — alerts must not die when the window closes.

### Alternative C — OS scheduler (Task Scheduler / cron) invoking a CLI
Process-supervision for free. Rejected: every invocation pays cold-start, has no in-process state for edge-detection (would need to re-derive last-state from disk anyway), is platform-divergent, and still needs the sidecar running to deliver SSE — at which point the sidecar may as well own the clock.
