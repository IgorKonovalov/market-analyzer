# 0056 — Self-warming metric store: background accrual in the sidecar

> **Status:** proposed — accepts at [Plan 0061](../plans/0061-metric-store-self-warming.md) close
> **Created:** 2026-07-06
> **Related:** [ADR-0051](0051-historized-metric-series-contract.md) (the store + `as_of` contract this feeds), [ADR-0055](0055-in-sidecar-watch-scheduler.md) (the lifespan-loop precedent), [ADR-0054](0054-exogenous-forecast-features-multi-horizon.md) (the v2 feature set this exists to make evaluable), [ADR-0052](0052-binance-exchange-data-source.md)/[ADR-0053](0053-onchain-valuation-source.md) (the sources being driven), [ADR-0016](0016-standalone-sidecar-mode.md) (the always-on process this rides)

## Context

The ADR-0051 metric store historizes five external series the v2 forecast feature set joins against ([ADR-0054](0054-exogenous-forecast-features-multi-horizon.md)): Fear & Greed, BTC dominance, BTCUSDT funding rate, BTCUSDT open interest, and MVRV. Three of them (F&G, funding, MVRV) are fully backfillable from their upstreams at any time; two (dominance, open interest) have **no historical API** — a point not captured in its hour is lost forever, which is why ADR-0051 built them as accrue-forward hourly buckets with first-write-wins semantics.

Accrual today happens only as a **side effect of tool calls**: the F&G/CoinGecko adapters write through on macro fetches, and `derivatives_snapshot`/`btc_cycle_snapshot` fetch only on `refresh=true`. The 2026-07-06 production finding is the consequence: **all five series had zero points** — nobody had called the tools on any cadence, so a month after the accrual machinery landed there was no data, the v2 feature join dropped every row, and the `forecast` tool returned a vacuous no-edge (`n_scored=0`) rather than a market verdict. The forcing constraint: accrue-only series build history at exactly 1× real time, so every un-accrued week is a week the v2 feature set's evaluable history shrinks by, permanently. A mechanism that depends on someone remembering has empirically produced zero points.

The tension is posture: until now the sidecar touches the network only when an agent (or the viewer) asks, apart from the ADR-0055 watch scheduler — which fetches bars in the background, but only when the user has explicitly created watches. Unattended metric accrual is background network activity with **no user-created object requesting it**. That posture change is this decision.

## Decision

The sidecar warms and maintains the metric store itself. A metric-accrual job rides the application lifespan (the ADR-0055 pattern: started after persistence is up, cancelled on shutdown, absent when persistence is absent), ticking on a configurable interval (default hourly — the store's bucket size) and incrementally topping up every series the v2 feature set requires. On first tick against an empty backfillable series it pulls full history; against an empty accrue-only series it seeds what the upstream offers (OI: ~30 days) and accrues forward. The job is **on by default** with a `config.json` off-switch, reports per-series health on `/healthz` beside the watch-scheduler heartbeat, and contains per-series failures so one dead upstream never stops the others. Writes go through the existing ADR-0051 repository semantics (first-write-wins, upsert-once) — the job adds no new write paths, only a clock.

## Consequences

**Positive.** The accrue-only series build gap-free history from first boot with zero ceremony — the v2 feature set's evaluable window starts growing the day this lands, instead of the day someone remembers. Fresh deployments self-heal (the 2026-07-06 zero-points state cannot silently recur while the flag is on). Freshness becomes observable (`/healthz`) instead of discoverable-by-forensics. Idempotent hourly buckets make the cadence safe: a tick that races a tool-call write is a no-op.

**Negative — the price.** The sidecar now emits unattended network traffic by default; "the app only talks to the network when asked" is no longer true (the off-switch preserves it as an opt-out, not an invariant). Series completeness is now coupled to **sidecar uptime**: hours the process is down are permanent holes in dominance/OI (acceptable for a desktop app whose owner runs it while trading — the as-of join tolerates gaps — but a hole is a dropped v2 row forever, per ADR-0054's no-zero-fill rule). Upstream rate-limit exposure gains a steady-state floor (five paced calls/hour; the cold-start burst is one-time and paced per each adapter's documented contract). And a background writer makes "why did this file change" marginally less obvious in debugging — mitigated by the heartbeat and by logging each tick's per-series outcome.

## Alternatives considered

- **Status quo (agent-driven accrual only).** Rejected on evidence: it produced zero points in a month. A determinism-sensitive dataset cannot depend on conversational habit.
- **External scheduler (a Claude Code routine / OS cron calling `refresh=true` tools hourly).** Rejected: it moves the reliability burden to whichever machine/session happens to have the cron, adds an MCP-auth dependency to a data-integrity concern, and dies silently when the user reinstalls or the session model changes. The data's completeness should not depend on infrastructure outside the process that owns the data.
- **Fold accrual into the ADR-0055 watch scheduler.** Rejected: the watch scheduler's cadence is watch-driven (it can legitimately be idle or disabled with no watches), its heartbeat vocabulary is alerting-shaped, and coupling "user asked to be alerted" to "the store must stay warm" recreates the original bug — no watches, no data. Separate duty, separate clock, separate heartbeat; same lifespan pattern.
- **Opt-in flag (off by default).** Rejected: a fresh deployment silently accrues nothing again — the exact failure mode being fixed. The off-switch covers the offline/debug case without making cold-by-default the norm.
