# Claude-CLI-driven architecture

Source of truth for the post-[ADR-0015](../adrs/0015-claude-code-primary-control-surface.md) layout — Claude Code as the primary control surface, Electron as the live viewer, sidecar standalone-capable. Update whenever a transport, route, or persisted secret changes shape. _Last refreshed 2026-07-14 (README-actualisation pass) — toolset now **56 tools** (authoritative count is the exhaustive `EXPECTED_FULL_TOOLSET` registration test); folded in the tool lanes that landed since Plan 0061: the Coinbase USD-native source ([ADR-0076](../adrs/0076-coinbase-usd-native-crypto-source.md)), the momentum/divergence/money-flow + price-structure/levels scanners (`detect_divergences`, `smart_volume`, `volume_breakout`/`volume_confirmation`, `counter_trend_volume`, `market_structure`, `anchored_vwap`, `fibonacci_levels`, `pivot_points`), the non-directional forecast pair (`forecast_regime`/`forecast_volatility`, [ADR-0070](../adrs/0070-non-directional-forecast-targets.md)), the recommendation track-record (`get_track_record`, [ADR-0075](../adrs/0075-recommendation-outcome-attribution.md)), the Polymarket read-source (`prediction_market_odds`/`search_prediction_markets`/`find_convergence_opportunities`, [ADR-0041](../adrs/0041-polymarket-odds-read-source.md)/[ADR-0078](../adrs/0078-chart-pattern-visual-fidelity.md)), and the cross-pool discrepancy scanner (`scan_pool_discrepancies`, [ADR-0080](../adrs/0080-executable-quote-pricing-concentrated-liquidity.md)). Note: **agent mode is slated for removal** ([ADR-0101](../adrs/0101-remove-agent-mode-gate.md) / Plan 0106, in flight) — the `GET`/`PUT /agent_mode` routes and gate on `POST /ui_events` below will go; refresh again at that close. Prior refresh 2026-07-06 (Plan 0061 close) — the `data/` metric-accrual lifespan job + `/healthz` heartbeat ([ADR-0056](../adrs/0056-self-warming-metric-store.md)), the `forecast.completed v1` event + Forecast tab (Plan 0037), and the `portfolio/` lane (Plan 0041). Prior 2026-07-05 — the DeFi P&L lane (Plan 0035 / [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md)). Prior 2026-07-03 (forecast/advisor/alerts lanes, metric-series store, Plan 0060 surface)._

## Component map

