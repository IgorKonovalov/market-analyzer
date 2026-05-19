# Roadmap — market-analyser

> **Status:** living document, aspirational. **This is not a plan.** Plans (under [`plans/`](plans/)) commit; this document describes direction. ADRs (under [`adrs/`](adrs/)) decide; this document anticipates what decisions are coming. Specific timelines, orderings, and capability cuts will move; the *shape* is the durable part.
>
> Last refreshed: 2026-05-19.

## Vision

`market-analyser` becomes an **agent-first MCP application**. The Python sidecar is the engine; the Electron desktop UI is the cockpit; and the MCP server ([ADR-0014](adrs/0014-mcp-as-second-sidecar-protocol.md)) is the primary surface through which intelligence enters the system. External agents — Claude Desktop today, anything MCP-conformant tomorrow — read cached market data, write analyses, propose strategies, run backtests, and surface news and sentiment, all through tools exposed by the sidecar. The desktop UI renders everything those agents produce alongside the user's manual exploration, and the same agents are first-class citizens of the workflow regardless of whether the user is in trader-mode (intraday, fast feedback) or investor-mode (multi-month horizon).

The end state we're aiming at is a **best-in-class single-user research and decision-support tool**: charts, screeners, backtests, news, sentiment, and volatility forecasts, with agents doing the heavy investigative work and the human keeping decisional authority. The non-negotiables from [`CLAUDE.md`](../../CLAUDE.md) — determinism, no lookahead bias, no secrets in logs, conditions-are-facts-decisions-are-the-user's — hold across every tier below.

## Tiers

Each tier is a coherent capability bundle. Tiers are **not** strict serialization — work inside a tier can parallelize and adjacent tiers can interleave when dependencies allow — but the broad ordering reflects what unblocks what. Specific plans inside each tier get authored when the tier surfaces; do not treat the bullet lists below as plan commitments.

### Tier 0 — Foundation (largely complete)

The walking skeleton, the data-layer rewrite, the dependency discipline, and the MCP foundation. After this tier closes, the platform is ready for analyst-grade work to land on top.

- [Plan 0001](plans/done/0001-bootstrap.md) — Electron + sidecar + SQLite + OHLCV chart for one symbol. **Done.**
- [Plan 0003](plans/done/0003-excise-vendored-upstream.md) — in-house data layer per [ADR-0009](adrs/0009-rewrite-data-layer-in-house.md). **Done.**
- [Plan 0004](plans/done/0004-bootstrap-review-followups.md) — bootstrap review followups. **Done.**
- [Plan 0005](plans/0005-dependency-cooldown.md) — 14-day cooldown + exact pins. **Drafted.**
- [Plan 0006](plans/0006-annotations-via-mcp.md) — MCP server mount + annotations table + chart markers (the "C" tier of the agent-writes ladder). **Drafted.**

### Tier 1 — Analyst surface

Make the desktop app actually useful for analysis, with deterministic technical primitives the agents can call into. After this tier, the agent can run a strategy, see its output, and the user sees the results visualized.

- **Indicators module** (`src/market_analyser/analysis/indicators.py`) — RSI, MACD, EMA, Bollinger, ATR. Deterministic transforms of cached bars; consumed by the indicator-overlay chart panel and by the agent via a future `compute_indicator` MCP tool.
- **Pattern detection module** — Japanese candlestick patterns (doji, hammer, engulfing, etc.) consumed by the `market-analyst` skill and by a future `scan_patterns` MCP tool.
- **Backtest engine** ([ADR-0004](adrs/0004-strategy-interface.md), no plan yet) — the missing half of the strategy contract. Owns equity-curve, Sharpe, drawdown, trade-log shapes.
- **Plan B from Plan 0006's C → B → A ladder** — strategy result rows persisted via MCP (`write_backtest_result`, `list_backtest_results`). Pairs with the backtest engine landing.
- **Reference strategies** ([Plan 0002](plans/0002-strategy-interface.md), drafted) — RSI, MACD-cross, and a small catalog the agent can `run_strategy` against.

### Tier 2 — Data breadth

Today only Yahoo OHLCV is wired. Tier 2 widens the data layer to the sources the vision needs.

- **TradingView screener adapter** — `src/market_analyser/data/adapters/tradingview_screener.py`, exposed as `MarketDataProvider.screener_query`. Lets agents and users find candidates ("BTC pairs with RSI < 30 on the 4h").
- **Sentiment adapter** — Reddit + RSS feeds parsed into a sentiment time series per symbol. New `MarketDataProvider.sentiment_for` method.
- **News adapter** — article ingestion + per-symbol tagging + dedup. New `MarketDataProvider.news_for`.
- **DeFi data layer** — Aave, Uniswap, Aerodrome, Compound adapters. Consumed by the `defi-analyst` skill. Schema for positions (under `positions/`, gitignored) defined here.
- **BTC-specific market data** — funding rates, open interest, miner outflows. Single adapter, BTC-only because the data shapes differ from generic equity OHLCV.
- **Reference data** — sectors, indices, coin metadata. Static lookups loaded into SQLite via Alembic seed.

