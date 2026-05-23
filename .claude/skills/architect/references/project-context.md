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

Data sources written (or in flight) as standalone adapters under `src/market_analyser/data/adapters/`:

| Adapter / module                        | Purpose                                  | Status                              |
|-----------------------------------------|------------------------------------------|-------------------------------------|
| `yahoo.py` + `_yahoo_fetch.py`          | Equities / historical OHLCV              | Shipped (Plan 0003)                 |
| `_http.py` (`ResilientHttpClient`)      | Shared resilience for external adapters  | Shipped (Plan 0009 phase 1)         |
| `tradingview_screener.py`               | TradingView screener queries             | In progress (Plan 0009)             |
| RSS news + per-headline VADER sentiment | News feed + sentiment scoring            | Approved (Plan 0010)                |
| Crypto Fear & Greed (alternative.me)    | Market-level crypto sentiment            | Approved (Plan 0011)                |
| StockTwits sentiment                    | Per-symbol Bullish/Bearish label counts  | Approved (Plan 0012)                |
| `analysis/indicators.py`                | Technical indicators (RSI, MACD, etc.)   | Future (no plan yet; analyst dep)   |
| Reddit sentiment                        | Per-symbol social sentiment              | Deferred (keyword scoring fragile)  |

All adapters dispatch through the `MarketDataProvider` Protocol ([ADR-0007](../../../../docs/architecture/adrs/0007-market-data-provider.md)). The Protocol shape, `as_of` seam, cache chokepoint, and lazy bring-in cadence are unchanged from ADR-0007 — only the implementation policy moved from "mirror upstream" to "own it directly".

## Stack

