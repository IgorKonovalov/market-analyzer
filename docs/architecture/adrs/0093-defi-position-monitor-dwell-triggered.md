# ADR-0093 — DeFi LP position monitoring runs as a dedicated dwell-triggered scheduler

> **Status:** accepted (2026-07-17, at Plan 0099 close — the monitor is live: dwell reducer + `defi.position_alert v1` + MCP watch CRUD shipped `1b018e9`/`1a8df6c`, proven on the real wallet in the phase-5 smoke)
> **Date:** 2026-07-13
> **Related plan(s):** [0099-defi-position-out-of-range-monitor](../plans/0099-defi-position-out-of-range-monitor.md) (implements)
> **Related ADRs:** [ADR-0055](0055-in-sidecar-watch-scheduler.md) (the market-alert scheduler this is a sibling to), [ADR-0016](0016-standalone-sidecar-mode.md) (the long-lived process it rides), [ADR-0017](0017-live-ui-updates-via-sse.md) / [ADR-0094](0094-os-native-notification-transport.md) (delivery), [ADR-0021](0021-renderer-to-agent-feedback.md) (the agent-pollable pending-events path it reuses), [ADR-0029](0029-advisory-recommendation-boundary.md) (the boundary its payloads must not cross), [ADR-0035](0035-defi-domain-placement.md) (the `defi/` domain it lives in)

## Context

A concentrated-liquidity LP earns fees only while the pool's current tick sits inside the position's `[tick_lower, tick_upper)` range. When price leaves the range the position stops generating fees and silently becomes idle inventory — the exact situation the user hit on Base wallet `0xae5b…9790`. The data plumbing to *detect* this already exists and is read-only: `RpcLpDetailAdapter` reads the position's ticks + current tick over Base RPC and computes `in_range`; `enrich_lp_positions` folds that onto each `DefiPosition`; the `scan_wallet` tool surfaces it. What is missing is anything that watches on a clock and says so unprompted.

ADR-0055 already answered "where the clock lives" for market conditions: an in-sidecar asyncio scheduler, edge-triggered, keyed by `(symbol, timeframe)`, evaluating cached OHLCV bars via `evaluate_signals`/snapshot primitives. An LP position watch is a different shape along every axis that matters: it is keyed by `(wallet, chain, pool_address, nft_token_id)`, not a symbol/timeframe; it evaluates **on-chain RPC state** (an `enrich_lp_positions` call), not cached bars; and its meaningful trigger is not an instantaneous crossing but a **sustained** condition — a position that dips out of range for one block and back is noise, whereas one that has been out for hours is forgone yield. So the decision has two parts: **where these watch/alert entities live relative to the shipped ADR-0055 subsystem**, and **what edge semantics the trigger uses**.

The shipped ADR-0055 path is load-bearing — it delivers the user's market alerts today. Its `Watch`/`Alert` models require `symbol`/`timeframe`, its `alert.triggered v1` payload forbids extra fields, and its scheduler's evaluator is bars-driven. Generalizing all of that to also host RPC-driven, wallet-keyed, dwell-qualified DeFi watches means migrating live tables, making `symbol`/`timeframe` optional, and branching the one scheduler into a bars-vs-RPC evaluation polymorphism — destabilizing a working path to save duplication.

## Decision

We will run LP position monitoring as a **dedicated in-sidecar scheduler in the `defi/` domain** — a fourth lifespan asyncio task (`defi/position_monitor.py`), sibling to the existing `defi/scan_job.py` / `defi/pnl_job.py` jobs and to ADR-0055's `WatchScheduler`, started/stopped with the app and exposing a heartbeat on `/healthz`. It owns its own persistence (`DefiPositionWatch` / `DefiPositionAlert`, their repositories, and one additive migration) and emits a new **`defi.position_alert v1`** event registered in `TYPE_REGISTRY`. The shipped ADR-0055 `WatchScheduler` / `Watch` / `Alert` / `alert.triggered` path is **not touched**.