```mermaid
flowchart LR
    User[Human at keyboard]

    subgraph CLI["Claude Code (CLI) — primary control surface"]
        Claude["Claude<br/>(MCP client built-in)"]
    end

    subgraph Sidecar["Python sidecar (standalone process)"]
        MCPRoute["/mcp<br/>Streamable HTTP<br/>(mcp-secret bearer)"]
        Routes["Renderer routes (renderer bearer)<br/>/healthz · /ohlcv · /quote · /search · /annotations<br/>/backtests · /news · /scan_patterns · /scan_chart_patterns<br/>/track_record · /agent_mode (removal in flight, ADR-0101)<br/>/ui_events · /defi/scan · /defi/pnl · /watches · /alerts<br/>/settings/* · /stop · /events (SSE)"]
        Bus["In-process event bus<br/>(per-subscriber asyncio queue)"]
        Tools["MCP tools (56 registered, pinned by the<br/>exhaustive toolset test)<br/>OHLCV + backfill · annotations · agent chart<br/>(show/update/highlight) · backtest + get_backtest<br/>+ walk-forward + compare · evaluate_signals<br/>· screener · news/sentiment/stocktwits · quote · search<br/>· analyze_symbol + multi-tf + volume scanners<br/>(smart_volume/volume_breakout/volume_confirmation/counter_trend)<br/>· scan_patterns · detect_levels · detect_chart_patterns<br/>· detect_divergences · market_structure · anchored_vwap<br/>· fibonacci_levels · pivot_points<br/>· macro (snapshot/btc/fear-greed) · btc_cycle_snapshot<br/>· get_metric_series · derivatives_snapshot<br/>· forecast + forecast_regime/volatility · recommend<br/>· get_track_record · prediction markets (odds/search/convergence)<br/>· scan_wallet · compute_wallet_pnl · scan_pool_discrepancies<br/>· portfolio_summary · create/list/delete_watch + list_alerts<br/>· ui-events poll"]
        Analysis["analysis/<br/>(indicators, patterns, chart patterns,<br/>levels, snapshot, volume, cycles, multi-tf)"]
        DL["MarketDataProvider +<br/>per-capability sources + repositories<br/>(Yahoo · Binance klines/derivatives ·<br/>CoinMetrics · CoinGecko · F&G · …)"]
        Engine["Backtest engine<br/>(pure run + walk-forward + persist<br/>+ live-signal eval; flat/long/short)"]
        Forecast["forecast/<br/>(causal features, walk-forward-gated<br/>calibrated direction probability)"]
        Advisor["advisor/<br/>(fuse() → labeled advisory<br/>Recommendation, ADR-0029)"]
        DeFi["defi/<br/>(wallet discovery, scan job,<br/>deep LP enrichment, P&L replay<br/>engine + pnl job, ADR-0036)"]
        Portfolio["portfolio/<br/>(three-leg aggregation: Binance read<br/>+ DeFi + manual file → portfolio_summary,<br/>ADR-0042)"]
        Sched["alerts/ scheduler<br/>(lifespan asyncio loop: watches →<br/>edge-triggered alert.triggered, ADR-0055)"]
        Accrual["data/ metric-accrual job<br/>(lifespan hourly tick, ADR-0056:<br/>warms the five v2 exogenous series,<br/>heartbeat on /healthz)"]
        Cache[("SQLite cache.sqlite<br/>bars, annotations, backtest_runs,<br/>metric_points, watches, alerts,<br/>defi_tx, price_snapshots")]
        MCPRoute --> Tools
        Tools --> DL
        Tools --> Analysis
        Tools --> Engine
        Tools --> Forecast
        Tools --> Advisor
        Tools --> DeFi
        Tools --> Portfolio
        Tools --> Bus
        Analysis --> DL
        Engine --> DL
        Engine --> Cache
        Engine --> Bus
        Forecast --> Analysis
        Advisor --> Forecast
        Advisor --> Engine
        Advisor --> Analysis
        DeFi --> DL
        DeFi --> Cache
        DeFi --> Bus
        Portfolio --> DL
        Portfolio --> DeFi
        Sched --> DL
        Sched --> Cache
        Sched --> Bus
        Accrual --> DL
        Accrual --> Cache
        Routes --> DL
        Routes --> DeFi
        Routes --> Cache
        Bus --> Routes
        DL --> Cache
    end

    subgraph UD["User data dir (mode 0600 files)"]
        Lockfile[("sidecar.lock<br/>pid, port, renderer_secret,<br/>process_create_time")]
        MCPSecret[("mcp-secret.json")]
        Secrets[("secrets.json<br/>third-party API keys<br/>(ADR-0038)")]
    end

    subgraph Electron["Electron viewer (optional, attachable)"]
        View["Main process + Renderer<br/>(React, lightweight-charts)<br/>chart · backtests · signals · recommendations<br/>· forecast · news · alerts · settings"]
    end

    User -- "types prompts" --> Claude
    User -- "watches charts" --> View
    Claude -- "HTTP Streamable<br/>Bearer: mcp-secret" --> MCPRoute
    View -- "HTTP<br/>Bearer: renderer_secret" --> Routes
    View -. "EventSource SSE<br/>?token=<renderer_secret>" .-> Routes
    View -. "idempotent attach<br/>or spawn if no live PID" .-> Sidecar
    Sidecar -. "writes on boot<br/>(rotated per sidecar launch)" .-> Lockfile
    View -. "reads on attach" .-> Lockfile
    MCPRoute -. "reads" .-> MCPSecret
    DeFi -. "reads Zerion key" .-> Secrets
```

