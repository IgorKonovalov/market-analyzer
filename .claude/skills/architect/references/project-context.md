# Project context — market-analyser

This is the canonical context the architect skill grounds its decisions in. Keep it up to date — when something here goes stale, the skill makes worse decisions. **Trust the filesystem over this file**: before writing any ADR, plan, or diagram, `Glob` the three `docs/architecture/` subdirectories to inventory what actually exists. If they disagree, this file is stale — refresh it.

## What we're building

A **desktop application** for analyzing markets and authoring trading strategies, **driven primarily through Claude Code (CLI) via MCP** ([ADR-0015](../../../../docs/architecture/adrs/0015-claude-code-primary-control-surface.md)). The user can:

- Talk to an agent (Claude Code) that calls MCP tools on the sidecar to query market data, run analyses, render charts, and author/backtest strategies.
- Open the Electron viewer to see live chart renders, equity curves, screener tables, and to manage privileged operations (rotate the MCP secret, stop the sidecar).
- Run sibling-skill workflows (`market-analyst`, `defi-analyst`, `backtester`, `strategy-author`) that produce artifacts under `runs/`.

It is **not** a hosted service and **not** a paper-trading or live-trading platform (at least not initially — see open backlog). It **is** an MCP-server-bearing app: the sidecar mounts a Streamable-HTTP MCP endpoint at `/mcp` per [ADR-0014](../../../../docs/architecture/adrs/0014-mcp-as-second-sidecar-protocol.md), and Claude Code drives it as the primary client.

## Data layer — written in-house

Per [ADR-0009](../../../../docs/architecture/adrs/0009-rewrite-data-layer-in-house.md) (supersedes ADR-0003), the data layer is written in-house under `src/market_analyser/data/`. No upstream is mirrored; no `vendored/` tree exists. Reasons:

- The desktop app runs locally and the agent reaches it via the sidecar's own MCP transport.
- We control the data layer's evolution directly — caching, offline mode, schema changes — without coordinating with an external repo.
- Backtests are reproducible — one source of truth.

Every external HTTP adapter sits on a **shared resilience client** (`ResilientHttpClient`: TTL cache + retry + backoff + concurrency cap + proxy-from-env), designed in [ADR-0019](../../../../docs/architecture/adrs/0019-external-http-adapter-resilience.md) and landed as Plan 0009 phase 1. New adapters inherit it rather than re-implementing retry/backoff inline.

Data sources shipped as standalone adapters under `src/market_analyser/data/adapters/` (as of 2026-07-03 — all shipped; verify with `Glob`):

| Adapter / module                        | Purpose                                  | Landed via                          |
|-----------------------------------------|------------------------------------------|-------------------------------------|
| `yahoo.py` + `_yahoo_fetch.py`          | Equities OHLCV (absolute-range) / quote / search | Plans 0003, 0019, 0024, 0031  |
| `_http.py` (`ResilientHttpClient`)      | Shared resilience for external adapters  | Plan 0009 ([ADR-0019](../../../../docs/architecture/adrs/0019-external-http-adapter-resilience.md)) |
| Binance klines                          | Crypto-exchange OHLCV; exchangeInfo-membership routing (`BTCUSDT` → Binance, else Yahoo) | Plan 0058 ([ADR-0052](../../../../docs/architecture/adrs/0052-binance-exchange-data-source.md)) |
| Binance derivatives                     | Funding rate + open interest series      | Plan 0056                           |
| CoinMetrics community                   | BTC MVRV series (MVRV-only after the 0057 reshape) | Plan 0057 ([ADR-0053](../../../../docs/architecture/adrs/0053-onchain-valuation-source.md)) |
| TradingView screener                    | Screener queries                         | Plan 0009                           |
| RSS news + per-headline VADER sentiment | News feed + sentiment scoring            | Plan 0010                           |
| Crypto Fear & Greed (alternative.me)    | Market-level crypto sentiment + history  | Plans 0011, 0055                    |
| StockTwits sentiment                    | Per-symbol Bullish/Bearish label counts  | Plan 0012                           |
| CoinGecko                               | BTC macro context (dominance, total mcap) + accrual | Plans 0022, 0055           |
| Zerion (`defi/`)                        | DeFi wallet discovery + decoded tx history | Plans 0032, 0034 ([ADR-0034](../../../../docs/architecture/adrs/0034-defi-portfolio-aggregator.md)) |
| Reddit sentiment                        | Per-symbol social sentiment              | Deferred (keyword scoring fragile)  |

