# Plans

Implementation plans for `market-analyser`. One file per plan (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Closed plans live in [`done/`](done) — the architect moves them there at the close ceremony, never the implementer.

This index is the one-minute view of what's in flight. The plan file (and `git log`) is the source of truth; if a status here disagrees with a plan's own `Status:` line, trust the plan file and fix this index.

## Active roster

| #    | File | Status | Summary |
|------|------|--------|---------|
| 0020 | [0020-backtest-metrics-walk-forward](0020-backtest-metrics-walk-forward.md) | approved | Extended `BacktestMetrics` (Calmar/Sortino/profit factor/expectancy) + rolling walk-forward eval + `compare_strategies`/`walk_forward_backtest` tools. Bumps `ENGINE_VERSION`, regenerates the 0008 golden fixture. Paired [ADR-0024](../adrs/0024-extended-backtest-metrics.md) (accepts at close). 3 phases: `backtester`×2 → `dev`. |
| 0022 | [0022-macro-context](0022-macro-context.md) | approved | `bitcoin_market_pulse` (CoinGecko global — BTC dominance + total mcap + neutral regime) via a new `get_macro_context` Protocol method, plus `market_snapshot` fanning `get_quote`. Paired [ADR-0027](../adrs/0027-crypto-macro-regime-classification.md) (accepts at close). Unblocked (0019 closed). 3 phases, all `dev`. |
| 0023 | [0023-news-view-in-app](0023-news-view-in-app.md) | approved | First UI surface for Plan 0010's news/sentiment: a standalone **News** view over `GET /news`. Live-only, sanitized text. Unblocked (0010 closed). Edits `App.tsx`/`client.ts`. 2 phases: `dev` → `ui-builder`. |
| 0026 | [0026-live-signal-evaluator](0026-live-signal-evaluator.md) | approved | First step of the advisor + forecasting track ([ADR-0029](../adrs/0029-advisory-recommendation-boundary.md)/[ADR-0030](../adrs/0030-forecasting-subsystem.md)). Evaluate a strategy against the **current** bar (vs `run_backtest`'s historical path): pure `backtest/live_signal.py` + an `evaluate_signals` tool + `signal.evaluated v1` SSE + a reactive viewer panel. Stays a condition-reporter (no recommendations). 3 phases: `backtester` → `dev` → `ui-builder`. |

## Recently closed

Full close-review notes live in each plan file under `done/`. Most-recent first.

| #    | Closed | Summary |
|------|--------|---------|
| 0031 | 2026-06-03 | Yahoo absolute-range fetch — switched the OHLCV fetcher from now-relative `range=` to absolute `period1`/`period2`, so a past-ending window is fetched verbatim (`_yahoo_fetch.py`/`yahoo.py`); lifted the artificial 732-day span cap for uncapped timeframes; added the 2026-05-31 error-envelope guard. Phase 2 (finding M1) mapped the typed `UpstreamDataError` family to `/ohlcv` HTTP status (404/429/422/502) over the real provider+adapter chain — the route's 502 branch was dead for the real path — and recency-gated the empty-response branch ([ADR-0033](../adrs/0033-empty-ohlcv-response-by-recency.md) accepted): a strictly-historical empty window returns `[]`, a leading-edge empty window raises `UnknownSymbolError`→404. **Unblocked 0030.** Clean Mode 4 (no blockers; one redundant-catch nit left as-is). Live smoke: 249 AAPL 1d bars over a past year (pre-fix ~11). |
| 0030 | 2026-06-03 | Lazy historical loading (scroll-left to fetch older bars) — `useOhlcvHistory` accumulating buffer hook + `useLazyHistoryTrigger` visible-range trigger + scroll-anchored prepend (shift-right-by-N) + left-edge affordance/error chip. Rides the existing synchronous `GET /ohlcv` (no new route, no `dev` work); reacts to 422 (clamp) / empty (`reachedStart`) instead of mirroring data-layer constants. The deterministic seeded-cache Playwright e2e (`0cd7f7a`, real — asserts barCount growth + second fetch) is the close gate; the live path was dead until [Plan 0031](done/0031-yahoo-absolute-range-fetch.md), closed in the same ceremony. No new ADR (applies ADR-0007/0008). Clean Mode 4 (no blockers). |
| 0028 | 2026-06-02 | Data-layer boundary & extensibility hardening (2026-05-31 audit): per-capability source Protocols (`data/sources.py`, 7 `@runtime_checkable`) + selector-registry dispatch so adding a sentiment/market-sentiment source is one dict entry, not a 4-5-file edit; extracted the inlined VADER aggregation into `RssVaderSentimentAdapter`; relocated `EventBus`+schema from `api/events/` to a neutral top-level `events/` core, killing the lone `data→api` import (`data.backfill` now pulls zero `api` modules). [ADR-0031](../adrs/0031-data-source-adapter-contract.md) + [ADR-0032](../adrs/0032-data-layer-no-api-dependency.md) accepted at close. Behavior-preserving — 802 offline specs + renderer parity guard green. Clean Mode 4 (no blockers; one docstring nit left as-is). |
| 0029 | 2026-06-02 | Decompose the 618-line `CandlestickChart.tsx` god component (audit): extracted `useChartGestures` (the 8 prop-mirror refs are gone), moved marker/overlay math to `lib/markers.ts` + `lib/overlays.ts`, collapsed the 4-spot overlay seam into one `OVERLAY_REGISTRY` table (adding a kind is now one row). Behavior-preserving — component 618 → 364 lines; 200 renderer specs + the `__test_chart_render__` gate green. No new ADR (applies ADR-0008). Clean Mode 4 (no blockers); two nits left as opportunistic cleanup (pre-existing `act()` warning in `gestures.test.tsx`; a fix-forward two-commit phase-2 split). **Unblocks 0030.** |
| 0021 | 2026-06-02 | Multi-timeframe alignment (`multi_timeframe_analysis`, W→D→4H→1H→15m) + three volume-scanner tools (`volume_breakout`/`volume_confirmation`/`smart_volume`) over a supplied symbol list. Consumes the 0018 snapshot + 0027 volume primitives; default ladder unblocked by 0025. No new ADR (within ADR-0023/0007). Clean Mode 4 (no blockers); one Minor + 3 nits fixed pre-close in `fd35da3`. |
| 0027 | 2026-05-31 | Volume bars + volume-aware analysis — `analysis/volume.py` (volume MA, relative volume + percentile, OBV + slope, trailing VWAP, `volume_summary`); folds a `volume_stance` + volume measures into `condition_snapshot`; renders volume/VWAP/OBV chart bands client-side from `bars`. No new ADR (within ADR-0023/0008). **Unblocks 0021's phase 2.** |
| 0019 | 2026-05-31 | Live quote — `get_quote` (`YahooQuoteAdapter` on the chart endpoint) + `quote_for` tool. Discharged the last `MarketDataProvider` stub. **Unblocks 0022.** |
| 0025 | 2026-05-30 | Timeframe expansion — `15m`/`1w` (native) + `4h` (in-house resample); canonical `data/timeframes.py` registry; `history_exceeded` reason. ADR-0028 accepted. **Unblocks 0021's timeframe half.** |
| 0018 | 2026-05-30 | Technical-analysis surface — `analysis/` (9 indicators + 14 patterns + `condition_snapshot`) + `analyze_symbol` tool. ADR-0023 accepted. **Unblocks `market-analyst` + 0021.** |
| 0017 | 2026-05-30 | MCP tool-registration refactor — migrated 8 inline tools to the `register_*` pattern; `mcp_app.py` 576 → 131 lines. Behavior-preserving. |
| 0014 | 2026-05-30 | Interactive chart + agent-mode — UI-event vocabulary + `POST /ui_events` + `get_pending_ui_events` + agent-mode toggle + single-instance Electron. ADR-0021 accepted. |
| 0024 | 2026-05-29 | Symbol search + autocomplete — `search_symbols` over Yahoo `/v1/finance/search` + `GET /search` + debounced `SymbolPicker`. ADR-0026 accepted. |
| 0013 | 2026-05-26 | Auto-backfill on cache miss — honest `get_ohlcv` shape + `backfill_ohlcv` tool + `ohlcv.backfill_*` SSE + typed `UpstreamDataError` taxonomy + `BackfillCoordinator`. |
| 0012 | 2026-05-25 | StockTwits sentiment — second per-symbol source (explicit labels, no NLP); `source` param on `get_sentiment`; first `ResilientHttpClient` subclass. |

Earlier closed plans (0001–0011, 0015, 0016) are in [`done/`](done) and `git log`.

## Open follow-ups (no plan needed)

Small items carried from closed plans — pick up opportunistically; remove the row when it lands. If one grows past ~half a day or starts coupling other changes, promote it to a real plan.

| From | Item | Owner | Note |
|------|------|-------|------|
| 0008 | Trade-log P&L $ formula compounds inconsistently with the engine for multi-trade runs | `architect` → `ui-builder` / `backtester` | Plan 0008 pinned UI P&L as `(exit-entry)*(initial/entry)`, but `_build_equity_curve` compounds (`units = cash/entry` where `cash` is running equity). Row N>1 disagrees with the equity-curve delta (~10% on the 2-trade example). Either add a `Trade.pnl_usd` field (ADR-0018 amendment — becomes its own plan) or derive in the renderer from `equity_curve`. |
| 0008/0009/0013 | Refresh `diagrams/claude-cli-driven-architecture.md` with backtest-results, screener, and backfill lanes | `architect` | The map predates `run_backtest`/`BacktestView` (0008), `screener_query`/TradingView (0009), and `backfill_ohlcv`/`BackfillCoordinator`/`ohlcv.backfill_*` (0013). One pass adds all three. |
| 0012 | `news_for.py:31` still declares the window vocabulary inline | `dev` | The window hoist (`data/_windows.py`, commit `bccfc61`) migrated `sentiment_for_news` + `stocktwits_sentiment` but missed `news_for`, which carries the same `Literal["1h","4h","24h","7d"]`. Import `SentimentWindow` there too, then `gen-types --check` (stays clean — pydantic inlines the alias). |
| 0014 | Confirm whether Claude Code surfaces `notifications/resources/updated` to the model | `human`/`architect` | The phase-4 smoke confirmed the polling tool but not the best-effort push path. Polling is the contract regardless; re-measure before any plan leans on push (ADR-0021 open question). |

_The Plan 0013 `data→api` smell was promoted to [Plan 0028](done/0028-data-layer-boundary-hardening.md) phase 3 ([ADR-0032](../adrs/0032-data-layer-no-api-dependency.md)) after the 2026-05-31 audit re-flagged it — **resolved at 0028 close (2026-06-02)**: the event bus now lives in the neutral top-level `events/` core._

### From the 2026-05-31 architecture audit

Minors surfaced by the codebase-health audit. The three majors became Plans [0028](done/0028-data-layer-boundary-hardening.md)/[0029](done/0029-candlestick-chart-decomposition.md) (both closed); these are the small items. Same rule — pick up opportunistically; promote if one grows past ~half a day or couples other changes.

| Area | Item | Owner | Note |
|------|------|-------|------|
| data | `Bar` permits zero prices → latent `ZeroDivisionError` | `dev` | `data/types.py` validator rejects `< 0` but allows `0.0`; `backtest/metrics.py:102,238` divides by entry/price. Tighten OHLC to `> 0` or guard the division. |
| api | MCP tools read wall-clock inline (snapshot-testability) | `dev` | ~8 tools call `datetime.now(tz=UTC)` for `queried_at`/`analyzed_at` with no seam (`analyze_symbol.py:97,100`, `quote_for.py:61`, `news_for.py:74`, …), blocking output snapshot-equality tests. Route through a module-level `_now()` seam like `default_provider._now`. |
| api | `ohlcv` route imports a data-internal underscore symbol | `dev` | `api/routes/ohlcv.py:15` imports `ResilientHttpError` from `data._http`; re-export it via the public `data/errors.py` and import from there. |
| api | No safety-net test that every MCP tool is registered | `dev` | `tests/api/test_mcp_tools.py:441` only checks a 3-tool subset; a forgotten `register_*` call yields a silently-missing tool. Assert the full expected toolset is registered. |
| desktop | SSE payloads cast unvalidated (IPC/SSE asymmetry) | `ui-builder` | `useEventStream.ts:216-235` validates only the envelope wrapper, then casts `payload as ChartShowPayloadV1` and hands it to the reducer raw — IPC is Zod-validated, SSE is not. Add Zod `safeParse` per event payload in `dispatchEnvelope`. **Consider promoting** if more event types land. |
| desktop | `sidecar:status` not parsed + raw channel literal | `ui-builder` | `main.ts:80` sends `'sidecar:status'` as a string literal (every other channel uses `IPC_CHANNELS`); `client.ts:62-73` consumes the payload without `SidecarStatusSchema.parse()`, forcing two `!` assertions. Parse on the renderer side and reference the constant. |
| desktop | Test gaps: `format.ts` + IPC handlers | `ui-builder` | `renderer/lib/format.ts` (NaN→"—", rounding, sign) has no direct test; `appHandlers.ts`/`dialogHandlers.ts`/`shellHandlers.ts` have no unit test (only `sidecarHandlers` does — and `shellHandlers` is a security boundary). Add `format.test.ts` + handler registration/validation specs. |
| desktop | `client.test.ts` shares module state across cases | `ui-builder` | `client.test.ts:69-171` reads "whatever port a prior test left" instead of pinning — order-coupled. Wrap the config-refresh block in `jest.isolateModules` or reset the module cache in `beforeEach`. |

_The `data` "Yahoo error-envelope not guarded" item was absorbed into [Plan 0031](done/0031-yahoo-absolute-range-fetch.md) (same file `_yahoo_fetch.py`, same parse path) and **shipped with it (closed 2026-06-03)** — the `_parse_chart_payload` guard now raises the typed taxonomy instead of a raw `KeyError`/500._

## Recommended execution order

The committed roster (Tier 2 0009–0012, then 0013–0014) is fully closed, as are the capability-expansion gates 0018, 0019, 0025, and 0027, the analysis batch's 0021, and (2026-06-03) the lazy-loading pair 0030/0031. **Pickup-ready now, all `approved` and on largely disjoint files (user may re-prioritise): 0020, 0022, 0023, 0026.** Serialize where files overlap:

- **0023 edits `App.tsx`/`client.ts`** — its collisions (0013/0014) are landed, so it now inherits rather than conflicts. It now also brushes `OhlcvView.tsx`'s neighbourhood that 0030 just closed against; trivial, but the same renderer surface.
- **0026** (approved 2026-06-02, pickup-ready) opens the advisor + forecasting track ([ADR-0029](../adrs/0029-advisory-recommendation-boundary.md)/[ADR-0030](../adrs/0030-forecasting-subsystem.md), both proposed); it depends on nothing not already shipped and runs largely parallel to the analysis batch. Scoped at approval to the full `data/timeframes.py` set (15m/1h/4h/1d/1w) now that Plan 0025 has landed. Mixed-owner: `backtester` (phase 1) → `dev` (phase 2) → `ui-builder` (phase 3); phase 2 touches the event bus — now relocated to the neutral top-level `events/` core (Plan 0028, landed), so import from `market_analyser.events`, not `api.events`. Its `ui-builder` phase 3 adds a viewer panel on the chart that now coexists with 0030's (landed) scroll-left lazy-loading on the same `CandlestickChart`/`OhlcvView` surface — keep the visible-range subscription separate from any new panel wiring.
- **0022** (Yahoo provider wiring / `data/adapters/`) no longer has a live-window collision risk with 0031 (closed) — the absolute-range fetch path has landed and conforms to `OhlcvSource`; a new `get_macro_context` adapter sits beside it.

The architect close ceremony serializes plan completion regardless: data-adapter plans (`data/adapters/`, `mcp_tools/`) and renderer plans (`App.tsx`/`client.ts`) shouldn't run in parallel against the same files. Plan history (the per-plan Gantt of who shipped what when) lives in `git log` and each plan's `done/` file.

## Parallel execution

Multiple `approved` plans can be implemented at once by giving **each implementer its own git worktree** (one branch off `main` per plan), so no two agents share a working tree. The default is still one session per plan; this is the opt-in pattern when you want throughput across plans the roster already marks disjoint. Worktrees isolate *files* — but three collisions survive file isolation in this repo and must be handled explicitly:

1. **Shared runtime (port + DB + lockfile + agent-mode file).** All resolve to one per-user app-data dir, and the sidecar refuses to start if a lockfile is already present (ADR-0016). Any done-when check that launches the **real sidecar process** (Playwright/Electron e2e, manual smoke, `pnpm dev`) collides across worktrees. Fix — set both per worktree before running such checks: `MARKET_ANALYSER_DATA_DIR=<unique temp dir>` (isolates DB + lockfile + agent-mode file in one move; taken verbatim per `config.py`) and run the sidecar with `--port=0` (OS-picked port, prints `PORT=<n>`). In-process `TestClient` unit tests bind no port and use temp DBs, so they parallelize without this.
2. **Alembic migrations are one linear chain.** `migrations/versions/` is a single sequence; two plans that each add a migration both branch off the same head → two heads at merge. There is no runtime fix — **do not parallelize two plans that touch `persistence/migrations/`**, or serialize just the migration-adding phase. Confirm the chosen set is migration-free before fanning out.
3. **Index + numbering races.** This README, `adrs/README.md`, and the next-free `NNNN` are shared append-points. **Assign all plan/ADR numbers up front** (architect, before fan-out) and expect trivial merge conflicts on the index files — resolve them at the close ceremony, or let one session own index writes.

**Workflow:**
1. **Architect, up front:** pick a disjoint set from the roster (the "largely disjoint files" sets the execution-order section already curates), confirm no two touch `persistence/migrations/`, assign any new numbers now.
2. **Per plan:** one worktree + branch; export a unique `MARKET_ANALYSER_DATA_DIR`; run sidecar/e2e on `--port=0`. The agent runs the plan's contiguous phases, committing per phase on its branch. **Never push.**
3. **Merge back through the close ceremony** — which is already serialized (architect reviews one plan at a time, flips status, moves to `done/`). That is the natural merge gate; do the branch merges one at a time there.

A **mixed-owner plan** (e.g. 0026 `backtester → dev → ui-builder`) is a *sequential* skill chain *within one worktree* — that is the cross-skill handoff protocol, not parallelism. Parallelism is **across plans** (worktree per plan), never across phases of one plan.

## Status vocabulary

| Status | Meaning |
|--------|---------|
| `draft` | Author wrote it; no user "go" yet. Implementers ignore. |
| `approved` | User signed off. Implementers may pick up. |
| `in-progress` | An implementing skill flipped it at Step 2 of its session. |
| `implementation complete — pending …` | All phases shipped; close blocked on a named followup or review delta. |
| `done` | Close ceremony fired; file lives in `done/`. |
| `abandoned` | Killed before completion. Stays here for the record. |
| `superseded by NNNN` | A later plan replaced this one. |

Only `architect` and the implementing skill (at Step 2) may mutate `Status:`. Implementers flip `draft → in-progress`; architect handles every other transition.

## Owner-skill vocabulary (per phase)

Each phase carries `**Owner skill:**` with exactly one value, backticked:

- `` `dev` `` — Python sidecar, persistence, CI, tooling, non-UI Electron.
- `` `ui-builder` `` — anything under `desktop/`.
- `` `strategy-author` `` — `src/market_analyser/strategies/`.
- `` `backtester` `` — `src/market_analyser/backtest/` and `runs/`.
- `` `human` `` — user-only task (rare).

Mixed-owner plans hand off at every boundary per the [cross-skill handoff protocol](../../../.claude/skills/architect/references/templates/cross-skill-handoff.md). Missing or ambiguous tags fail Mode 4 review as blockers.

## Conventions

- **Numbering** is sequential, zero-padded to four digits, independent for plans and ADRs. **Next free plan: 0032. Next free ADR: 0034** (see [`../adrs/`](../adrs/)). Architect runs `Glob docs/architecture/plans/*.md` and `…/adrs/*.md` before drafting — never trusting memory.
- **One plan per file.** No `0004a`/`0004b` splits — if work grows, write a new numbered plan and reference the parent.
- **Plans aren't ADRs.** A plan says *what we're building and how* (it expires); an ADR says *why we chose this over the alternatives* (it doesn't). If a plan's decision warrants permanent capture, write an ADR too.
- **Plans don't move until the close ceremony.** Implementers commit per phase but never `git mv` to `done/`. The architect reviews the whole plan, then flips status + moves the file in one commit.
- **In-progress plans are append-only on substance.** The only mid-flight edits are the `Status:` line and minor honesty fixes. Structural amendments happen via a new followup plan.
- **Cross-references stay link-shaped** so they survive renumbering and the move to `done/`.