Boundaries:

- **CLI** is Claude Code with its built-in MCP client. The user's primary input device — symbols, timeframes, strategies, overlay choices, backtest parameters, render commands all originate here.
- **Sidecar** is the single Python process. It serves two transports on one loopback port: MCP at `/mcp` (Streamable HTTP, agent-facing, per [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md)) and the renderer HTTP routes (viewer-facing, per [ADR-0002](../adrs/0002-ipc-local-http.md)). Both transports share the same data layer, repositories, and SQLite cache. The data layer routes external reads through per-capability source Protocols + a selector registry ([ADR-0031](../adrs/0031-data-source-adapter-contract.md)); the `analysis/` surface ([ADR-0023](../adrs/0023-technical-analysis-surface.md): trailing indicators, candlestick patterns, `condition_snapshot`, volume, multi-timeframe) and the `defi/` domain ([ADR-0035](../adrs/0035-defi-domain-placement.md): wallet discovery + async scan job, emitting `defi.scan_*`; since Plan 0035 also the [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) P&L vertical — decoded-tx ingestion into the immutable `defi_tx` cache, block-time pricing through the first-write-wins `price_snapshots` cache, and the average-cost replay engine behind `compute_wallet_pnl`/`POST /defi/pnl`, emitting `defi.pnl_*`) sit beside the backtest engine as agent-callable lanes. The backtest engine (pure `run` + walk-forward + thin `persist`, per [ADR-0018](../adrs/0018-backtest-result-schema.md)/[ADR-0024](../adrs/0024-extended-backtest-metrics.md); flat/long/short since [ADR-0050](../adrs/0050-short-selling-strategy-backtest.md)) sits behind `run_backtest`/`get_backtest`/`walk_forward_backtest`/`compare_strategies`, persists to the `backtest_runs` table, and emits `run.completed v1`. Three lanes landed after the 2026-06-05 refresh: **`forecast/`** ([ADR-0030](../adrs/0030-forecasting-subsystem.md)/[ADR-0040](../adrs/0040-forecasting-model-artifacts.md) — causal features over `analysis/`, walk-forward-gated calibrated direction probability, multi-horizon since [ADR-0054](../adrs/0054-exogenous-forecast-features-multi-horizon.md), behind the `forecast` tool — emitting `forecast.completed v1` since Plan 0037); **`advisor/`** ([ADR-0029](../adrs/0029-advisory-recommendation-boundary.md) — the one sanctioned recommend layer: `fuse()` of snapshot + live signal + walk-forward edge + forecast behind the `recommend` tool, emitting `recommendation.completed v1`); and **`alerts/`** ([ADR-0055](../adrs/0055-in-sidecar-watch-scheduler.md) — a lifespan asyncio scheduler evaluating persisted watches per closed bar and emitting edge-triggered, condition-only `alert.triggered v1`). A fourth lane, **`portfolio/`** ([ADR-0042](../adrs/0042-cross-venue-portfolio-aggregation.md) — pure three-leg aggregation of the Binance read-only account adapter + the DeFi replay basis + the manual positions file), sits behind the read-only `portfolio_summary` tool. Historized external metrics (F&G, dominance, funding, OI, MVRV) live in the `metric_points` table behind the [ADR-0051](../adrs/0051-historized-metric-series-contract.md) `as_of` contract, surfaced via `btc_cycle_snapshot`/`get_metric_series`/`derivatives_snapshot` — and since Plan 0061 the store is **self-warming**: a metric-accrual job rides the app lifespan ([ADR-0056](../adrs/0056-self-warming-metric-store.md), the ADR-0055 pattern — separate duty, separate clock), ticking hourly to keep the five v2 exogenous series topped up (cold-start backfill/seed, per-series failure containment, per-series heartbeat on `/healthz`). The event bus lives in a neutral top-level `events/` core ([ADR-0032](../adrs/0032-data-layer-no-api-dependency.md)) — MCP tools, the engine, the scan job, the `recommend` tool, and the scheduler publish (vocabulary: `chart.*`, `run.completed`, `signal.evaluated`, `recommendation.completed`, `forecast.completed`, `ohlcv.backfill_*`, `defi.scan_*`, `defi.pnl_*`, `alert.triggered` — all v1); the SSE handler at `/events` is the sole subscriber-dispatch, with a UI-event buffer serving the agent's polling path ([ADR-0021](../adrs/0021-renderer-to-agent-feedback.md)).
- **User data dir** holds three persisted files, each `0600`. `mcp-secret.json` is the long-lived MCP bearer (ADR-0014); `sidecar.lock` is per-sidecar-launch (ADR-0016); `secrets.json` holds third-party data-source API keys (e.g. the Zerion key the DeFi scan needs) per [ADR-0038](../adrs/0038-third-party-api-key-storage.md) — its values are never logged and never returned by any endpoint (`GET /settings/secrets` reports only `"set"`/absent).
- **Electron viewer** is optional. The sidecar runs without it; opening Electron attaches to the running sidecar via the lockfile (or spawns one if none is running). Closing Electron does not stop the sidecar.