All market-data adapters dispatch through the `MarketDataProvider` Protocol ([ADR-0007](../../../../docs/architecture/adrs/0007-market-data-provider.md)) and the per-capability source Protocols + selector registry of [ADR-0031](../../../../docs/architecture/adrs/0031-data-source-adapter-contract.md). External metric series (F&G, dominance, funding, OI, MVRV) are historized in the one `metric_points` table behind the [ADR-0051](../../../../docs/architecture/adrs/0051-historized-metric-series-contract.md) `as_of` contract. The `as_of` seam, cache chokepoint, and lazy bring-in cadence are unchanged from ADR-0007.

## Stack

- **Backend.** Python 3.10+, managed with `uv`. Core deps: `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `alembic`, `mcp` (Streamable HTTP server), `psutil` (PID liveness for lockfile attach), `sse-starlette` (SSE stream). Data-source adapters add their own deps as they land (`tradingview-screener`, `tradingview-ta` in Plan 0009; `feedparser` + `vaderSentiment` in Plan 0010).
- **Frontend.** Electron + React + TypeScript live viewer ([ADR-0005](../../../../docs/architecture/adrs/0005-desktop-shell-electron.md), supersedes ADR-0001). Charts via `lightweight-charts`. Subscribes to the sidecar event stream per [ADR-0017](../../../../docs/architecture/adrs/0017-live-ui-updates-via-sse.md).
- **Sidecar IPC — renderer.** Local HTTP/JSON on `127.0.0.1`, per-sidecar-launch bearer-token shared secret ([ADR-0002](../../../../docs/architecture/adrs/0002-ipc-local-http.md), [ADR-0011](../../../../docs/architecture/adrs/0011-bearer-secret-transport.md), refined by [ADR-0016](../../../../docs/architecture/adrs/0016-standalone-sidecar-mode.md) — bearer now persisted in `sidecar.lock` `0600` for the attach window). The shared data-dir contract ([ADR-0020](../../../../docs/architecture/adrs/0020-shared-data-dir-contract.md)) keeps Python and Electron resolving the same user-data directory.
- **Sidecar IPC — agent (MCP).** Streamable HTTP at `/mcp` ([ADR-0014](../../../../docs/architecture/adrs/0014-mcp-as-second-sidecar-protocol.md)), long-lived bearer in `mcp-secret.json` (user data dir, `0600`).
- **Sidecar lifecycle.** Standalone process ([ADR-0016](../../../../docs/architecture/adrs/0016-standalone-sidecar-mode.md)); lockfile at `<user-data>/sidecar.lock`; Electron attaches idempotently (PID-liveness + `/healthz` identity check); closing the viewer does NOT stop the sidecar. Implemented in Plan 0007. One-command dev loop via `pnpm dev:all` (Plan 0015).
- **Live UI updates.** Server-Sent Events at `GET /events` (renderer-bearer-gated) carry typed versioned envelopes from the sidecar's in-process event bus, which lives in the neutral top-level `events/` core ([ADR-0017](../../../../docs/architecture/adrs/0017-live-ui-updates-via-sse.md), [ADR-0032](../../../../docs/architecture/adrs/0032-data-layer-no-api-dependency.md)). Event vocabulary as of 2026-07-03 (all v1): `chart.show/update/highlight/update_dropped`, `run.completed`, `signal.evaluated`, `recommendation.completed`, `ohlcv.backfill_*` (×3), `defi.scan_*` (×4), `alert.triggered`.
- **Persistence.** SQLite for all application data (cached bars, annotations, strategy metadata, backtest runs) + a hand-editable `config.json` for user config ([ADR-0006](../../../../docs/architecture/adrs/0006-persistence-layout.md)).
- **Data layer.** Single `MarketDataProvider` Protocol, dispatching to per-source adapters ([ADR-0007](../../../../docs/architecture/adrs/0007-market-data-provider.md)); implementations written in-house per [ADR-0009](../../../../docs/architecture/adrs/0009-rewrite-data-layer-in-house.md); external adapters share `ResilientHttpClient` ([ADR-0019](../../../../docs/architecture/adrs/0019-external-http-adapter-resilience.md)).
- **Backtest.** Pure `run(strategy, bars, params, **costs) -> BacktestResult` engine + metric helpers + thin `persist()` (disk + SQLite-indexed `backtest_runs` table); schema per [ADR-0018](../../../../docs/architecture/adrs/0018-backtest-result-schema.md). Implemented in Plan 0008.
- **Dependency discipline.** 14-day cooldown ([ADR-0012](../../../../docs/architecture/adrs/0012-dependency-cooldown.md)); exact pins on every direct dep, runtime and dev ([ADR-0013](../../../../docs/architecture/adrs/0013-pin-direct-dependencies.md)).

## Sibling skills

These skills consume your plans/ADRs. Design with them in mind. Full descriptions in each skill's SKILL.md; orientation in [`CLAUDE.md`](../../../../CLAUDE.md).

- **`dev`** — Generalist Python/sidecar/CI implementer. Reads plans, runs done-when checks per phase, commits per phase. Flips `Status: approved → in-progress` at session Step 2; never moves plans to `done/`.
- **`strategy-author`** — Trading-strategy code under `src/market_analyser/strategies/`.
- **`backtester`** — Engine + run artifacts under `src/market_analyser/backtest/` and `runs/`.
- **`ui-builder`** — Electron desktop viewer end-to-end under `desktop/`. **Post-ADR-0015: viewer for agent-issued renders, not the primary control surface.** Subscribes to the SSE event stream; renders chart commands in response.
- **`market-analyst`** — Read-only TradFi pattern/trend snapshots → `runs/analysis/`.
- **`defi-analyst`** — Read-only DeFi pool/position analyses → `runs/defi/`.
- **`advisor`** — The one sanctioned recommendation layer ([ADR-0029](../../../../docs/architecture/adrs/0029-advisory-recommendation-boundary.md); created 2026-07-02): turns conditions into a labeled advisory call via the `recommend` tool → `runs/advice/`. Never an implementer — plans don't assign phases to it.
- **`skill-creator`** — Skill maintenance (meta).

(A future **`trader`** skill is mandated by [ADR-0025](../../../../docs/architecture/adrs/0025-trade-execution-feasibility.md) invariant 3 for the execution arc — Plan 0044's companion step; it does not exist yet.)

When you write a plan, **name the sibling skill that will own each phase** via the `**Owner skill:**` tag (fixed vocabulary: `dev` / `strategy-author` / `backtester` / `ui-builder` / `human`). That's the handoff seam — see [`templates/cross-skill-handoff.md`](templates/cross-skill-handoff.md).

## Close-ceremony handoff from the implementing skill

After the implementing skill finishes the **last** phase of a plan, it ends the session by prompting the user to start a fresh `/architect` session with a structured brief (plan number, phases shipped, commits made, done-when results, notes). When you receive such a brief — typically as the user's first message in the new session — you handle the close ceremony:

1. **Review the whole plan's implementation** against the plan and related ADRs using the Mode 4 workflow. Deliver the review in-conversation; do **not** write a review file.
2. **Update the plan's `Status:` line** to `Status: done`.
3. **Move the plan file** to `docs/architecture/plans/done/<NNNN-slug>.md`.
4. **Refresh `docs/architecture/plans/README.md`** — remove from active roster, update execution order, confirm next-free-number.
5. **Flip any paired ADR** from `proposed` to `accepted` if the plan's close confirms the decision held.

The fresh-session boundary is deliberate — review benefits from a clean context. Don't try to do dev work and architect review in the same session.

If the user asks for a mid-plan checkpoint review (rare — they explicitly ask while phases are still in flight), do the review in-conversation but **do not** flip status to `done` or move the file.

## ADRs

Live in `docs/architecture/adrs/`. The committed index at [`docs/architecture/adrs/README.md`](../../../../docs/architecture/adrs/README.md) is the source of truth for the roster, per-ADR status, supersede/refine lineage, and the next free number — read it (or `Glob docs/architecture/adrs/*.md`) rather than trusting a snapshot table here. As of 2026-07-09: 64 ADRs (0001–0064), next free is **0065**. Two superseded (0001→0005, 0003→0009); per-ADR status (accepted / proposed) lives in the index — read it rather than trusting an enumeration here, which goes stale as plans close and accept their paired ADRs.

## Open ADR backlog

Pick one when the user asks for a starter design task; or when the corresponding plan starts to need the decision.

- **Offline mode.** Does the app function without network? If yes, what's cached and for how long? Partially answered by ADR-0006's `bars` cache and ADR-0007's `as_of` seam, but the explicit policy ("what does the app *do* with no network on cold start?") is not yet captured.
- **Migration safety policy.** Today's migrations are additive only (chain head: `0005_watches_alerts`). Need a rule before the first non-additive migration lands.
- **Crash supervision + general scheduling for the standalone sidecar.** ADR-0016 deferred automated restart-on-crash; [ADR-0055](../../../../docs/architecture/adrs/0055-in-sidecar-watch-scheduler.md)'s scheduler is deliberately watch-scoped, not a general job runner. A future ADR covers timed ingestion / digests / restart supervision.
- **OS-native notification transport.** In-app alert delivery shipped (Plan 0060 toast + Alerts view); native desktop notifications need their own transport ADR.
- **LLM-output provenance schema.** ADR-0040 covers model artifacts; a "produced by model@version via tools […]" trail for agent-written facts is unwritten.

(Resolved since this list was first drafted: third-party API keys → [ADR-0038](../../../../docs/architecture/adrs/0038-third-party-api-key-storage.md); model versioning/determinism → [ADR-0040](../../../../docs/architecture/adrs/0040-forecasting-model-artifacts.md). The full open/decided table lives in [`roadmap.md` § Cross-cutting decisions ahead](../../../../docs/architecture/roadmap.md#cross-cutting-decisions-ahead).)

## Current state of the codebase

Snapshot as of 2026-07-09 (Plan 0070 close) — a working agent-driven app spanning analysis, forecasting, advice, DeFi, portfolio aggregation, and alerting. **Verify with `Glob` before relying on any line here.**

- `docs/architecture/adrs/` — 64 ADRs (0001–0064), next free **0065**; roster + statuses in [`adrs/README.md`](../../../../docs/architecture/adrs/README.md).
- `docs/architecture/plans/` — active (all `approved`, none in-progress): 0040, 0042–0046, 0066–0069, 0071. Everything else is closed under `plans/done/`. Next free plan number is **0072**. The roster, recommended execution order, and status vocabulary live in [`plans/README.md`](../../../../docs/architecture/plans/README.md) — read it first.
- `docs/architecture/diagrams/` — `claude-cli-driven-architecture.md` (the authoritative system map + lifecycle/recovery sequences; refreshed 2026-07-03), `strategy-execution-sequence.md` (backtest runtime order / anti-lookahead seam), `bootstrap-component-map.md` (OHLCV data flow + SQLite schema reference).
- `src/market_analyser/` — sidecar source: `api/` (FastAPI app + FastMCP hub `mcp_app.py` + one module per tool under `mcp_tools/` + `routes/`), `data/` (provider + ADR-0031 source registry + adapters + timeframes + metric-series registry), `persistence/` (SQLite + Alembic, migrations `0001`–`0007`), `events/` (neutral bus + envelope registry), `contracts/` + `strategies/` (8 modules, flat/long/short), `backtest/` (engine + metrics + walk-forward + live-signal eval), `analysis/` (indicators, candlestick + chart patterns, levels, volume, cycles, snapshot), `forecast/` (ADR-0030/0040), `advisor/` (ADR-0029), `alerts/` (ADR-0055 scheduler), `defi/` (ADR-0035), `portfolio/` (ADR-0042, read-only cross-venue aggregation — Plan 0041), `apiref/` (ADR-0064 generated API reference — Plan 0070), `annotations/`. **Not yet in the tree:** `execution/` (Plans 0044–0046).
- `desktop/` — Electron + React + TS renderer: 7 nav tabs (Chart, Backtests, Signals, Recommendations, News, Alerts, Settings) over 8 views, theme system (ADR-0039), SSE subscriber with per-payload Zod validation for the newest events, typed fetch client. One-command dev loop via `pnpm dev:all`.
- `.claude/skills/` — `architect`, `dev`, `ui-builder`, `strategy-author`, `backtester`, `market-analyst`, `defi-analyst`, `advisor`, `skill-creator`, `safe-commit`.

**Workflow rule (learned from a 2026-05-17 incident):** Before writing any ADR, plan, or diagram, `Glob` the three `docs/architecture/` subdirectories to inventory what exists. Trust the filesystem, not this section — if the two disagree, the section is stale and should be refreshed as a follow-up. (See also the `feedback_glob_before_draft` memory.)

## Glossary

- **Phase** — a small, end-to-end change that delivers user value. Plans break work into ordered phases; the whole plan ships as one batch (no architect review between phases).
- **Sibling skill** — one of `dev`, `strategy-author`, `backtester`, `ui-builder`, `market-analyst`, `defi-analyst`, `skill-creator` — peers to this architect skill in the same project.
- **Vendor** — copy code from another repo into ours, take ownership of it. Opposite of "depend on". (Project policy is no-vendor per ADR-0009.)
- **Sidecar** — the Python process that mounts FastAPI + MCP and owns the data layer. Standalone per ADR-0016; auto-attached by Electron via the lockfile.
- **Live viewer** — the Electron renderer, post-ADR-0015. Subscribes to the sidecar's SSE event stream and renders agent-issued chart commands. Not a primary control surface.
- **Primary control surface** — Claude Code (CLI) via MCP, post-ADR-0015. The user's primary input device.
