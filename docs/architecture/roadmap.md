# Roadmap — market-analyser

> **Status:** living document, aspirational. **This is not a plan.** Plans (under [`plans/`](plans/)) commit; this document describes direction. ADRs (under [`adrs/`](adrs/)) decide; this document anticipates what decisions are coming. Specific timelines, orderings, and capability cuts will move; the *shape* is the durable part.
>
> Last refreshed: 2026-07-21 (app at **v0.23.0**; refreshed the top-of-document trust signals — the [At a glance](#at-a-glance--shipped-vs-planned) ledger and the [In flight / approved](#in-flight--approved-the-open-plans) table, which had drifted badly: the entire 0097–0110 close batch (chart chain completion + drawing tools, the watchlist-scanner / quality-rank / sector-rotation upstream-parity batch, DeFi fundamentals + position risk, the Reddit/X sentiment sources, the MCP tool-surface consolidation, and the watch-management UX) plus the 0111/0112 additions had shipped or been drafted since the last refresh. Also threaded in the new Tier-5 event-calendar direction (Plan 0113) and the docs living-spec layer (Plan 0112). **Honesty note: the tier bodies below still narrate the pre-0096 close batch** — several inline `approved`/`in flight` tags in the Tier sections name plans that have since closed. The [At a glance](#at-a-glance--shipped-vs-planned) ledger and the plan files are the current truth; the tier prose is the durable *why*, not a live status board, and a full tier-body catch-up is owed as its own pass. Prior refresh 2026-07-14 (v0.9.0, through the 0072–0096 batch). Prior substantive refresh 2026-07-03, post Plan 0060 close.

## At a glance — shipped vs. planned

A one-screen ledger of where the plan program stands. The tiers below narrate *why*; this is the *what*. Source of truth is each plan's `Status:` line and the [plans index](plans/README.md); if this disagrees, the plan file wins.

**As of 2026-07-21 (v0.23.0): ~106 plans shipped (through the Plan 0042 close, in [`plans/done/`](plans/done)), 7 in flight or approved.** The two read-only pillars of the 2026-06-05 program — forecasting and the advisory layer — are live; the crypto-intelligence and DeFi-analytics programs shipped in full; the portfolio arc's data + risk halves shipped (0041/0042) with its UI (0043) in flight; the **execution arc (0044–0046) remains designed but unbuilt** and is the most-gated work on the critical path. The read-only decision-support surface is essentially saturated, so new direction now goes to **Tier 5 (news & market investigation)** — the event-calendar first slice is committed (Plan 0113).

### Shipped (thematic digest of the 96 closed plans)

| Theme | What landed | Representative plans |
|-------|-------------|----------------------|
| **Foundation & platform** | Electron + Python sidecar + SQLite; in-house data layer; MCP mount; standalone sidecar + SSE live viewer; one-command dev loop; golden-path smoke | 0001, 0003–0007, 0015–0017 |
| **Analyst surface** | Indicators + candlestick & classical chart patterns; support/resistance & Fibonacci/pivot levels; volume analysis; multi-timeframe; Ichimoku; live-signal eval; momentum/divergence/money-flow; market structure | 0018, 0021, 0026, 0027, 0051, 0052, 0073, 0075, 0091, 0092 |
| **Backtest engine** | Pure `run` + equity/trade-log; extended metrics + walk-forward; long **and short** | 0002, 0008, 0020, 0053 |
| **Data breadth** | TradingView screener + resilient HTTP; RSS news + VADER; StockTwits; Fear & Greed; symbol search; Coinbase as 3rd OHLCV source | 0009–0012, 0019, 0024, 0081 |
| **Crypto intelligence** | Cycle/macro series spine; Binance derivatives (funding, OI); on-chain valuation (MVRV); Binance klines; self-warming metric store | 0055–0059, 0061 |
| **Forecasting** | Foundation (direction-as-probability, walk-forward-gated) + UI; exogenous feature-set v2; tiered feature sets; explainability; pivot to non-directional (volatility + regime) | 0036, 0037, 0059, 0062, 0063, 0077 |
| **Advisory** | The `advisor` layer + UI (labeled, basis-carrying recommendations); technical-read tier; tiered-forecast unification; recommendation track record | 0038, 0039, 0066, 0074, 0080 |
| **DeFi** | Wallet discovery; deep LP detail; tx-replay P&L + completeness arc (gauge/swap/dust/wallet-total/windowed); executable-quote CL pricing; cross-pool discrepancy scanner | 0032, 0034, 0035, 0084, 0086–0088, 0079 |
| **Prediction markets** | Keyless Polymarket odds; convergence screener; on-chart market links | 0040, 0078, 0089 |
| **Chart & UX** | Interactive chart + decomposition; lazy history; theming & chart-style settings; trendline/pattern span rendering; glossary hover; legend declutter; lightweight-charts v4→v5 migration; chart & app declutter | 0014, 0029, 0033, 0064, 0068, 0071, 0095, 0096 |
| **Alerting** | Watchlist alerting loop — in-sidecar scheduler, edge-triggered `alert.triggered`, Alerts view + toast | 0060 |
| **Platform hardening** | Russian localization + reason codes; generated CI-gated API reference; 2026-07 remediation audit; versioning cadence | 0069, 0070, 0072, 0087 |

### In flight / approved (the open plans)

The full authoritative roster (with per-plan close notes) is the [plans index](plans/README.md); this is the one-screen view.

| # | Plan | Status | One-liner |
|---|------|--------|-----------|
| 0113 | [event-calendar-source](plans/0113-event-calendar-source.md) | approved | **Tier-5 first slice.** Composed keyless-first event calendar — FOMC/FRED macro + Finnhub earnings (free-key-inert) + Binance/Coinbase listings self-diff — behind one `event_calendar(category)` tool; token unlocks deferred ([ADR-0107](adrs/0107-event-calendar-composed-source.md)). |
| 0112 | [living-behavioral-specs](plans/0112-living-behavioral-specs.md) | approved | Docs living-spec layer under `docs/architecture/specs/` — per-subsystem behavioral contracts reconciled at close ([ADR-0106](adrs/0106-spec-system-posture-and-living-specs.md); the OpenSpec-evaluation outcome). |
| 0111 | [reddit-oauth-access-path](plans/0111-reddit-oauth-access-path.md) | approved (**paused**) | Keyed Reddit OAuth after keyless proved IP-blocked ([ADR-0105](adrs/0105-reddit-keyed-oauth-access-path.md)). **Human gate blocked** — can't obtain app credentials. |
| 0046 | [pending-order-confirm-ux](plans/0046-pending-order-confirm-ux.md) | approved | **Execution arc.** Assisted-confirm pending-order queue + kill switch; no order without confirmation ([ADR-0025](adrs/0025-trade-execution-feasibility.md) invariant 1). |
| 0045 | [binance-futures-testnet-adapter](plans/0045-binance-futures-testnet-adapter.md) | approved | **Execution arc.** Binance USDⓈ-M **testnet** adapter. |
| 0044 | [execution-skeleton](plans/0044-execution-skeleton.md) | approved | **Execution arc core.** `ExecutionVenue` Protocol + order/position FSM + idempotency + reconciliation + risk guard + kill switch ([ADR-0043](adrs/0043-execution-venue-protocol.md)). Adds a migration. |
| 0043 | [portfolio-ui-surface](plans/0043-portfolio-ui-surface.md) | approved (**in progress**) | Portfolio view + DeFi risk panel over the cross-venue aggregator ([ADR-0042](adrs/0042-cross-venue-portfolio-aggregation.md)). Being implemented now. |

The **execution arc (0044 → 0045 → 0046)** is the most-gated remaining committed work and sits last on the critical path (testnet-first, assisted-confirm — ADR-0025). Everything read-only through Tier 4 has closed; new direction goes to **Tier 5** (Plan 0113) and platform (Plan 0112).

## Vision

`market-analyser` becomes an **agent-first MCP application**. The Python sidecar is the engine; the Electron desktop UI is the cockpit; and the MCP server ([ADR-0014](adrs/0014-mcp-as-second-sidecar-protocol.md)) is the primary surface through which intelligence enters the system. External agents — Claude Desktop today, anything MCP-conformant tomorrow — read cached market data, write analyses, propose strategies, run backtests, and surface news and sentiment, all through tools exposed by the sidecar. The desktop UI renders everything those agents produce alongside the user's manual exploration, and the same agents are first-class citizens of the workflow regardless of whether the user is in trader-mode (intraday, fast feedback) or investor-mode (multi-month horizon).

The end state we're aiming at is a **best-in-class single-user research and decision-support tool**: charts, screeners, backtests, news, sentiment, and volatility forecasts, with agents doing the heavy investigative work and the human keeping decisional authority. The non-negotiables from [`CLAUDE.md`](../../CLAUDE.md) — determinism, no lookahead bias, no secrets in logs, conditions-are-facts-decisions-are-the-user's — hold across every tier below.

**The tool is crypto-first ([ADR-0069](adrs/0069-crypto-first-asset-class-positioning.md)).** Crypto — spot, derivatives (funding, open interest), on-chain valuation (MVRV, dominance, cycle metrics), and DeFi — is the primary asset class: it gets first-class data breadth, the richest forecasting feature sets, and the portfolio/execution arc. This is where the project's differentiated data and its only demonstrated forecast edge live (the exogenous features that give the forecaster any edge are crypto-only by nature). **TradFi (equities, indices, futures) is supported but secondary**: the asset-class-neutral technical surface — OHLCV, candlestick + classical patterns, indicators, the screener, news, generic sentiment, backtesting — works on TradFi symbols and stays maintained, but TradFi gets **no** dedicated fundamentals or macro-fundamentals data layer, and the forecasting/advisory surfaces make **no** claim of a comparable predictive edge on a TradFi symbol. Any future TradFi-fundamentals work is a separately-justified plan that amends the positioning, not an assumed commitment below.

**2026-06-05 — the vision deliberately expands from decision-*support* toward decision-*execution*, in contained layers.** A user-approved program (the [trade/predict/portfolio program](plans/README.md#trade--predict--portfolio-program-provisional-approved-in-shape-2026-06-05); Plans 0036–0046, ADRs 0040–0044) adds, in priority order: **price forecasting** ([ADR-0030](adrs/0030-forecasting-subsystem.md)/[ADR-0040](adrs/0040-forecasting-model-artifacts.md), Tier 3), an **advisory layer** that synthesises conditions/signals/backtests/forecasts into a *labeled, basis-carrying recommendation* ([ADR-0029](adrs/0029-advisory-recommendation-boundary.md)), **cross-venue portfolio management + position risk** ([ADR-0042](adrs/0042-cross-venue-portfolio-aggregation.md)/[ADR-0037](adrs/0037-defi-position-risk-forecast.md), Tier 4), **Polymarket prediction-market odds** as a read signal ([ADR-0041](adrs/0041-polymarket-odds-read-source.md), Tier 2), and **assisted, testnet-first trade execution** on Binance USDⓈ-M Futures ([ADR-0025](adrs/0025-trade-execution-feasibility.md)/[ADR-0043](adrs/0043-execution-venue-protocol.md)/[ADR-0044](adrs/0044-trade-secret-store.md)). This crosses two lines the original read-only vision drew — *recommend* and *act* — but **contains each crossing to one new, gated layer**: the read-only analyst skills (`market-analyst`/`defi-analyst`) keep their "conditions are facts" contract unchanged; recommendation lives only in a new **`advisor`** skill, and execution only in a new **`trader`** skill (the program's *only* two new skills). The human keeps decisional authority via the **assisted-confirm step** — every order is prepared+sized by the agent but submitted only on explicit human confirmation — and execution stays **testnet/paper-only** until the full intent→submit→fill→reconcile→close loop is proven; real funds remain a separate, deliberate future decision.

## Tiers

Each tier is a coherent capability bundle. Tiers are **not** strict serialization — work inside a tier can parallelize and adjacent tiers can interleave when dependencies allow — but the broad ordering reflects what unblocks what. Specific plans inside each tier get authored when the tier surfaces; do not treat the bullet lists below as plan commitments.

### Tier 0 — Foundation (complete)

The walking skeleton, the data-layer rewrite, the dependency discipline, the MCP foundation, and the agent-first role inversion (Claude Code primary, Electron a live viewer). This tier has closed; analyst-grade work now lands on top.

- [Plan 0001](plans/done/0001-bootstrap.md) — Electron + sidecar + SQLite + OHLCV chart for one symbol. **Done.**
- [Plan 0003](plans/done/0003-excise-vendored-upstream.md) — in-house data layer per [ADR-0009](adrs/0009-rewrite-data-layer-in-house.md). **Done.**
- [Plan 0004](plans/done/0004-bootstrap-review-followups.md) — bootstrap review followups. **Done.**
- [Plan 0005](plans/done/0005-dependency-cooldown.md) — 14-day cooldown + exact pins. **Done.**
- [Plan 0006](plans/done/0006-annotations-via-mcp.md) — MCP server mount + annotations table + chart markers (the "C" tier of the agent-writes ladder). **Done.**
- [Plan 0007](plans/done/0007-live-agent-driven-viewer.md) — standalone sidecar (lockfile + idempotent attach, [ADR-0016](adrs/0016-standalone-sidecar-mode.md)) + SSE event stream ([ADR-0017](adrs/0017-live-ui-updates-via-sse.md)) + agent-issued chart renders. The implementation of the [ADR-0015](adrs/0015-claude-code-primary-control-surface.md) role inversion. **Done.**
- [Plan 0015](plans/done/0015-pnpm-dev-all.md) — one-command dev loop (`pnpm dev:all`). **Done.**

### Tier 1 — Analyst surface

Make the desktop app actually useful for analysis, with deterministic technical primitives the agents can call into. After this tier, the agent can run a strategy, see its output, and the user sees the results visualized.

- **Indicators + pattern surface** (`src/market_analyser/analysis/`) — **Done** ([Plan 0018](plans/done/0018-technical-analysis-surface.md), [ADR-0023](adrs/0023-technical-analysis-surface.md)): 9 indicators (RSI, MACD, EMA, Bollinger, ATR, …) + 14 candlestick patterns + a `condition_snapshot`, plus the `analyze_symbol` tool. Extended by volume analysis ([Plan 0027](plans/done/0027-volume-bars-and-analysis.md)), multi-timeframe + volume scanners ([Plan 0021](plans/done/0021-multi-timeframe-and-volume-scanners.md)), and the live-quote/macro-context work. This surface doubles as the forecaster's feature library (Tier 3).
- **Backtest engine** ([Plan 0008](plans/done/0008-backtest-engine-v1.md), [ADR-0018](adrs/0018-backtest-result-schema.md)) — pure `run` + metric helpers + thin `persist`; owns the equity-curve, Sharpe, drawdown, and trade-log shapes. **Done.**
- **Strategy result persistence** (Plan B from Plan 0006's C → B → A ladder) — realized by Plan 0008's `backtest_runs` table + `persist()` + the `run_backtest` MCP tool emitting `run.completed v1`, with `BacktestView` / `RecentBacktestsView` in the renderer. **Done.**
- **Reference strategies** ([Plan 0002](plans/done/0002-strategy-interface.md)) — RSI reference + five ported (Bollinger, MACD-cross, EMA-cross, supertrend, donchian) + the `signals_to_trades` adapter + a `strategies list` CLI. **Done.**

### Tier 2 — Data breadth

Today only Yahoo OHLCV is wired. Tier 2 widens the data layer to the sources the vision needs.

- **TradingView screener adapter** ([Plan 0009](plans/done/0009-resilience-and-tradingview-screener.md)) — `screener_query` over `get_screener`, paired with the shared `ResilientHttpClient` ([ADR-0019](adrs/0019-external-http-adapter-resilience.md)) that every external adapter inherits. **Done.**
- **News + sentiment adapter** ([Plan 0010](plans/done/0010-news-and-vader-sentiment.md)) — RSS feeds + per-headline VADER scoring; `get_news` + `get_sentiment`, plus an in-app News view ([Plan 0023](plans/done/0023-news-view-in-app.md)). **Done.** Reddit-based sentiment deliberately deferred.
- **StockTwits sentiment** ([Plan 0012](plans/done/0012-stocktwits-sentiment.md)) — second per-symbol source using explicit Bullish/Bearish label counts; adds a `source` parameter to `get_sentiment`. **Done.**
- **Crypto Fear & Greed** ([Plan 0011](plans/done/0011-fear-and-greed-indices.md)) — market-level sentiment via alternative.me; `get_market_sentiment`. **Done.**
- **DeFi data layer** — **largely realized**: Zerion-based wallet discovery ([Plan 0032](plans/done/0032-defi-wallet-discovery.md), done; [ADR-0034](adrs/0034-defi-portfolio-aggregator.md)/[ADR-0035](adrs/0035-defi-domain-placement.md)) and deep LP detail ([Plan 0034](plans/done/0034-defi-deep-lp-detail.md), done 2026-06-05) both shipped; tx-replay P&L ([Plan 0035](plans/0035-defi-pnl-reconstruction.md)/[ADR-0036](adrs/0036-defi-pnl-reconstruction.md), approved) is the remaining piece. Consumed by the `defi-analyst` skill; positions under `positions/` (gitignored).
- **Polymarket prediction-market odds** ([ADR-0041](adrs/0041-polymarket-odds-read-source.md)/[Plan 0040](plans/0040-polymarket-odds-adapter.md), approved) — read-only market-implied probabilities (auth-free Gamma + CLOB; price = implied probability) as a new signal class for the forecaster/advisor. Trading deferred to the execution arc.
- **BTC-specific market data** — **largely shipped** by the crypto intelligence program (Plans [0055](plans/done/0055-cycle-macro-series-spine.md)–[0058](plans/done/0058-binance-klines-ohlcv.md), closed 2026-06-10→15): funding rates + open interest ([ADR-0052](adrs/0052-binance-exchange-data-source.md)), MVRV on-chain valuation ([ADR-0053](adrs/0053-onchain-valuation-source.md), reshaped MVRV-only — realized-cap/SOPR proved keyless-forbidden), cycle metrics (halving clock, Mayer, 200W), F&G/dominance history, and Binance spot klines as a second OHLCV source — all historized through the [ADR-0051](adrs/0051-historized-metric-series-contract.md) `metric_points` contract. Miner outflows remain unshipped (no free keyless source found).
- **Reference data** — sectors, indices, coin metadata. Static lookups loaded into SQLite via Alembic seed.

Crosses with at least three new ADRs:

- **Secrets schema and rotation** — **resolved**: read-only third-party keys land in a `0600` secrets file ([ADR-0038](adrs/0038-third-party-api-key-storage.md)); the higher-value *trade* keys go to the OS keychain ([ADR-0044](adrs/0044-trade-secret-store.md), execution arc). Two distinct value classes, two stores — never co-mingled.
- **Offline mode** (open in the backlog) — what does the app *do* without network? Currently undefined.
- **Migration safety policy** (open in the backlog) — first non-additive migration will trip this if no rule is in place.

### Tier 3 — Predictive surface

Where the agent-first vision goes from "useful analyst" to "useful forecaster." Volatility prediction, regime detection, sentiment-price coupling. This is the tier where the determinism non-negotiable bites hardest, because model outputs are inherently probabilistic.

**Foundation shipped (Plan [0036](plans/done/0036-forecasting-subsystem-foundation.md) closed 2026-06-07):** the `forecast/` package + `forecast` MCP tool exist — next-bar direction as a calibrated up/down/flat probability with an honest no-edge verdict, implementing [ADR-0030](adrs/0030-forecasting-subsystem.md) (walk-forward-beats-baseline gate) and [ADR-0040](adrs/0040-forecasting-model-artifacts.md) (sklearn `HistGradientBoosting`, determinism mechanism, model versioning/provenance — this **resolved the "model versioning and determinism" cross-cutting ADR** flagged below). Still in flight: [Plan 0037](plans/0037-forecast-ui-surface.md) (the viewer surface, approved) and [Plan 0059](plans/0059-forecast-feature-set-v2.md) (feature-set v2 — cycle + exogenous series joined lag-1 as-of, multi-horizon `{1, 5, 21}`, [ADR-0054](adrs/0054-exogenous-forecast-features-multi-horizon.md); approved and fully unblocked). Volatility and regime forecasting are later additions behind the same invariants. The feeds-into-the-advisor link is **realized** — the advisory arc (below) shipped.

- **Volatility + regime forecasting — now committed** ([Plan 0077](plans/0077-forecast-pivot-volatility-and-regime.md), paired [ADR-0070](adrs/0070-non-directional-forecast-targets.md)/[ADR-0071](adrs/0071-advisor-non-directional-inputs-and-direction-demotion.md)): the forecasting subsystem pivots from near-random **direction** to two **non-directional** targets where an honest edge is attainable — a **volatility forecast** (regression vs a deterministic EWMA/persistence baseline; drives sizing/stops) and a **regime-transition forecast** (trailing rule-based classification + a persistence-baselined next-period classifier). Both reuse the walk-forward/determinism/explainability harness (no new dependency — GARCH/HMM deferred), are validated cross-asset (BTC + ETH), and feed the advisor as **non-voting** conviction/sizing/stop inputs. The existing direction forecaster stays but is **demoted** (surfaced only where it beats baseline; non-gating below a skill-margin threshold). This is the roadmap's answer to the empirical finding that direction-of-return is near-random (v1 no-edge everywhere; v2-deep only h=21, fold-fragile).
- **Sentiment → price coupling** — cross-correlation studies between Tier 2's sentiment series and OHLCV. Agent-driven exploration that lands as either annotations (Tier 0/Plan 0006) or full result rows (Tier 1/Plan B).
- **Plan A from Plan 0006's ladder — agent-written strategy code.** The agent produces a Python module conforming to [ADR-0004](adrs/0004-strategy-interface.md); the sidecar loads it and runs it. This is the highest-risk capability on the roadmap because it executes agent-written code. Requires its own ADR(s) on sandboxing, code review, and module-loading discipline before any line of plan gets drafted.

Crosses with at least three new ADRs:

- **Model versioning and determinism** — when a volatility model is retrained, what's the reproducibility story? Hash of weights + training-window cutoff is one possibility; capturing this is required before the first persisted model output ships.
- **Agent code execution sandbox** — likely Docker, restricted Python subset, or Pyodide. Big design.
- **LLM provenance metadata** — agent-produced annotations and forecasts need a "produced by `<model>@<version>` on `<timestamp>` via tools `<list>`" trail to be auditable. Schema decided once, applies forward.

### Tier 4 — Investor surface

Long-horizon counterpart to Tier 1's trader surface. Different time scales, different data shapes, different UX.

- **Fundamentals data** — earnings, balance-sheet, ratios. **Explicitly deferred, not a pending commitment** ([ADR-0069](adrs/0069-crypto-first-asset-class-positioning.md), crypto-first): a TradFi fundamentals layer would reverse the secondary-TradFi positioning and so requires a plan that argues for it, not an assumed Tier 4 item. Different source class from price data; likely a paid adapter with its own key handling (depends on Tier 2's secrets-schema ADR).
- **Cross-venue portfolio tracking** — **now committed** ([ADR-0042](adrs/0042-cross-venue-portfolio-aggregation.md), [Plan 0041](plans/0041-cross-venue-portfolio-aggregation.md)): unified holdings + **average-cost basis** + P&L + exposure across **Binance (read API) + DeFi + a manual positions file**, read-only and **tools-only** (no operator skill — the TradFi/DeFi skill split is preserved, not relaxed). Paired with **DeFi position risk/forecast** ([ADR-0037](adrs/0037-defi-position-risk-forecast.md), [Plan 0042](plans/0042-defi-position-risk-forecast.md)) — scenario sensitivity + conditional liquidation/IL probability framed as conditional facts, not a market view — and a portfolio UI ([Plan 0043](plans/0043-portfolio-ui-surface.md)). A live broker API for equities remains the deferred, heavier option.
- **Multi-year backtests** — backtest engine extended with a memory-efficient mode for long histories.
- **Macro indicators** — yield curves, CPI, M2, etc. Adapter + cache. Visible in a "macro context" panel adjacent to the chart.

### The advisory + execution arc (committed 2026-06-05)

The deliberate departure from the original read-only vision — and the most gated work on the roadmap. It does not slot into a single tier (it spans forecasting in Tier 3, portfolio in Tier 4, and a wholly new execution domain), so it is named here as its own arc. Two crossings, each contained to **one new skill**:

- **Advisory layer — SHIPPED 2026-07-02** ([ADR-0029](adrs/0029-advisory-recommendation-boundary.md) accepted; [Plan 0038](plans/done/0038-advisor-layer.md) + UI [Plan 0039](plans/done/0039-advisor-ui-surface.md) both closed; the **`advisor`** skill created) — fuses conditions ([ADR-0023](adrs/0023-technical-analysis-surface.md)), live signals ([Plan 0026](plans/done/0026-live-signal-evaluator.md)), backtested edge ([ADR-0024](adrs/0024-extended-backtest-metrics.md)), and forecasts ([ADR-0030](adrs/0030-forecasting-subsystem.md)) into a **labeled trade recommendation** carrying its rationale + backtested/forecasted basis. The crossing of "conditions are facts, decisions are the user's" is *contained*: the analyst skills are untouched, a basis-free recommendation is a validation error, and the advisor holds no key and places no order. It is the synthesis the user acts on manually.
- **Assisted, testnet-first execution** ([ADR-0025](adrs/0025-trade-execution-feasibility.md) + [ADR-0043](adrs/0043-execution-venue-protocol.md)/[ADR-0044](adrs/0044-trade-secret-store.md); Plans [0044](plans/0044-execution-skeleton.md)/[0045](plans/0045-binance-futures-testnet-adapter.md)/[0046](plans/0046-pending-order-confirm-ux.md); new **`trader`** skill) — a venue-independent `ExecutionVenue` Protocol + persisted order/position state machine + idempotency + reconciliation + risk guard + kill switch, with a Binance USDⓈ-M Futures **testnet** adapter (official SDK) and an **assisted-confirm** queue: the agent prepares and sizes, the human confirms, only then does it submit. Trade keys live in the OS keychain ([ADR-0044](adrs/0044-trade-secret-store.md)). Real funds, autonomous mode, and a second venue (a DeFi perp; Polymarket trading via `py-sdk`) are each a separate, later decision.

This arc realizes [ADR-0025](adrs/0025-trade-execution-feasibility.md)'s six invariants (assisted-first, testnet-first, isolated domain, segregated secrets, idempotency+reconciliation, guard+kill-switch). On the program's critical path it sequences **after** forecasting and the advisor. These two skills (`advisor`, `trader`) are the **only** skills the whole program adds; everything else extends existing surfaces or ships as agent-callable tools.

### Tier 5 — News and market investigation

The "be a news source and market investigation app" piece of the vision, layered on top of Tier 2's news adapter. **This tier is now active** — with the read-only decision-support surface saturated, it holds the next new direction (2026-07-21).

- **Event detection — first slice committed** ([Plan 0113](plans/0113-event-calendar-source.md)/[ADR-0107](adrs/0107-event-calendar-composed-source.md), approved): a composed keyless-first `EventCalendarSource` behind one `event_calendar(category)` tool — FOMC + FRED release dates (macro), Finnhub earnings (free-key-inert), and Binance/Coinbase listings self-diff. **Token unlocks deferred** (no keyless JSON — DefiLlama emissions confirmed paid-only; spend paused). Calendar-shaped data that will drive notifications via the existing dwell scheduler + OS-notification path (follow-on). Timeline, corroboration, and digest below remain unbuilt.
- **Multi-source corroboration** — when multiple feeds report the same story, dedup and rank by source quality. Agent task surface: "investigate this rumour."
- **Timeline view per symbol** — chronologically ordered news + sentiment + price moves for a chosen symbol over a chosen window. Designed for the "what actually happened on this day" workflow.
- **Agent-curated digest** — daily/weekly summary the agent produces, written into a `digests` table and surfaced as a left-panel feed.
- **Investigation agent** — an MCP client (could be Claude Desktop with a curated prompt, or a project-local agent loop) that takes a symbol, runs the analyst tools, queries news + sentiment, and writes a structured investigation report into the DB.

### Tier 6 — Product polish

Everything that makes Tier 0–5 actually pleasant to live with. Deferred until the layers below it are stable, because polish on a moving substrate is wasted work.

- **Multi-watchlist** — saved sets of symbols, with the screener results pinnable. (Condition *watches* shipped separately as the alerting loop — [ADR-0055](adrs/0055-in-sidecar-watch-scheduler.md)/[Plan 0060](plans/done/0060-watchlist-alerting-loop.md), closed 2026-07-03: persisted watches, an in-sidecar scheduler, edge-triggered `alert.triggered` events, an Alerts view + toast. Symbol *watchlists* as saved browse sets remain this open item.)
- **Notifications** — native **OS-level** desktop notifications when the agent surfaces a flagged event; transport ADR required. Partially overtaken: in-app alert delivery (SSE toast + Alerts view + agent polling leg) shipped with Plan 0060; ADR-0055 explicitly keeps OS notifications out of v1 scope.
- **Auto-update** — packaging plan, currently deferred ([README.md](../../README.md#roadmap)).
- **Themes / accessibility** — light/dark, font scaling, screen-reader audit.
- **Scheduled / off-app agent workflows** — the standalone sidecar shipped ([ADR-0016](adrs/0016-standalone-sidecar-mode.md), [Plan 0007](plans/done/0007-live-agent-driven-viewer.md)), and the first in-sidecar *scheduler* now exists ([ADR-0055](adrs/0055-in-sidecar-watch-scheduler.md)/[Plan 0060](plans/done/0060-watchlist-alerting-loop.md)): a lifespan asyncio loop evaluating persisted watches per closed bar — the app's first unprompted voice. What remains deferred is *general* scheduled work (overnight ingestion, timed scans, agent-driven digests) and crash-supervision/automated restart — ADR-0016 still leaves those to a future ADR; ADR-0055's scheduler is deliberately watch-scoped, not a general job runner.

## Cross-cutting decisions ahead

Each of the following will need an ADR before its first dependent capability ships. Listed roughly in expected order of need:

| ADR-needed                                          | Triggered by                            | Status (2026-07-03) |
|-----------------------------------------------------|-----------------------------------------|---------------------|
| Migration safety policy (first non-additive migration) | Any tier with schema change          | Open — program migration-adders serialize per the single-Alembic-chain rule, but a formal non-additive policy is still unwritten |
| Secrets schema and rotation                         | First paid third-party feed / trade key | **Resolved** — read keys → `0600` file ([ADR-0038](adrs/0038-third-party-api-key-storage.md)); trade keys → OS keychain ([ADR-0044](adrs/0044-trade-secret-store.md)); two value classes, two stores |
| Offline-mode policy                                 | First user without reliable network     | Open |
| Model versioning and determinism                    | First persisted model output            | **Resolved** — [ADR-0040](adrs/0040-forecasting-model-artifacts.md) (hash-based `model_version` + per-forecast provenance) |
| LLM provenance metadata                             | First agent-produced fact in the DB     | Open — ADR-0040 covers *model-artifact* provenance; an LLM-output provenance schema is still unwritten |
| Agent code execution sandbox                        | First agent-written strategy module     | Open — not triggered (the program forecasts/advises/executes but runs no agent-written strategy code) |
| Sidecar crash-supervision / scheduling              | First *scheduled* off-app workflow      | Partial — the watch-scoped in-sidecar scheduler is decided ([ADR-0055](adrs/0055-in-sidecar-watch-scheduler.md)); general scheduling + crash-supervision still open |
| Notification transport                              | First user-facing alert                 | Partial — in-app delivery shipped (SSE toast + Alerts view, Plan 0060); the OS-native transport ADR is still open |
| DeFi positions encryption-at-rest                   | First on-chain position imported        | Partial — read keys via [ADR-0038](adrs/0038-third-party-api-key-storage.md); a Polygon hot-wallet signing key would use [ADR-0044](adrs/0044-trade-secret-store.md) |
| **Forecasting subsystem**                           | First forward prediction                | **Decided** — [ADR-0030](adrs/0030-forecasting-subsystem.md) (causal, validated, direction-as-probability) |
| **Advisory recommendation boundary**                | First actionable recommendation         | **Decided** — [ADR-0029](adrs/0029-advisory-recommendation-boundary.md) (contained `advisor` layer) |
| **Execution venue protocol + order state machine**  | First order layer                       | **Decided** — [ADR-0043](adrs/0043-execution-venue-protocol.md) (Protocol + persisted FSM + idempotency + reconciliation), under [ADR-0025](adrs/0025-trade-execution-feasibility.md)'s six invariants |

The `Triggered by` column is the architect's signal that drafting the ADR can't wait any longer; the `Status` column tracks which have landed — most of the 2026-06-05 program's load-bearing ADRs are now decided, with offline-mode, LLM-output provenance, the agent-code sandbox, scheduling, and notifications still genuinely open.

## Risks at the long-horizon level

- **Scope creep.** "Best in class" is unbounded by definition. The tier structure exists so that each tier has a clear cut criterion; we should resist work that doesn't fit cleanly into an existing tier or doesn't justify a new one. If a capability doesn't compress neatly, the right move is to write the ADR / plan that names what it actually is, not to expand a tier's surface silently.
- **Determinism vs LLM nondeterminism.** Agent-produced artifacts (annotations, forecasts, digests, written strategies) carry an inherent reproducibility cost that backtests do not tolerate. The architectural answer is layering: deterministic primitives in `src/market_analyser/`, nondeterministic agent outputs in tables tagged with model + version + timestamp, and the backtest engine permitted to consume only the former. The LLM-provenance ADR makes this layering enforceable; without it, the boundary will drift.
- **Trust boundaries widening per tier.** Tier 0–1 is a fully-local single-user app. Tier 2 introduces third-party feeds (data egress). Tier 3 introduces probabilistic model outputs. The **advisory + execution arc** now introduces the **largest blast radius on this list**: a trade-permissioned key and a path that places live orders — money movement, not just data. The mitigations are structural and gated, not aspirational: execution is **testnet-first** (no real funds until the loop is proven), **assisted-first** (no order without explicit human confirmation), the trade key lives in the **OS keychain with an honest threat model** ([ADR-0044](adrs/0044-trade-secret-store.md): DPAPI doesn't stop a same-login attacker, so withdrawals-off + IP-allowlist are mandatory complements), and every order passes a **risk guard + kill switch** ([ADR-0043](adrs/0043-execution-venue-protocol.md)). Tier 5 separately introduces external information ingestion at scale. Each boundary needs a security review against an updated threat model; defaults from [ADR-0008](adrs/0008-electron-shell-conventions.md) (Electron) and [ADR-0011](adrs/0011-bearer-secret-transport.md) (bearer) are necessary, not sufficient.
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