Crosses with at least three new ADRs:

- **Secrets schema and rotation** (open in the architect's project-context backlog) — third-party feeds need keys; today's per-launch bearer model handles nothing persisted besides `mcp-secret.json`. Sentiment-grade or news-grade paid sources change this.
- **Offline mode** (open in the backlog) — what does the app *do* without network? Currently undefined.
- **Migration safety policy** (open in the backlog) — first non-additive migration will trip this if no rule is in place.

### Tier 3 — Predictive surface

Where the agent-first vision goes from "useful analyst" to "useful forecaster." Volatility prediction, regime detection, sentiment-price coupling. This is the tier where the determinism non-negotiable bites hardest, because model outputs are inherently probabilistic.

- **Volatility forecasting** — model + features pipeline. Probably starts with a deterministic baseline (GARCH or a simple realized-vol forecaster) before any ML model, so that the determinism contract survives. Persisted predictions are first-class rows the chart can render.
- **Regime detection** — bull / bear / ranging / volatile classifier. Same determinism rule: deterministic features first, models layered on top with explicit versioning.
- **Sentiment → price coupling** — cross-correlation studies between Tier 2's sentiment series and OHLCV. Agent-driven exploration that lands as either annotations (Tier 0/Plan 0006) or full result rows (Tier 1/Plan B).
- **Plan A from Plan 0006's ladder — agent-written strategy code.** The agent produces a Python module conforming to [ADR-0004](adrs/0004-strategy-interface.md); the sidecar loads it and runs it. This is the highest-risk capability on the roadmap because it executes agent-written code. Requires its own ADR(s) on sandboxing, code review, and module-loading discipline before any line of plan gets drafted.

Crosses with at least three new ADRs:

- **Model versioning and determinism** — when a volatility model is retrained, what's the reproducibility story? Hash of weights + training-window cutoff is one possibility; capturing this is required before the first persisted model output ships.
- **Agent code execution sandbox** — likely Docker, restricted Python subset, or Pyodide. Big design.
- **LLM provenance metadata** — agent-produced annotations and forecasts need a "produced by `<model>@<version>` on `<timestamp>` via tools `<list>`" trail to be auditable. Schema decided once, applies forward.

### Tier 4 — Investor surface

Long-horizon counterpart to Tier 1's trader surface. Different time scales, different data shapes, different UX.

- **Fundamentals data** — earnings, balance-sheet, ratios. Different source class from price data; likely a paid adapter with its own key handling (depends on Tier 2's secrets-schema ADR).
- **Portfolio tracking** — what the user owns and at what cost basis. Read-only inputs from a positions file at first; later, optional broker integrations (much later — out of any committed scope).
- **Multi-year backtests** — backtest engine extended with a memory-efficient mode for long histories.
- **Macro indicators** — yield curves, CPI, M2, etc. Adapter + cache. Visible in a "macro context" panel adjacent to the chart.

### Tier 5 — News and market investigation

The "be a news source and market investigation app" piece of the vision, layered on top of Tier 2's news adapter.

- **Event detection** — earnings dates, FOMC, crypto unlock schedules, listing announcements. Calendar-shaped data that drives notifications.
- **Multi-source corroboration** — when multiple feeds report the same story, dedup and rank by source quality. Agent task surface: "investigate this rumour."
- **Timeline view per symbol** — chronologically ordered news + sentiment + price moves for a chosen symbol over a chosen window. Designed for the "what actually happened on this day" workflow.
- **Agent-curated digest** — daily/weekly summary the agent produces, written into a `digests` table and surfaced as a left-panel feed.
- **Investigation agent** — an MCP client (could be Claude Desktop with a curated prompt, or a project-local agent loop) that takes a symbol, runs the analyst tools, queries news + sentiment, and writes a structured investigation report into the DB.

### Tier 6 — Product polish

Everything that makes Tier 0–5 actually pleasant to live with. Deferred until the layers below it are stable, because polish on a moving substrate is wasted work.

- **Multi-watchlist** — saved sets of symbols, with the screener results pinnable.
- **Notifications** — native desktop notifications when the agent surfaces a flagged event. Transport ADR required.
- **Auto-update** — packaging plan, currently deferred ([README.md](../../README.md#roadmap)).
- **Themes / accessibility** — light/dark, font scaling, screen-reader audit.
- **Standalone sidecar mode** — agent workflows that run when the app is closed (overnight ingestion, scheduled scans). [ADR-0014](adrs/0014-mcp-as-second-sidecar-protocol.md) explicitly defers this; Tier 5's "agent-curated digest" likely surfaces the need.

## Cross-cutting decisions ahead

Each of the following will need an ADR before its first dependent capability ships. Listed roughly in expected order of need:

| ADR-needed                                         | Triggered by                          | Latest tier still safe to defer |
|-----------------------------------------------------|---------------------------------------|---------------------------------|
| Migration safety policy (first non-additive migration) | Any tier with schema change           | Tier 1                          |
| Secrets schema and rotation                         | First paid third-party feed           | Tier 2                          |
| Offline-mode policy                                 | First user without reliable network   | Tier 2                          |
| Model versioning and determinism                    | First persisted model output          | Tier 3                          |
| LLM provenance metadata                             | First agent-produced fact in the DB   | Tier 1 (Plan 0006 phase 2 is already close to this — `agent_id` is a placeholder) |
| Agent code execution sandbox                        | First agent-written strategy module   | Tier 3                          |
| Standalone sidecar mode                             | First off-app agent workflow          | Tier 5                          |
| Notification transport                              | First user-facing alert               | Tier 6                          |
| DeFi positions encryption-at-rest                   | First on-chain position imported      | Tier 2 (DeFi sub-bullet)        |

The `Triggered by` column is the architect's signal that drafting the ADR can't wait any longer; the `Latest tier still safe to defer` column is the latest *plan-shaped* commitment that can ship without the ADR existing.

## Risks at the long-horizon level

- **Scope creep.** "Best in class" is unbounded by definition. The tier structure exists so that each tier has a clear cut criterion; we should resist work that doesn't fit cleanly into an existing tier or doesn't justify a new one. If a capability doesn't compress neatly, the right move is to write the ADR / plan that names what it actually is, not to expand a tier's surface silently.
- **Determinism vs LLM nondeterminism.** Agent-produced artifacts (annotations, forecasts, digests, written strategies) carry an inherent reproducibility cost that backtests do not tolerate. The architectural answer is layering: deterministic primitives in `src/market_analyser/`, nondeterministic agent outputs in tables tagged with model + version + timestamp, and the backtest engine permitted to consume only the former. The LLM-provenance ADR makes this layering enforceable; without it, the boundary will drift.
- **Trust boundaries widening per tier.** Tier 0–1 is a fully-local single-user app. Tier 2 introduces third-party feeds (data egress). Tier 3 introduces agent-written code execution (largest blast radius on this list). Tier 5 introduces external information ingestion at scale. Each tier needs a security review against an updated threat model; defaults from [ADR-0008](adrs/0008-electron-shell-conventions.md) (Electron) and [ADR-0011](adrs/0011-bearer-secret-transport.md) (bearer) are necessary, not sufficient.
- **Mental-model collisions.** The trader workflow, the investor workflow, the analyst workflow, and the "agent runs the investigation" workflow have different cadences and different decision surfaces. The risk is a generic UI that serves none of them well. The mitigation is to design each surface as a distinct view (the desktop app already supports this — Settings, OhlcvView, future views compose) and accept that the same data flows through differently shaped lenses, rather than chasing a unified one.
- **Vendor / model dependency.** Tier 3's predictive surface and Tier 5's investigation surface lean heavily on whatever LLM the user's MCP client speaks to (Claude today). Lock-in to a specific model family is acceptable for a first release but a deliberate plan to keep MCP tool descriptions provider-agnostic is the seam that protects against it. No commitment to multi-provider support is implied by this note — only the design discipline.

## What this roadmap is NOT

- **A commitment.** Tier ordering, capability inclusion, and emphasis will change as plans get drafted, ADRs land, and the actual product surfaces. The vision is the durable part; the bullet lists are not.
- **A timeline.** No quarter / year / release-train estimates. Plans get drafted when their dependencies clear, not on a calendar.
- **A spec.** Every tier expands into one or more plans authored at the time, each with its own interview, options, and trade-offs. Pulling implementation detail out of the bullets above is premature.
- **Comprehensive.** Capabilities not listed are not ruled out; they are simply not currently named. If the user (or a future maintainer) wants to add a capability, the right move is to either write a Mode-1 plan inside the relevant tier or to propose a tier amendment in the same kind of architect session that produced this document.

## How this document maintains itself

- **Refresh trigger:** every plan close-ceremony where the closed plan completed a tier item, the architect updates this file as part of the same session. (Same discipline as the plans README.)
- **Source of truth:** plan and ADR files in `plans/` and `adrs/`. If this roadmap disagrees with a plan's `Status:` line or an ADR's body, the plan/ADR wins and this file gets fixed.
- **Living-document framing:** the `Last refreshed` line at the top is the trust signal. If it's more than a few weeks behind the most recent close ceremony, the document has drifted and should be re-read with skepticism.