- **Backend.** Python 3.10+, managed with `uv`. Core deps: `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `alembic`, `mcp` (Streamable HTTP server), `psutil` (PID liveness for lockfile attach), `sse-starlette` (SSE stream). Data-source adapters add their own deps as they land (`tradingview-screener`, `tradingview-ta` in Plan 0009; `feedparser` + `vaderSentiment` in Plan 0010).
- **Frontend.** Electron + React + TypeScript live viewer ([ADR-0005](../../../../docs/architecture/adrs/0005-desktop-shell-electron.md), supersedes ADR-0001). Charts via `lightweight-charts`. Subscribes to the sidecar event stream per [ADR-0017](../../../../docs/architecture/adrs/0017-live-ui-updates-via-sse.md).
- **Sidecar IPC — renderer.** Local HTTP/JSON on `127.0.0.1`, per-sidecar-launch bearer-token shared secret ([ADR-0002](../../../../docs/architecture/adrs/0002-ipc-local-http.md), [ADR-0011](../../../../docs/architecture/adrs/0011-bearer-secret-transport.md), refined by [ADR-0016](../../../../docs/architecture/adrs/0016-standalone-sidecar-mode.md) — bearer now persisted in `sidecar.lock` `0600` for the attach window). The shared data-dir contract ([ADR-0020](../../../../docs/architecture/adrs/0020-shared-data-dir-contract.md)) keeps Python and Electron resolving the same user-data directory.
- **Sidecar IPC — agent (MCP).** Streamable HTTP at `/mcp` ([ADR-0014](../../../../docs/architecture/adrs/0014-mcp-as-second-sidecar-protocol.md)), long-lived bearer in `mcp-secret.json` (user data dir, `0600`).
- **Sidecar lifecycle.** Standalone process ([ADR-0016](../../../../docs/architecture/adrs/0016-standalone-sidecar-mode.md)); lockfile at `<user-data>/sidecar.lock`; Electron attaches idempotently (PID-liveness + `/healthz` identity check); closing the viewer does NOT stop the sidecar. Implemented in Plan 0007. One-command dev loop via `pnpm dev:all` (Plan 0015).
- **Live UI updates.** Server-Sent Events at `GET /events` (renderer-bearer-gated) carry typed versioned envelopes from the sidecar's in-process event bus ([ADR-0017](../../../../docs/architecture/adrs/0017-live-ui-updates-via-sse.md)). Producers shipped so far: `chart.show/update v1`, `highlight_pattern`, `run.completed v1` (backtests). Implemented in Plan 0007; first backtest producer in Plan 0008.
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
- **`skill-creator`** — Skill maintenance (meta).

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

Live in `docs/architecture/adrs/`. Next free ADR number is **0022**.

| #     | Title                                                       | Status                          |
|-------|-------------------------------------------------------------|---------------------------------|
| 0001  | Tauri vs Electron                                           | superseded by ADR-0005          |
| 0002  | IPC over localhost HTTP with bearer auth                    | accepted (secret transport refined by ADR-0011) |
| 0003  | Vendoring strategy (mirror upstream)                        | superseded by ADR-0009          |
| 0004  | Strategy interface (Params + generate_signals + META)       | accepted                        |
| 0005  | Desktop shell — Electron + React + TypeScript               | accepted (supersedes ADR-0001)  |
| 0006  | Persistence layout — SQLite + config.json                   | accepted                        |
| 0007  | MarketDataProvider Protocol                                 | accepted                        |
| 0008  | Electron shell conventions                                  | accepted (TS config partly superseded by ADR-0010) |
| 0009  | Rewrite data layer in-house (supersedes ADR-0003)           | accepted                        |
| 0010  | tsconfig solution layout                                    | accepted                        |
| 0011  | Bearer secret transport — env-var, not argv                 | accepted (refined by ADR-0016)  |
| 0012  | Dependency cooldown (14 days)                               | accepted                        |
| 0013  | Pin every direct dependency exactly                         | accepted                        |
| 0014  | MCP as a second sidecar protocol (Streamable HTTP at /mcp)  | accepted (refined by ADR-0015)  |
| 0015  | Claude Code (MCP) as primary control surface                | accepted                        |
| 0016  | Standalone sidecar mode + idempotent attach                 | accepted                        |
| 0017  | Live UI updates via SSE event stream                        | accepted                        |
| 0018  | Backtest result schema                                      | accepted (Plan 0008 close)      |
| 0019  | External HTTP adapter resilience (`ResilientHttpClient`)    | proposed (accepts at Plan 0009 close) |
| 0020  | Shared data-dir contract (Python ↔ Electron)                | accepted (Plan 0007 close)      |
| 0021  | Renderer → agent feedback (UI events via MCP resource)      | proposed (accepts at Plan 0014 close) |

## Open ADR backlog

Pick one when the user asks for a starter design task; or when the corresponding plan starts to need the decision.

- **Offline mode.** Does the app function without network? If yes, what's cached and for how long? Partially answered by ADR-0006's `bars` cache and ADR-0007's `as_of` seam, but the explicit policy ("what does the app *do* with no network on cold start?") is not yet captured. Plan 0013 (auto-backfill on cache miss) touches the edges of this but doesn't decide the whole-app policy.
- **Third-party data-source API keys.** Secrets schema, rotation, and Settings UI. Out of scope so far; needed before any *authenticated* external data source (the Tier 2 sources in Plans 0009–0012 are all keyless).
- **Migration safety policy.** Today's migrations are additive only. Need a rule before the first non-additive migration lands.
- **Crash supervision for the standalone sidecar.** ADR-0016 deferred automated restart-on-crash. A future ADR may introduce a tray-app supervisor or OS service integration (LaunchAgent / systemd-user / Task Scheduler) if manual restart UX becomes painful.

## Current state of the codebase

The repository has substantial architecture documentation AND a working agent-driven app: walking-skeleton OHLCV chart, MCP foundation, strategy contract + reference strategies, a backtest engine, and the live SSE viewer.

- `docs/architecture/adrs/` — 21 ADRs (0001–0021); two superseded (0001, 0003), two proposed (0019, 0021), the rest accepted.
- `docs/architecture/plans/` — active (all `approved`): 0009 (in progress), 0010, 0011, 0012, 0013, 0014. Closed under `plans/done/`: 0001–0008 and 0015. Next free plan number is **0016**. The roster, recommended execution order, and status vocabulary live in [`plans/README.md`](../../../../docs/architecture/plans/README.md) — read it first.
- `docs/architecture/diagrams/` — `claude-cli-driven-architecture.md` (the authoritative system map + lifecycle/recovery sequences, post-ADR-0015), `strategy-execution-sequence.md` (backtest runtime order / anti-lookahead seam), `bootstrap-component-map.md` (OHLCV walking-skeleton data flow + SQLite schema reference).
- `src/market_analyser/` — sidecar source: `api/` (FastAPI + MCP server + routes + tools), `data/` (`MarketDataProvider`, `_http.py` resilience client, `adapters/yahoo*.py`, `tradingview_screener.py` landing via Plan 0009), `persistence/` (SQLite + Alembic + repositories incl. `backtest_runs`), `strategies/` (contracts + `discover()` + RSI reference + five ported: bollinger, macd, ema_cross, supertrend, donchian), `backtest/` (pure engine + metrics + `signals_to_trades` adapter + `BacktestResult` + `persist`).
- `desktop/` — Electron + React + TS renderer: OHLCV view, Settings page (mcp-secret rotation), annotation chart-marker polling, SSE subscriber + `show_*` chart handlers, `BacktestView` (equity curve + metrics + trade log) + `RecentBacktestsView`. One-command dev loop via `pnpm dev:all`.
- `.claude/skills/` — `architect`, `dev`, `ui-builder`, `strategy-author`, `backtester`, `market-analyst`, `defi-analyst`, `skill-creator`.

**Workflow rule (learned from a 2026-05-17 incident):** Before writing any ADR, plan, or diagram, `Glob` the three `docs/architecture/` subdirectories to inventory what exists. Trust the filesystem, not this section — if the two disagree, the section is stale and should be refreshed as a follow-up. (See also the `feedback_glob_before_draft` memory.)

## Glossary

- **Phase** — a small, end-to-end change that delivers user value. Plans break work into ordered phases; the whole plan ships as one batch (no architect review between phases).
- **Sibling skill** — one of `dev`, `strategy-author`, `backtester`, `ui-builder`, `market-analyst`, `defi-analyst`, `skill-creator` — peers to this architect skill in the same project.
- **Vendor** — copy code from another repo into ours, take ownership of it. Opposite of "depend on". (Project policy is no-vendor per ADR-0009.)
- **Sidecar** — the Python process that mounts FastAPI + MCP and owns the data layer. Standalone per ADR-0016; auto-attached by Electron via the lockfile.
- **Live viewer** — the Electron renderer, post-ADR-0015. Subscribes to the sidecar's SSE event stream and renders agent-issued chart commands. Not a primary control surface.
- **Primary control surface** — Claude Code (CLI) via MCP, post-ADR-0015. The user's primary input device.
