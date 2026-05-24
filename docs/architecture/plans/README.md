# Plans

Implementation plans for `market-analyser`. Each plan is one file (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Completed plans live in [`done/`](done) — the architect moves a plan there as part of the close ceremony, never the implementer.

## Active roster

| #    | File                                                          | Status         | Summary |
|------|---------------------------------------------------------------|----------------|---------|
| 0010 | [0010-news-and-vader-sentiment](0010-news-and-vader-sentiment.md) | approved | RSS news adapter (CoinDesk / CoinTelegraph / Yahoo Finance / MarketWatch / CNBC via `feedparser`) implementing the stubbed `get_news` + per-headline VADER scoring (via `vaderSentiment`) implementing the stubbed `get_sentiment`. Two MCP tools: `news_for(symbol, window, with_sentiment)` and `sentiment_for_news(symbol, window)`. Three phases, all `dev`. Inherits resilience module from Plan 0009 phase 1. |
| 0011 | [0011-fear-and-greed-indices](0011-fear-and-greed-indices.md) | approved | Smallest Tier 2 plan: Alternative.me crypto Fear & Greed adapter (one HTTP call, four fields) + `crypto_fear_greed` MCP tool + a new `MarketSentimentSample` Protocol method (`get_market_sentiment(market="crypto")`) distinct from per-symbol `get_sentiment`. One phase, `dev`. Inherits resilience module from Plan 0009. CNN equity F&G deferred (scraping required). |
| 0012 | [0012-stocktwits-sentiment](0012-stocktwits-sentiment.md) | approved | Second per-symbol sentiment source: StockTwits' free API exposes explicit user-applied `Bullish`/`Bearish` labels — counted directly, no NLP model. Adds a `source: Literal["rss-vader", "stocktwits"]` parameter to `get_sentiment` (additive, default preserves Plan 0010 callers). New `stocktwits_sentiment` MCP tool. Three phases, all `dev`. Inherits resilience module from Plan 0009. |
| 0013 | [0013-auto-backfill-on-cache-miss](0013-auto-backfill-on-cache-miss.md) | approved | Closes the Plan 0007 followup symptom (agent thinks `get_ohlcv` is cache-only and asks the user to invoke `/dev`). Rewrites the tool's docstring to be honest, changes its response shape to `{bars, partial_reason, message}`, adds opt-in `backfill_async=true`, adds a dedicated verb-named `backfill_ohlcv` MCP tool, defines three SSE events (`ohlcv.backfill_started/backfilled/backfill_failed v1`), introduces typed adapter errors (`RateLimitedError`, `UpstreamUnavailableError`, `UnknownSymbolError`), adds a `BackfillCoordinator` with (symbol, timeframe) dedup, and wires the renderer to show an inline spinner + auto-refetch on completion + toast on failure. Four phases: `dev` × 3 → `ui-builder`. |
| 0016 | [0016-golden-path-smoke](0016-golden-path-smoke.md) | approved | Adds `pnpm smoke` — a runnable Python driver (`tests/smoke/golden_path.py`) that attaches to a running `pnpm dev:all` sidecar and drives one end-to-end golden path through every shipped layer against **live** upstreams (`/healthz` → `get_ohlcv` Yahoo → `show_chart` → `run_backtest` → `screener_query` TradingView → annotation/highlight → `/events` SSE liveness → `strategies list` CLI), asserting wire responses and exiting non-zero on integration failure while the SSE-publishing tools feed the live viewer for a human visual checklist. Hybrid (scripted + checklist), golden-path scope, local-only (never a CI gate). No paired ADR (dev tooling, like Plan 0015). Three phases: `dev` × 2 → `human`. Out-of-band — touches `tests/smoke/`, root `package.json`, and onboarding docs; disjoint from the Tier 2 series, so it can run anytime. |
| 0014 | [0014-interactive-chart-and-agent-mode](0014-interactive-chart-and-agent-mode.md) | approved | Closes the renderer→agent feedback loop ADR-0017 deliberately left open. Three new UI-event types (`ui.range_selected v1`, `ui.bar_clicked v1`, `ui.agent_mode_toggled v1`), a chart-header agent-mode toggle (default OFF, persisted to `agent_mode.json`), `POST /ui_events` (renderer-bearer-gated, 403 when mode is OFF), an MCP tool `get_pending_ui_events(since=None, drain=True)` + MCP resource `ui-events://recent` + `notifications/resources/updated` on every append (best-effort push), single-instance Electron enforcement (supersedes Plan 0007's "two viewers OK" allowance). Paired with [ADR-0021](../adrs/0021-renderer-to-agent-feedback.md) (proposed; moves to accepted at Plan 0014 close). Four phases: `dev` × 2 → `ui-builder` → `human`. |

## Recently closed

| #    | File                                                                            | Closed     | Summary |
|------|---------------------------------------------------------------------------------|------------|---------|
| 0009 | [0009-resilience-and-tradingview-screener](done/0009-resilience-and-tradingview-screener.md) | 2026-05-24 | Tier 2 anchor. Shipped the shared resilience module (`data/_http.py` — TTL cache + retry + backoff + concurrency cap + proxy-from-env) paired with [ADR-0019](../adrs/0019-external-http-adapter-resilience.md) (flipped `proposed → accepted` at close), the TradingView screener adapter (`tradingview-screener` used as a query/URL builder; the POST is issued through `ResilientHttpClient` per ADR-0019's single-HTTP-path invariant) implementing the stubbed `MarketDataProvider.get_screener`, a `screener_query` MCP tool, and the `YahooAdapter` retrofit onto the shared client (zero resilience drift). Four phases, all `dev`. Close-review: no blockers; 371 passed / 4 known Windows skips / 3 network-deselected, mypy `--strict` clean. Phase-1 tests landed *stronger* than the plan asked (concurrency test asserts max-in-flight == 2, not just timing; permanent-error test fails if `sleep` is called). Four followups in the plan body: unused `tradingview-ta` direct dep (`dev`, load-bearing — see open-followups below), `_http.py` 513 lines just over the ADR-0019 package-split trigger (logic still under budget — no action), undocumented dual `limit` cap (nit), screener lane for the system-map diagram (folds into the queued Plan 0008 diagram refresh). |
| 0001 | [0001-bootstrap](done/0001-bootstrap.md)                                        | 2026-05-18 | Walking-skeleton Electron + Python-sidecar bootstrap with OHLCV chart for one symbol. Phases 1–5 + 4.1 shipped; closed after Plan 0004 landed. |
| 0003 | [0003-excise-vendored-upstream](done/0003-excise-vendored-upstream.md)          | 2026-05-19 | Rewrote the Yahoo OHLCV fetch in-house (`data/adapters/_yahoo_fetch.py`), deleted `data/vendored/` and `vendored.lock`, scrubbed `tradingview-mcp` mentions across `docs/`, `CLAUDE.md`, and the (gitignored) skills tree. Implementation shipped in commits `2337ee6`, `1df1be0`, `ae099e4`, `def5e08`; closed cleanly with one minor finding (done-when grep allow-list narrower than the substantive ADR append-only policy — body retentions in ADR-0004 and ADR-0007 are intentional). |
| 0004 | [0004-bootstrap-review-followups](done/0004-bootstrap-review-followups.md)      | 2026-05-18 | Cleared the architect-review deltas from Plan 0001 — silent cache truncation, post-restart 401, supervisor-spec stub, missing CSP-block test, secret-out-of-argv (now [ADR-0011](../adrs/0011-bearer-secret-transport.md)), renderer DX cluster, OhlcvView empty-state affordance. |
| 0005 | [0005-dependency-cooldown](done/0005-dependency-cooldown.md)                    | 2026-05-19 | Landed the dependency-discipline pair: `[tool.uv] exclude-newer = "2026-05-05"` + `minimumReleaseAge: 20160` in `pnpm-workspace.yaml` (cooldown; ADR-0012), and every direct dep in `pyproject.toml` + `desktop/package.json` rewritten to exact `==X.Y.Z` / `X.Y.Z` pins (ADR-0013). User-authorized single-commit landing; phase-1 corrected ADR-0012's mechanism (kebab-case in `.npmrc` → camelCase in `pnpm-workspace.yaml`) and bumped CI pnpm 9 → 11.1.2. Followups captured in the plan body. |
| 0006 | [0006-annotations-via-mcp](done/0006-annotations-via-mcp.md)                    | 2026-05-20 | Mounted MCP server (Streamable HTTP, rev 2025-03-26) on the sidecar at `/mcp` with its own long-lived `mcp-secret.json`. Three MCP tools (`get_ohlcv`, `write_annotation`, `list_annotations`) + `annotations` SQLite table + Settings page (reveal/copy/rotate) + 1 Hz chart-marker polling. Six phases, mixed `dev` + `ui-builder`. Two prior-review followups (CI guard + `.gitignore` for `mcp-secret*.json`) shipped before close; two new followups carried in the plan body (`get_ohlcv` timeframe validation; stale bootstrap-component-map schema). See [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md). |
| 0002 | [0002-strategy-interface](done/0002-strategy-interface.md)                      | 2026-05-20 | Strategy contract module (`Signal`, `BaseParams`, `META`, `StrategyProtocol`) + `discover()` + RSI reference + `signals_to_trades` adapter + `Trade` type + five ported reference strategies (`bollinger`, `macd`, `ema_cross`, `supertrend`, `donchian`) + `market-analyser strategies list [--json]` CLI. Five phases across three skills (`dev` → `dev` → `backtester` → `strategy-author` → `dev`); 56/56 specs pass; CLI prints six rows byte-identical across runs. Phase 5 added a `--json` flag at user authorization (not in plan). One open follow-up tracked below (CLI smoke test). |
| 0007 | [0007-live-agent-driven-viewer](done/0007-live-agent-driven-viewer.md) | 2026-05-22 | Standalone sidecar (lockfile + idempotent attach) + SSE `/events` stream + three new MCP `show_*` tools (`show_chart`, `update_chart`, `highlight_pattern`) + Electron SSE subscriber + Claude Code config. Phases 1–4 shipped 2026-05-20; phase 5 smoke (2026-05-21) surfaced four defects with one root cause (Python/Electron data-dir divergence in dev). Architect amended in-place 2026-05-22 with five new phases 4.1–4.5: shared data-dir contract per [ADR-0020](../adrs/0020-shared-data-dir-contract.md) (now `accepted`), `/healthz` identity check on attach (closes plan line 247), supervisor refresh API, renderer port+secret refresh + EventSource re-open, overlays prop wired + non-tautological live-chart spec. Phase 5 smoke re-fired 2026-05-22 and passed end-to-end. Owners ended: `dev` × 6 → `ui-builder` × 3 → `human`. Close-review findings + smoke bugs rolled into the plan's Followups section: 5 minor/nit cleanups (owners `ui-builder`/`dev`), 2 architectural items split out as **Plan 0013** (auto-backfill on cache miss) and **Plan 0014** (interactive chart + agent-mode toggle with bidirectional protocol). |
| 0008 | [0008-backtest-engine-v1](done/0008-backtest-engine-v1.md)                      | 2026-05-23 | Backtest engine v1 + paired [ADR-0018](../adrs/0018-backtest-result-schema.md) (flipped `proposed → accepted` at close). Pure `run(strategy, bars, params, **costs) -> BacktestResult` + four metric helpers + thin `persist()` (disk + SQLite-indexed `backtest_runs` table) + `run_backtest` MCP tool emitting `run.completed v1` + Electron `BacktestView` (equity curve + metrics + trade log) + `RecentBacktestsView`. Closes Plan 0002's deferred engine half AND gives ADR-0017's `run.completed v1` envelope its first producer. Five phases: `backtester` × 2 → `dev` × 2 → `ui-builder`. Tests landed comprehensive: golden in-process + cross-process determinism, atomic persist (duplicate-id cleanup), migration reversibility, cross-tenant 401 (MCP bearer rejected on renderer routes), no-bearer-leak in logs, 117 renderer Jest + 48 main-process Jest + 15 Playwright e2e all green no skips/xfails. Close-review: no blockers; 1 minor structural bug carried as a followup (trade-log P&L $ formula `(exit-entry) * (initial/entry)` is structurally inconsistent with the engine's compounding equity-curve math for multi-trade runs — fix path in plan Followups), 1 ADR flip done at close, 3 nits (Literal typegen narrowing, last-write-wins fetch ordering, useEffect dep array on a test-only seam). One diagram refresh queued (claude-cli-driven-architecture.md needs a backtest-results lane). |
| 0015 | [0015-pnpm-dev-all](done/0015-pnpm-dev-all.md)                                  | 2026-05-22 | One-command dev loop: new root `package.json` with `pnpm dev:all` orchestrating three `concurrently` children — `spawn-sidecar.mjs` (Python sidecar wrapper with default Ctrl+C teardown, `--keep-sidecar` opt-out, and reuse-an-already-running-sidecar path), `write-mcp-config.mjs --watch` (atomic `.mcp.json` writer reading `sidecar.lock` for port + `mcp-secret.json` for bearer, 0600 on POSIX), and the gated `pnpm --filter desktop dev`. Pure dev tooling — `git diff src/` empty across all three commits (`ec1032e`, `be9f94b`, `79794bc`). Three phases all `dev`. Close-review: no blockers, four nits / minor (cooldown-bypass mechanism, Windows-`unref` doc wording, `--lockfile=` flag namespace, phase-1-done-when superseded by phase-3 reuse path) — all parked in the plan's Followups. Tests: 23 pass / 2 platform-skip on Node side, 1 pass on the Python data-dir helper. CI gained `pnpm test:dev-scripts` in the `desktop-types` job. |

## Open follow-ups (no plan needed)

Small items carried over from closed plans — too small for their own plan, too small to gate a close. Pick up opportunistically; remove the row when the work lands. If an entry grows past ~half a day or starts coupling other changes, promote it to a real plan.

| From plan | Item | Owner | Note |
|-----------|------|-------|------|
| 0002 | `tests/test_cli.py` smoke test for `strategies list` (row count, sorted ids, run-twice byte equality) | `dev` | Phase 5's done-when ("six rows, identical across runs") was verified manually at close (2026-05-20). A ~15-line `capsys`-based spec would lock it in and catch a renderer regression or a stray seventh strategy. Dev correctly scoped this out of phase 5 per the no-scope-creep rule; recorded here so it doesn't fall on the floor. |
| 0008 | Trade-log P&L $ formula compounds inconsistently with the engine for multi-trade runs | `architect` → `ui-builder` (or `backtester` if schema change) | Plan 0008 §221 pinned UI P&L $ as `(exit - entry) * (initial_capital / entry)`, but the engine's `_build_equity_curve` compounds (`units = cash / entry_price` where `cash` is the running equity, not `initial_capital`). For trade N>1 the row disagrees with the equity-curve delta (~10% drift on the 2-trade worked example in the plan's Followups). Two paths: (a) `Trade.pnl_usd` field — ADR-0018 amendment + engine writer + UI reader; (b) UI derives from `equity_curve[exit_bar] - equity_curve[entry_bar - 1]` — renderer-only. Pick at next architect touch; if option (a) wins it grows past half a day and becomes its own plan. |
| 0008 | `gen-types.mjs::mapType` doesn't narrow Pydantic single-value `Literal` types | `ui-builder` or `dev` | `Trade.kind` and `BacktestResult.sizing` emit as plain `string` in `desktop/renderer/types/sidecar/...` instead of `'long'` / `'fixed_fraction'`. Cosmetic; no phase-5 code branches on these. One-line `mapType` change. |
| 0008 / 0009 | Refresh `docs/architecture/diagrams/claude-cli-driven-architecture.md` with a backtest-results lane **and** a screener lane | `architect` | The current map predates the `run_backtest` MCP tool + `BacktestView` + `RecentBacktestsView` (0008) and the `screener_query` tool + `TradingViewScreenerAdapter` + TradingView upstream (0009). One pass adds both lanes so the system map reflects post-0009 reality. |
| 0009 | `tradingview-ta==3.3.0` is an unused direct dependency | `dev` | The screener adapter uses `tradingview-screener` purely as a query/URL builder and issues the POST through `ResilientHttpClient`; `tradingview-ta` is imported nowhere in `src/`. Against ADR-0013 parsimony. Drop from `pyproject.toml` + `uv lock` in one commit, unless a near-term Tier 2 plan (0010–0012) is expected to consume it — in which case record the intent in a one-line comment. Implementer flagged it in commit `6aaa638`. |
| 0006 / 0007 | Windows companion tests for four POSIX-only skipped tests | `dev` | Four legitimate platform skips today: `test_mcp_secret_file_mode_is_0600` (test_mcp_walking_skeleton.py:137), `test_post_rotate_preserves_0600_mode` (test_settings_route.py:179), `test_lockfile_mode_is_0600_on_posix` (test_sidecar_lockfile.py:187), `test_sigterm_removes_lockfile_before_exit` (test_sidecar_lockfile.py:244). The first three assert `stat.S_IMODE == 0o600` — meaningless on Windows where protection is ACL-based; add `icacls`-shelling or `pywin32` `GetFileSecurity` companions asserting the file is restricted to the current user with inheritance disabled. The fourth uses SIGTERM (which Python maps to `TerminateProcess` on Windows, skipping `finally`); add a Windows companion using `CREATE_NEW_PROCESS_GROUP` + `signal.CTRL_BREAK_EVENT`, which Python *does* deliver as a soft signal that runs the cleanup `finally` block. Pure coverage hygiene — not a correctness issue (ADR-0006/0011/0016 user-data-dir ACL inheritance + PID-liveness recovery already defend the property on Windows). Require CI to run the POSIX assertions on at least one Linux/macOS leg of the matrix so the existing skips aren't permanently dark. |

## Recommended execution order

Plan 0006 closed on 2026-05-20, putting the MCP server, annotations table, Settings page, and chart-marker polling in place. On the same day the architect accepted [ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) (Claude Code is now the primary control surface; Electron is the live viewer) plus the two mechanism ADRs it forces ([ADR-0016](../adrs/0016-standalone-sidecar-mode.md), [ADR-0017](../adrs/0017-live-ui-updates-via-sse.md)). **Plan 0007 (live agent-driven viewer)** is the implementation of that role inversion — it closes the deferred items from ADR-0014 and Plan 0006, and without it the agent-primary workflow described in ADR-0015 has no mechanism.

Plan 0002 (strategy interface) **closed on 2026-05-20** with all five phases shipped — its Plan 0008-blocking pieces (contracts module, `signals_to_trades` adapter, `Trade` type) are in place. Plan 0007 **closed on 2026-05-22** after five hardening sub-phases 4.1–4.5; the SSE bus + `useEventStream` hook + standalone-sidecar attach are all live, which means every substantive plan downstream has its gates satisfied.

Plan 0015 (`pnpm dev:all`) **closed on 2026-05-22** — three phases all `dev`, no production code touched, every subsequent plan's smoke loop now runs through the one-command flow. The four close-ceremony findings are nits / minor and live in the plan's Followups section.

Plan 0008 (backtest engine v1) **closed on 2026-05-23** — five phases shipped across three skills (`backtester × 2` → `dev × 2` → `ui-builder`); paired ADR-0018 flipped `proposed → accepted` at close. The `run.completed v1` envelope reserved by ADR-0017 / Plan 0007 now has its first producer; `BacktestResult` is in production use across the engine, the persistence layer, the MCP tool, and the renderer. Close-review: no blockers; the load-bearing finding is a structural P&L $ formula drift in the trade log (carried in plan Followups + the open-followup table; not a blocker because it doesn't affect the equity curve, only the trade-log column).

**Tier 2 (data breadth) ships as a series** — Plans 0009–0012. The series is anchored by [ADR-0019](../adrs/0019-external-http-adapter-resilience.md) (shared resilience module for every external HTTP adapter, `accepted` at the Plan 0009 close on 2026-05-24); each subsequent adapter inherits the module for free, so they sequence cheaply behind 0009. **Plan 0009 closed 2026-05-24** — the resilience module, TradingView screener, `screener_query` MCP tool, and `YahooAdapter` retrofit are all live, so **Plan 0010 is recommended next**. Sequencing rationale:

- **Plan 0009** (closed) landed the resilience module + TradingView screener (highest-leverage adapter — unlocks "find me candidates" workflows the app has never been able to do) + retrofitted `YahooAdapter` so we exit with zero drift.
- **Plan 0010** layers news (free RSS) + per-headline VADER sentiment on top of the resilience module. One fetch, two outputs. Now the live front of the series.
- **Plan 0011** is the smallest possible Tier 2 plan (one HTTP call for crypto F&G) — exercises the resilience module against a different upstream shape than the screener.
- **Plan 0012** adds StockTwits sentiment as a second per-symbol source (explicit user labels — no NLP model needed) and extends `get_sentiment` with a `source` parameter.

Reddit-based sentiment is **deliberately deferred** — the `tradingview-mcp` upstream tried keyword-based scoring and it was flagged as fragile during architecture review. If after 0010 + 0012 land we find a coverage gap (small-caps or meme-driven names with no StockTwits / news coverage), Reddit returns with a real model in a future plan.

**Plans 0013 (auto-backfill on cache miss) and 0014 (interactive chart + agent-mode toggle)** were drafted 2026-05-22 from Plan 0007's close-ceremony Followups. They are sequenced **after** the Tier 2 data series for two reasons: (1) Plan 0007's followups originally framed them as "design after current work lands"; (2) they touch the MCP-tool surface and renderer (overlapping with Plan 0008's `run_backtest` tool + BacktestView), so running them after Plan 0008 closes minimises merge surface. The two are sequenced 0013 → 0014: Plan 0013 establishes the typed-error pattern and the renderer-side event-handler plumbing pattern that Plan 0014 mirrors for UI-event types, and the original Plan 0007 followup explicitly said "architect to design Plan 0014 after Plan 0013 lands". Plan 0014 is paired with [ADR-0021](../adrs/0021-renderer-to-agent-feedback.md), which proposes the resource+notification mechanism for renderer→agent feedback and is itself the gating decision for the plan; ADR-0021 moves to `accepted` at Plan 0014 close. The user may re-prioritise 0013 / 0014 ahead of the Tier 2 series at any time — both touch disjoint files from 0009–0012, so the sequencing is preference, not technical constraint.

Plan 0002 (closed 2026-05-20) used three skill handoffs (`dev` → `backtester` → `strategy-author` → `dev`). At approval (2026-05-19) the architect considered collapsing to two — either by moving phase 5 (CLI) ahead of phase 4, or by making strategy-author phase 4 tests compare signal lists instead of trade lists. Both options were rejected: phase 5's done-when (six rows printed by `strategies list`) is the integration check that proves discovery + contract + CLI work together, and phase 4's done-when (trade list matches reference byte-for-byte after `signals_to_trades`) is the integration check that proves the contract round-trips through the adapter. The structure held — the close review found no blockers and one minor follow-up (CLI smoke test, tracked below).

Execution sequence (Plans 0007 + 0015 closed 2026-05-22; recommended next: Plan 0008 then Tier 2 series 0009–0012 then Plans 0013 + 0014):

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

C1. /dev          Plan 0015 phases 1–3  (root package.json + dev:all + sidecar wrapper;
                                         .mcp.json writer + onboarding doc;
                                         Ctrl+C teardown + --keep-sidecar + reuse-existing)  [done 2026-05-22]
C2. /architect    close Plan 0015       (fresh architect session)                            [done 2026-05-22]

D1. /backtester   Plan 0008 phases 1–2  (BacktestResult + four pure helpers + run() pure orchestrator;
                                         Plan 0002 prereq = phases 1–3 satisfied 2026-05-20)  [done 2026-05-23]
D2. /dev          Plan 0008 phases 3–4  (persist + SQLite migration + GET routes + run_backtest MCP tool;
                                         cross-skill handoff from /backtester;
                                         phase 4 depends on Plan 0007 phases 1–4 = SSE bus)    [done 2026-05-23]
D3. /ui-builder   Plan 0008 phase 5     (BacktestView + RecentBacktestsView;
                                         cross-skill handoff from /dev;
                                         depends on Plan 0007 phase 4 = useEventStream hook)   [done 2026-05-23]
D4. /architect    close Plan 0008       (fresh architect session; flips ADR-0018 to accepted)  [done 2026-05-23]

[Tier 2 data series; sequential, each inherits the resilience module from E1]
E1. /dev          Plan 0009             (resilience module + TradingView screener + Yahoo retrofit;
                                         4 phases all dev)                                       [done 2026-05-24]
E2. /architect    close Plan 0009       (fresh architect session; flips ADR-0019 to accepted)   [done 2026-05-24]

[Recommended next]
E3. /dev          Plan 0010             (RSS news + per-headline VADER sentiment;
                                         3 phases all dev; depends on Plan 0009 phase 1 = resilience module)
E4. /architect    close Plan 0010       (fresh architect session)
E5. /dev          Plan 0011             (crypto Fear & Greed;
                                         1 phase; depends on Plan 0009 phase 1)
E6. /architect    close Plan 0011       (fresh architect session)
E7. /dev          Plan 0012             (StockTwits sentiment;
                                         3 phases; depends on Plan 0009 phase 1 + Plan 0010 phase 2)
E8. /architect    close Plan 0012       (fresh architect session)

[Plan 0007 close-ceremony followups — last; user may re-prioritise ahead of Tier 2]
F1. /dev          Plan 0013 phases 1–3  (auto-backfill: typed events + typed errors;
                                         get_ohlcv contract honesty + backfill_ohlcv tool;
                                         BackfillCoordinator + dedup + partial-failure surfacing)
F2. /ui-builder   Plan 0013 phase 4     (renderer: backfill spinner + auto-refetch +
                                         failure toast; cross-skill handoff from /dev)
F3. /architect    close Plan 0013       (fresh architect session)

F4. /dev          Plan 0014 phases 1–2  (UI-event vocabulary + POST /ui_events +
                                         agent-mode state; MCP tool + resource +
                                         resource-update notification)
F5. /ui-builder   Plan 0014 phase 3     (renderer: agent-mode toggle + range-select +
                                         bar-click + single-instance Electron;
                                         cross-skill handoff from /dev)
F6. /human        Plan 0014 phase 4     (end-to-end smoke with Claude Code)
F7. /architect    close Plan 0014       (fresh architect session; flips ADR-0021 to accepted)
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

- **Numbering** is sequential and zero-padded to four digits. Next free number is **0017** (Plan 0016 approved 2026-05-24 — golden-path smoke; Plan 0009 closed 2026-05-24; the active roster now runs 0010 → 0011 → 0012 → 0013 → 0014, with 0016 out-of-band tooling). ADR numbers are an independent sequence (see [`../adrs/`](../adrs/)) — next free ADR is **0022** (ADR-0021 proposed 2026-05-22 alongside Plan 0014; moves to accepted at Plan 0014 close after Phase 4 smoke; ADR-0019 accepted 2026-05-24 at Plan 0009 close; ADR-0018 accepted 2026-05-23 at Plan 0008 close; previous accepted ADRs: ADR-0020 accepted 2026-05-22 at Plan 0007 close, ADR-0017 accepted 2026-05-20). Architect runs `Glob docs/architecture/plans/*.md` and `Glob docs/architecture/adrs/*.md` before drafting to pick the next numbers, never trusting memory.
- **One plan per file.** No "Plan 0004a" / "Plan 0004b" splits — if the work grows, write a new numbered plan and reference the parent.
- **Plans aren't ADRs.** A plan says *what we're building this week and how*; an ADR says *why we chose this design over the alternatives*. Plans expire; ADRs don't. If a plan's decision warrants permanent capture, the architect also writes an ADR (Mode 2).
- **Plans don't move until the architect's close ceremony.** Implementers commit per phase but never `git mv` a plan to `done/`. The close ceremony reviews the whole plan in one pass, then flips status + moves the file in a single architect-authored commit.
- **In-progress plans are append-only on substance.** The only mid-flight edit is the `Status:` line and minor honesty fixes (e.g. correcting a stale owner tag). Structural amendments — adding phases, rewriting done-when — happen via a new followup plan, not in-place.
- **Cross-references stay link-shaped.** When one plan references another's phase, use a markdown link (`[Plan 0004 phase 7](0004-...md)`) so the cross-ref survives renumbering and the close-ceremony move to `done/`.

## When you don't know which plan to start

Don't guess. The execution sequence above is the source of truth as of 2026-05-24 (Plans 0001 + 0002 + 0003 + 0004 + 0005 + 0006 + 0007 + 0008 + 0009 + 0015 closed; **Plan 0010 recommended next** — RSS news + per-headline VADER sentiment, three phases all `dev`, inheriting the resilience module Plan 0009 landed; remaining Tier 2 series 0010 → 0011 → 0012 sequenced linearly because each inherits Plan 0009 phase 1; Plan 0007 followup series 0013 → 0014 drafted 2026-05-22 and sequenced after Tier 2; ADR-0019 accepted at Plan 0009 close 2026-05-24; ADR-0018 accepted at Plan 0008 close 2026-05-23; ADR-0021 proposed alongside Plan 0014). Plan 0016 (golden-path smoke) is `approved` and out-of-band tooling — disjoint files from the Tier 2 series (`tests/smoke/`, root `package.json`, onboarding docs), so a `/dev` session can pick it up anytime without disturbing the 0010→0014 sequence. If reality has drifted (the user names a plan not in that sequence, or a status disagrees with a recent commit), trust `git log` and the plan's own `Status:` line over this README — and surface the drift so the README gets refreshed.
