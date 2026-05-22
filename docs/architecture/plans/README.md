# Plans

Implementation plans for `market-analyser`. Each plan is one file (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Completed plans live in [`done/`](done) — the architect moves a plan there as part of the close ceremony, never the implementer.

## Active roster

| #    | File                                                          | Status         | Summary |
|------|---------------------------------------------------------------|----------------|---------|
| 0008 | [0008-backtest-engine-v1](0008-backtest-engine-v1.md)         | approved       | Backtest engine v1: pure `run(strategy, bars, params, **costs) -> BacktestResult` + four metric helpers (`_apply_costs`, `_build_equity_curve`, `_calc_metrics`, `_buy_and_hold_return`) + thin `persist()` (disk + SQLite-indexed `backtest_runs` table) + `run_backtest` MCP tool emitting `run.completed v1` + Electron `BacktestView` (equity curve + metrics + trade log) + `RecentBacktestsView`. Closes Plan 0002's deferred engine half AND gives [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md)'s `run.completed v1` envelope its first producer. Five phases: `backtester` × 2 → `dev` × 2 → `ui-builder`. Paired with [ADR-0018](../adrs/0018-backtest-result-schema.md) (`BacktestResult` schema). Long-only, fixed-fraction sizing, flat-bps costs; sweeps and walk-forward deferred to follow-ups. |
| 0009 | [0009-resilience-and-tradingview-screener](0009-resilience-and-tradingview-screener.md) | approved | Tier 2 anchor. Lands the shared resilience module (`data/_http.py` — TTL cache + retry + backoff + concurrency cap + proxy-from-env) paired with [ADR-0019](../adrs/0019-external-http-adapter-resilience.md), the TradingView screener adapter (via `tradingview-screener` + `tradingview-ta` libs) implementing the stubbed `MarketDataProvider.get_screener`, a `screener_query` MCP tool, and a retrofit of `YahooAdapter` onto the shared client so we exit with zero adapter-level resilience drift. Four phases, all `dev`. |
| 0010 | [0010-news-and-vader-sentiment](0010-news-and-vader-sentiment.md) | approved | RSS news adapter (CoinDesk / CoinTelegraph / Yahoo Finance / MarketWatch / CNBC via `feedparser`) implementing the stubbed `get_news` + per-headline VADER scoring (via `vaderSentiment`) implementing the stubbed `get_sentiment`. Two MCP tools: `news_for(symbol, window, with_sentiment)` and `sentiment_for_news(symbol, window)`. Three phases, all `dev`. Inherits resilience module from Plan 0009 phase 1. |
| 0011 | [0011-fear-and-greed-indices](0011-fear-and-greed-indices.md) | approved | Smallest Tier 2 plan: Alternative.me crypto Fear & Greed adapter (one HTTP call, four fields) + `crypto_fear_greed` MCP tool + a new `MarketSentimentSample` Protocol method (`get_market_sentiment(market="crypto")`) distinct from per-symbol `get_sentiment`. One phase, `dev`. Inherits resilience module from Plan 0009. CNN equity F&G deferred (scraping required). |
| 0012 | [0012-stocktwits-sentiment](0012-stocktwits-sentiment.md) | approved | Second per-symbol sentiment source: StockTwits' free API exposes explicit user-applied `Bullish`/`Bearish` labels — counted directly, no NLP model. Adds a `source: Literal["rss-vader", "stocktwits"]` parameter to `get_sentiment` (additive, default preserves Plan 0010 callers). New `stocktwits_sentiment` MCP tool. Three phases, all `dev`. Inherits resilience module from Plan 0009. |

## Recently closed

| #    | File                                                                            | Closed     | Summary |
|------|---------------------------------------------------------------------------------|------------|---------|
| 0001 | [0001-bootstrap](done/0001-bootstrap.md)                                        | 2026-05-18 | Walking-skeleton Electron + Python-sidecar bootstrap with OHLCV chart for one symbol. Phases 1–5 + 4.1 shipped; closed after Plan 0004 landed. |
| 0003 | [0003-excise-vendored-upstream](done/0003-excise-vendored-upstream.md)          | 2026-05-19 | Rewrote the Yahoo OHLCV fetch in-house (`data/adapters/_yahoo_fetch.py`), deleted `data/vendored/` and `vendored.lock`, scrubbed `tradingview-mcp` mentions across `docs/`, `CLAUDE.md`, and the (gitignored) skills tree. Implementation shipped in commits `2337ee6`, `1df1be0`, `ae099e4`, `def5e08`; closed cleanly with one minor finding (done-when grep allow-list narrower than the substantive ADR append-only policy — body retentions in ADR-0004 and ADR-0007 are intentional). |
| 0004 | [0004-bootstrap-review-followups](done/0004-bootstrap-review-followups.md)      | 2026-05-18 | Cleared the architect-review deltas from Plan 0001 — silent cache truncation, post-restart 401, supervisor-spec stub, missing CSP-block test, secret-out-of-argv (now [ADR-0011](../adrs/0011-bearer-secret-transport.md)), renderer DX cluster, OhlcvView empty-state affordance. |
| 0005 | [0005-dependency-cooldown](done/0005-dependency-cooldown.md)                    | 2026-05-19 | Landed the dependency-discipline pair: `[tool.uv] exclude-newer = "2026-05-05"` + `minimumReleaseAge: 20160` in `pnpm-workspace.yaml` (cooldown; ADR-0012), and every direct dep in `pyproject.toml` + `desktop/package.json` rewritten to exact `==X.Y.Z` / `X.Y.Z` pins (ADR-0013). User-authorized single-commit landing; phase-1 corrected ADR-0012's mechanism (kebab-case in `.npmrc` → camelCase in `pnpm-workspace.yaml`) and bumped CI pnpm 9 → 11.1.2. Followups captured in the plan body. |
| 0006 | [0006-annotations-via-mcp](done/0006-annotations-via-mcp.md)                    | 2026-05-20 | Mounted MCP server (Streamable HTTP, rev 2025-03-26) on the sidecar at `/mcp` with its own long-lived `mcp-secret.json`. Three MCP tools (`get_ohlcv`, `write_annotation`, `list_annotations`) + `annotations` SQLite table + Settings page (reveal/copy/rotate) + 1 Hz chart-marker polling. Six phases, mixed `dev` + `ui-builder`. Two prior-review followups (CI guard + `.gitignore` for `mcp-secret*.json`) shipped before close; two new followups carried in the plan body (`get_ohlcv` timeframe validation; stale bootstrap-component-map schema). See [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md). |
| 0002 | [0002-strategy-interface](done/0002-strategy-interface.md)                      | 2026-05-20 | Strategy contract module (`Signal`, `BaseParams`, `META`, `StrategyProtocol`) + `discover()` + RSI reference + `signals_to_trades` adapter + `Trade` type + five ported reference strategies (`bollinger`, `macd`, `ema_cross`, `supertrend`, `donchian`) + `market-analyser strategies list [--json]` CLI. Five phases across three skills (`dev` → `dev` → `backtester` → `strategy-author` → `dev`); 56/56 specs pass; CLI prints six rows byte-identical across runs. Phase 5 added a `--json` flag at user authorization (not in plan). One open follow-up tracked below (CLI smoke test). |
| 0007 | [0007-live-agent-driven-viewer](done/0007-live-agent-driven-viewer.md) | 2026-05-22 | Standalone sidecar (lockfile + idempotent attach) + SSE `/events` stream + three new MCP `show_*` tools (`show_chart`, `update_chart`, `highlight_pattern`) + Electron SSE subscriber + Claude Code config. Phases 1–4 shipped 2026-05-20; phase 5 smoke (2026-05-21) surfaced four defects with one root cause (Python/Electron data-dir divergence in dev). Architect amended in-place 2026-05-22 with five new phases 4.1–4.5: shared data-dir contract per [ADR-0020](../adrs/0020-shared-data-dir-contract.md) (now `accepted`), `/healthz` identity check on attach (closes plan line 247), supervisor refresh API, renderer port+secret refresh + EventSource re-open, overlays prop wired + non-tautological live-chart spec. Phase 5 smoke re-fired 2026-05-22 and passed end-to-end. Owners ended: `dev` × 6 → `ui-builder` × 3 → `human`. Close-review findings + smoke bugs rolled into the plan's Followups section: 5 minor/nit cleanups (owners `ui-builder`/`dev`), 2 architectural items split out as **Plan 0013** (auto-backfill on cache miss) and **Plan 0014** (interactive chart + agent-mode toggle with bidirectional protocol). |

## Open follow-ups (no plan needed)

Small items carried over from closed plans — too small for their own plan, too small to gate a close. Pick up opportunistically; remove the row when the work lands. If an entry grows past ~half a day or starts coupling other changes, promote it to a real plan.

| From plan | Item | Owner | Note |
|-----------|------|-------|------|
| 0002 | `tests/test_cli.py` smoke test for `strategies list` (row count, sorted ids, run-twice byte equality) | `dev` | Phase 5's done-when ("six rows, identical across runs") was verified manually at close (2026-05-20). A ~15-line `capsys`-based spec would lock it in and catch a renderer regression or a stray seventh strategy. Dev correctly scoped this out of phase 5 per the no-scope-creep rule; recorded here so it doesn't fall on the floor. |
| (meta — skill ecosystem) | Auto-handoff for `dev` ↔ `ui-builder` only | `skill-creator` | Each implementer skill gets a final SKILL.md step: "if the plan's next phase has a different owner, invoke that skill via the Skill tool before returning" — scoped to `dev` ↔ `ui-builder` only. Architect↔implementer handoffs stay manual: the "go" approval and the fresh-session close review are gates that matter. User agreed to scope and trade-offs 2026-05-22; queued post-Plan-0007 close. |

## Recommended execution order

Plan 0006 closed on 2026-05-20, putting the MCP server, annotations table, Settings page, and chart-marker polling in place. On the same day the architect accepted [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) (Claude Code is now the primary control surface; Electron is the live viewer) plus the two mechanism ADRs it forces ([ADR-0016](../adrs/0016-standalone-sidecar-mode.md), [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md)). **Plan 0007 (live agent-driven viewer)** is the implementation of that role inversion — it closes the deferred items from ADR-0014 and Plan 0006, and without it the agent-primary workflow described in ADR-0015 has no mechanism.

Plan 0002 (strategy interface) **closed on 2026-05-20** with all five phases shipped — its Plan 0008-blocking pieces (contracts module, `signals_to_trades` adapter, `Trade` type) are in place. Plan 0007 remains in flight in parallel on 2026-05-20; it touches disjoint files (0007 is in `src/market_analyser/api/` + `desktop/`).

**Plan 0008 (backtest engine v1)** is the natural sequel. It is the engine half [ADR-0004](../adrs/0004-strategy-interface.md) named but Plan 0002 deliberately deferred — pure `run(strategy, bars, params, **costs) -> BacktestResult` + thin persistence + a `run_backtest` MCP tool. It also closes a load-bearing gap in Plan 0007: the `run.completed v1` SSE envelope was reserved with no producer, and Plan 0008 ships its first one. The plan is paired with [ADR-0018](../adrs/0018-backtest-result-schema.md) (`BacktestResult` schema). Plan 0008's Plan 0002 prerequisite (phases 1–3 = contracts module + `signals_to_trades` adapter + `Trade` type) **was satisfied 2026-05-20 at Plan 0002 close**; the remaining gate is Plan 0007 phases 1–4 (SSE bus + `useEventStream` hook), which Plan 0008's relevant phase 4 depends on.

**Tier 2 (data breadth) ships as a series** — Plans 0009–0012 — after the strategy/backtest threads close. The series is anchored by [ADR-0019](../adrs/0019-external-http-adapter-resilience.md) (shared resilience module for every external HTTP adapter); each subsequent adapter inherits the module for free, so they sequence cheaply behind 0009. Sequencing rationale:

- **Plan 0009** lands the resilience module + TradingView screener (highest-leverage adapter — unlocks "find me candidates" workflows the app has never been able to do) + retrofits `YahooAdapter` so we exit with zero drift.
- **Plan 0010** layers news (free RSS) + per-headline VADER sentiment on top of the resilience module. One fetch, two outputs.
- **Plan 0011** is the smallest possible Tier 2 plan (one HTTP call for crypto F&G) — exercises the resilience module against a different upstream shape than the screener.
- **Plan 0012** adds StockTwits sentiment as a second per-symbol source (explicit user labels — no NLP model needed) and extends `get_sentiment` with a `source` parameter.

Reddit-based sentiment is **deliberately deferred** — the `tradingview-mcp` upstream tried keyword-based scoring and it was flagged as fragile during architecture review. If after 0010 + 0012 land we find a coverage gap (small-caps or meme-driven names with no StockTwits / news coverage), Reddit returns with a real model in a future plan.

Plan 0002 (closed 2026-05-20) used three skill handoffs (`dev` → `backtester` → `strategy-author` → `dev`). At approval (2026-05-19) the architect considered collapsing to two — either by moving phase 5 (CLI) ahead of phase 4, or by making strategy-author phase 4 tests compare signal lists instead of trade lists. Both options were rejected: phase 5's done-when (six rows printed by `strategies list`) is the integration check that proves discovery + contract + CLI work together, and phase 4's done-when (trade list matches reference byte-for-byte after `signals_to_trades`) is the integration check that proves the contract round-trips through the adapter. The structure held — the close review found no blockers and one minor follow-up (CLI smoke test, tracked below).

Execution sequence (Plan 0007 closed 2026-05-22; 0008 next; Tier 2 series after 0008):

```
[Done — kept for historical sequencing]
A1. /dev          Plan 0007 phases 1–3  (dev block: lockfile + SSE + show_* tools)    [done 2026-05-20]
A2. /ui-builder   Plan 0007 phase 4     (Electron SSE subscriber + chart handlers)    [done 2026-05-20]
                  -- 2026-05-21: smoke surfaced four defects with one root cause     --
                  -- 2026-05-22: architect amended plan with hardening phases below  --
A3. /dev          Plan 0007 phases 4.1–4.3 (shared data-dir contract per ADR-0020;
                                         /healthz identity check on attach;
                                         supervisor refresh API)                      [done 2026-05-22]
A4. /ui-builder   Plan 0007 phases 4.4–4.5 (renderer port+secret refresh + EventSource
                                         re-open; overlays prop + non-tautological
                                         live-chart spec)                             [done 2026-05-22]
A5. /human        Plan 0007 phase 5     (Claude Code MCP + end-to-end smoke)         [done 2026-05-22]
A6. /architect    close Plan 0007       (fresh architect session)                    [done 2026-05-22]

B1. /dev          Plan 0002             (mixed-skill: dev → backtester → strategy-author →
                                         dev; hand off at each owner boundary)        [done 2026-05-20]
B2. /architect    close Plan 0002       (fresh architect session)                     [done 2026-05-20]

[Sequential — depends on A6 + B2, both satisfied]
C1. /backtester   Plan 0008 phases 1–2  (BacktestResult + four pure helpers + run() pure orchestrator;
                                         Plan 0002 prereq = phases 1–3 satisfied 2026-05-20)
C2. /dev          Plan 0008 phases 3–4  (persist + SQLite migration + GET routes + run_backtest MCP tool;
                                         cross-skill handoff from /backtester;
                                         phase 4 depends on Plan 0007 phases 1–4 = SSE bus, now satisfied)
C3. /ui-builder   Plan 0008 phase 5     (BacktestView + RecentBacktestsView;
                                         cross-skill handoff from /dev;
                                         depends on Plan 0007 phase 4 = useEventStream hook, now satisfied)
C4. /architect    close Plan 0008       (fresh architect session)

[Tier 2 series — sequential after C4; each inherits the resilience module from D1]
D1. /dev          Plan 0009             (resilience module + TradingView screener + Yahoo retrofit;
                                         4 phases all dev)
D2. /architect    close Plan 0009       (fresh architect session)
D3. /dev          Plan 0010             (RSS news + per-headline VADER sentiment;
                                         3 phases all dev; depends on Plan 0009 phase 1)
D4. /architect    close Plan 0010       (fresh architect session)
D5. /dev          Plan 0011             (crypto Fear & Greed;
                                         1 phase; depends on Plan 0009 phase 1)
D6. /architect    close Plan 0011       (fresh architect session)
D7. /dev          Plan 0012             (StockTwits sentiment;
                                         3 phases; depends on Plan 0009 phase 1 + Plan 0010 phase 2)
D8. /architect    close Plan 0012       (fresh architect session)
```

Plan 0008 keeps two skill handoffs (`backtester` → `dev` → `ui-builder`). The architect considered collapsing the `dev` block (phases 3 + 4) into the `backtester` block since they both touch `src/market_analyser/backtest/`, but rejected it: phase 3 introduces the SQLite migration + repository + HTTP routes (the persistence layer's center of gravity is `dev`, not the engine), and the cross-skill boundary at the `backtester` → `dev` handoff is the integration check that proves the pure engine is genuinely pure (the `dev` phases can build the I/O layer without changing `engine.py`).

Plans 0009–0012 are single-owner (`dev`) plans — no cross-skill handoffs inside them. They sequence strictly because each inherits the resilience module from Plan 0009 phase 1. Running them in parallel is not viable: they all touch `src/market_analyser/data/adapters/` and `src/market_analyser/api/mcp_tools/` concurrently, the merge conflict surface is high, and the architect-close-ceremony serialization rule applies anyway. The Plan 0009 close ceremony also gates whether the resilience module's shape needs adjustment before the next adapter sits on it — running 0010 against an unreviewed 0009 invites cascading rework.

## Status vocabulary

| Status                              | Meaning |
|-------------------------------------|---------|
| `draft`                             | Author wrote it; no user "go" yet. Implementers ignore. |
| `approved`                          | User signed off at the interview's end. Implementers may pick up. |
| `in-progress`                       | An implementing skill flipped it at Step 2 of its session. |
| `implementation complete — pending …` | All phases shipped; close ceremony blocked on a named followup plan or unresolved review delta. |
| `done`                              | Architect close ceremony fired; plan file lives in `done/`. |
| `abandoned`                         | User killed it before completion. Stays in this directory for the record. |
| `superseded by NNNN`                | A later plan replaced this one. (Rare — usually plans cleanly close.) |

Only `architect` and the implementing skill at Step 2 are allowed to mutate `Status:`. Implementers flip `draft → in-progress`; architect handles every other transition.

## Owner-skill vocabulary (per phase)

Each phase carries `**Owner skill:**` with exactly one value from the fixed set, backticked:

- `` `dev` `` — Python sidecar code, persistence, CI, tooling, Electron shell phases that aren't UI.
- `` `ui-builder` `` — anything under `desktop/`.
- `` `strategy-author` `` — strategies in `src/market_analyser/strategies/`.
- `` `backtester` `` — engine and run artifacts in `src/market_analyser/backtest/` and `runs/`.
- `` `human` `` — user-only task (rare; reserved for things Claude shouldn't touch).

Plans with mixed-owner phases hand off at every boundary per the [cross-skill handoff protocol](../../../.claude/skills/architect/references/templates/cross-skill-handoff.md). Missing or ambiguous tags fail Mode 4 review as blockers.

## Conventions

- **Numbering** is sequential and zero-padded to four digits. Next free number is **0013** (Plan 0007's close-ceremony Followups queued two architectural items as Plans 0013 — auto-backfill on cache miss — and 0014 — interactive chart + agent-mode toggle; neither is drafted yet). ADR numbers are an independent sequence (see [`../adrs/`](../adrs/)) — next free ADR is **0021** (last drafted: ADR-0020, **accepted** 2026-05-22 at Plan 0007 close after Phase 5 smoke confirmed the contract holds; previous proposed ADRs ADR-0019 and ADR-0018 were proposed 2026-05-20; previous accepted ADR was ADR-0017 on 2026-05-20). Architect runs `Glob docs/architecture/plans/*.md` and `Glob docs/architecture/adrs/*.md` before drafting to pick the next numbers, never trusting memory.
- **One plan per file.** No "Plan 0004a" / "Plan 0004b" splits — if the work grows, write a new numbered plan and reference the parent.
- **Plans aren't ADRs.** A plan says *what we're building this week and how*; an ADR says *why we chose this design over the alternatives*. Plans expire; ADRs don't. If a plan's decision warrants permanent capture, the architect also writes an ADR (Mode 2).
- **Plans don't move until the architect's close ceremony.** Implementers commit per phase but never `git mv` a plan to `done/`. The close ceremony reviews the whole plan in one pass, then flips status + moves the file in a single architect-authored commit.
- **In-progress plans are append-only on substance.** The only mid-flight edit is the `Status:` line and minor honesty fixes (e.g. correcting a stale owner tag). Structural amendments — adding phases, rewriting done-when — happen via a new followup plan, not in-place.
- **Cross-references stay link-shaped.** When one plan references another's phase, use a markdown link (`[Plan 0004 phase 7](0004-...md)`) so the cross-ref survives renumbering and the close-ceremony move to `done/`.

## When you don't know which plan to start

Don't guess. The execution sequence above is the source of truth as of 2026-05-22 (Plans 0001 + 0002 + 0003 + 0004 + 0005 + 0006 + 0007 closed; Plan 0008 next, both gates satisfied; Tier 2 series 0009 → 0010 → 0011 → 0012 sequenced after 0008; Plans 0013 and 0014 queued from Plan 0007's close-ceremony Followups but not yet drafted). If reality has drifted (the user names a plan not in that sequence, or a status disagrees with a recent commit), trust `git log` and the plan's own `Status:` line over this README — and surface the drift so the README gets refreshed.