Critical invariants:

- **Cross-tenant bearer isolation.** MCP secret authenticates only on `/mcp/*`. Renderer secret authenticates only on the renderer routes. Each middleware uses constant-time comparison; the dispatcher routes by prefix.
- **Single sidecar instance per user.** Enforced by `sidecar.lock` + PID liveness probe with `process_create_time` cross-check (ADR-0016).
- **SQLite single-writer.** Falls out of the single-instance enforcement.
- **No lookahead in agent-facing tools.** `get_ohlcv` and bar-reading tools forward `as_of=None` (live mode). The backtest-aware path is the separate `run_backtest` tool (per [ADR-0018](../adrs/0018-backtest-result-schema.md)), not an `as_of` parameter bolted onto `get_ohlcv`; the engine enforces the next-bar-open fill seam internally (see [`strategy-execution-sequence.md`](strategy-execution-sequence.md)) and re-runs deterministically (per ADR-0014 and the [CLAUDE.md non-negotiable](../../../CLAUDE.md#cross-cutting-non-negotiables)).

## Lifecycle: cold start vs. attach vs. agent-only

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant E as Electron main
    participant LF as sidecar.lock
    participant S as Python sidecar
    participant C as Claude Code (MCP)
    participant V as Renderer

    Note over User,V: Cold start (no sidecar running)
    User->>E: open app
    E->>LF: read
    LF-->>E: not present
    E->>S: spawn(python -m market_analyser.api --port=0)
    S->>LF: atomic write {pid, port, renderer_secret, ...}
    S-->>E: PORT=53221 on stdout
    E->>LF: read renderer_secret + port
    E->>V: launch with {port, renderer_secret}
    V->>S: GET /healthz (renderer bearer)
    V->>S: open EventSource /events?token=<secret>

    Note over User,V: Agent-only (no Electron open)
    User->>C: "show me AAPL 1d with EMA20"
    C->>S: MCP show_chart(...)
    S->>S: publish chart.show v1 to event bus
    S-->>C: {event_published: true}
    Note right of S: no subscribers — event dropped<br/>(user can open Electron and re-ask)

    Note over User,V: Attach (Electron opens, sidecar already running)
    User->>E: open app
    E->>LF: read
    LF-->>E: {pid: 12345, port: 53221, renderer_secret, ...}
    E->>S: probe psutil.Process(12345).create_time()
    Note right of E: matches lockfile (Python-side)
    E->>S: GET /healthz (Authorization: Bearer renderer_secret)
    S-->>E: {ok: true, data_dir: "/.../market-analyser"}
    Note right of E: data_dir matches resolveSharedDataDir()<br/>(ADR-0020, phase 4.2)<br/>→ attach, do NOT spawn<br/>on mismatch: fall through to spawn
    E->>V: launch with {port, renderer_secret}
    V->>S: open EventSource /events
    User->>C: "now zoom to last 10 days"
    C->>S: MCP update_chart(...)
    S->>S: publish chart.update v1
    S-->>V: SSE: chart.update envelope
    V-->>User: chart re-renders live

    Note over User,V: Electron close (sidecar survives)
    User->>E: quit app
    E--xV: window closed
    Note right of E: before-quit handler does NOT<br/>signal the sidecar (ADR-0016)
    Note over S: sidecar keeps running<br/>lockfile unchanged
```

The "agent-only" branch is the visible payoff of [ADR-0016](../adrs/0016-standalone-sidecar-mode.md): Claude can drive workflows without the viewer being open. The "attach" branch is the payoff of the lockfile mechanism: opening the viewer is one step regardless of prior sidecar state.

## Recovery: renderer-initiated refresh on out-of-band sidecar restart

Standalone-mode sidecars (ADR-0016) don't restart from Electron's perspective — the supervisor decoupled lifecycle. But the sidecar *can* die out-of-band (user `python -m market_analyser.api stop` followed by a fresh start, or a manual kill). The renderer discovers and recovers via the sequence below.

```mermaid
sequenceDiagram
    autonumber
    participant V as Renderer<br/>useEventStream
    participant E as Electron main<br/>SidecarSupervisor
    participant LF as sidecar.lock
    participant S1 as Old sidecar (dead)
    participant S2 as New sidecar (alive)

    Note over V,S2: Steady state: old sidecar dies, user starts a new one
    V->>S1: EventSource open
    Note over S1: process exits<br/>finally → remove lockfile
    S2->>LF: atomic write {new pid, new port, new renderer_secret}
    V->>S1: ❌ onerror (connection lost)
    V->>S1: ❌ onerror
    V->>S1: ❌ onerror
    Note right of V: 3 errors within 10s<br/>without an intervening onopen<br/>(phase 4.4 threshold)
    V->>E: window.api.sidecar.refresh() (IPC)
    E->>E: SidecarSupervisor.refresh()
    Note right of E: re-runs attachOrSpawnSidecar:<br/>reads lockfile, runs PID + healthz check
    E->>LF: read
    LF-->>E: {new pid, new port, new renderer_secret}
    E->>S2: GET /healthz (Bearer new renderer_secret)
    S2-->>E: {ok: true, data_dir: matches}
    E-->>V: sidecar:status {kind: refreshed, port: NEW, secretToken: NEW}
    Note right of V: api/client cache updates<br/>both port + secretToken<br/>subscribeToConfigChanges fires
    V->>V: close old EventSource
    V->>S2: open EventSource /events?token=NEW
    S2-->>V: : ping
    V-->>V: onopen → state=open, error counter reset
```

The `refresh()` call coalesces concurrent invocations (phase 4.3) so a renderer-side error storm collapses to one attach cycle upstream. The "refresh fires at most once per `onopen`-bounded window" rule (phase 4.4) prevents an infinite refresh loop if the new sidecar also can't be reached.

## Companion diagrams

This file owns the **process/component map and the sidecar lifecycle**. Two companions cover narrower cuts:

- [`bootstrap-component-map.md`](bootstrap-component-map.md) — the OHLCV walking-skeleton **data flow** (renderer → sidecar → provider → cache/Yahoo) and the **SQLite schema**. Reach for it when the question is "how does a single chart request resolve" or "what shape is a table", not "how do the processes fit together".
- [`strategy-execution-sequence.md`](strategy-execution-sequence.md) — the **backtest runtime order** and the next-bar-open anti-lookahead seam. Independent of which client (agent vs viewer) requested the run.