The trigger is a **dwell-qualified edge**: on each tick the monitor re-reads a watched position's live `in_range`, and fires exactly once when the position has been *continuously out of range for at least the watch's `dwell` threshold* (default measured in hours, per-watch configurable), recording the first-out timestamp so the dwell is measured across ticks and survives a sidecar restart. Re-entry into range resets the dwell state and re-arms the watch. Watches come from two sources unified behind one repository: a **config-pinned** wallet set (a declared wallet whose CL LPs are all monitored) and **agent-created** per-position watches via MCP tools (`create_position_watch` / `list_position_watches` / `delete_position_watch` / `list_position_alerts`), mirroring the ADR-0055 tool surface. Evaluation calls the same best-effort `enrich_lp_positions` path the on-demand `scan_wallet` tool uses; a failed RPC read leaves the prior dwell state untouched rather than resetting it. **Alert payloads state conditions only** — pool, tick bounds, current tick, dwell hours, forgone-fee context — never a directive; the advisory rebalance layer (ADR-0029) is a separate downstream consumer, not part of the alert.

## Consequences

### Positive
- The shipped market-alert path stays exactly as-is — no live-table migration, no evaluator polymorphism, no risk to a working feature. The two schedulers are independently testable and independently fail.
- Reuses every proven ADR-0055 pattern (lifespan asyncio loop + `/healthz` heartbeat, edge-triggering, condition-only payloads, agent-pollable pending-events) and the existing read-only `enrich_lp_positions` detection primitive — no new detection code, no new network posture.
- Dwell-qualification makes the alert economically meaningful: it fires on *sustained* zero-fee state (the thing worth acting on), not on price brushing the boundary.
- Persisted dwell state gives "it's been out of range since 03:00" for free and lets the agent poll "what fired while I was away" on session start.

### Negative
- **The sidecar grows a second duty cycle**, and this one hits an external RPC. Rate-limit and cost exposure scale with watched-position count × check frequency. Mitigations: a conservative default interval (LP ranges move on the timescale of hours, not seconds — a 15-minute default is ample), best-effort enrichment that already degrades gracefully, and a scheduler heartbeat so a wedged monitor is visible.
- A modest amount of CRUD/scheduler skeleton is duplicated from ADR-0055 rather than shared. Accepted as the price of not destabilizing the shipped path.
- Dwell timing rides the wall clock and is therefore non-deterministic; per the ADR-0055 rule it stays **outside** any financially-meaningful path (there is none here — this is a notification, not a backtest input). The evaluation itself (an enriched position in → `in_range` out) is the pure, tested part.
- Alert latency is bounded by the check interval plus the dwell threshold — this is deliberately a slow alerter, not a tick-level one.

### Neutral
- Proximity-to-edge pre-warnings ("price within X% of the range boundary", which needs tick→price conversion not yet implemented) are deliberately deferred. The watch vocabulary is extensible; that qualifier can be added later without a schema change.

## Alternatives considered

### Alternative A — Generalize the ADR-0055 `WatchScheduler` to host DeFi watches
Widen `WatchKind` with an `lp_out_of_range` kind, make `symbol`/`timeframe` optional on the live `Watch`/`Alert` tables, branch the scheduler into an RPC evaluator, and reuse `alert.triggered` + the Alerts view + existing tools. Rejected: it forces a migration on live tables and bars-vs-RPC evaluation polymorphism into the one scheduler that already delivers the user's market alerts — destabilizing a shipped, load-bearing path to avoid duplicating a small CRUD skeleton. The domains are cleanly separable (`defi/` already owns its own jobs); keep them separate.

### Alternative B — Agent-side `/loop` polling the `scan_wallet` tool
Zero sidecar change: a Claude Code loop calls `scan_wallet` on an interval and notices `in_range=false`. Rejected as the *primary* mechanism for the same reason ADR-0055 rejected it — it only runs while an agent session is alive and paying tokens per poll, and it has no cross-tick state for dwell (it would re-derive "how long out of range" from disk anyway). An always-on watch belongs in the always-on process. (An ad-hoc agent loop remains fine on top.)

### Alternative C — Instantaneous edge trigger (fire the moment `in_range` flips false)
Simplest reducer, matches ADR-0055's semantics exactly. Rejected: CL price sitting right on a range boundary crosses in and out repeatedly, so an instantaneous trigger chatters. The user's concern — and the positions-file's own "rebalance when out-of-range > 7 days" intent — is *sustained* out-of-range. The dwell qualifier is the whole point of the alert.
