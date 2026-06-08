# ADRs

Architecture Decision Records for `market-analyser`. Each ADR is one file (`NNNN-<slug>.md`) capturing a decision **and the alternatives rejected**, so a future maintainer can tell whether the original reasoning still holds. ADRs are **append-only once accepted** — to change a decision, write a new ADR that supersedes the old one (the old one stays, marked `superseded by NNNN`).

This index is the one-minute entrypoint: what exists, each one's status, and how they relate. It is a *view* — the ADR files are the source of truth. If a row disagrees with a file's header, the file wins; fix the row.

## Roster

| #    | Title | Status | Lineage | Plan(s) |
|------|-------|--------|---------|---------|
| [0001](0001-tauri-vs-electron.md) | Tauri as desktop shell | superseded by 0005 | → 0005 | 0001 |
| [0002](0002-ipc-local-http.md) | UI↔sidecar IPC over localhost HTTP | accepted | refined by 0011 | 0001 |
| [0003](0003-vendoring-strategy.md) | Vendor an upstream MCP project (mirrored subtree) | superseded by 0009 | → 0009 | 0001 |
| [0004](0004-strategy-interface.md) | Strategy interface: typed fn + declarative params | accepted | amended by 0009 | 0002 |
| [0005](0005-desktop-shell-electron.md) | Desktop shell: Tauri → Electron | accepted | supersedes 0001 | 0001 |
| [0006](0006-persistence-layout.md) | Persistence: SQLite for data, JSON for config | accepted | amended by 0009; related 0020 | 0001 |
| [0007](0007-market-data-provider.md) | `MarketDataProvider` abstraction | accepted | amended by 0009 | 0001 |
| [0008](0008-electron-shell-conventions.md) | Electron shell conventions (build, IPC, CSP) | accepted | tsconfig partly superseded by 0010 | 0001 |
| [0009](0009-rewrite-data-layer-in-house.md) | Drop vendored upstream; rewrite data layer in-house | accepted | supersedes 0003; amends 0004/0006/0007 | 0003 |
| [0010](0010-tsconfig-solution-layout.md) | tsconfig solution layout (shared base) | accepted | refines 0008 (tsconfig) | — |
| [0011](0011-bearer-secret-transport.md) | Bearer-secret transport: env-var, not argv | accepted | refines 0002; refined by 0016 | 0001, 0004 |
| [0012](0012-dependency-cooldown.md) | Dependency cooldown (14 days) | accepted | paired with 0013 | 0005 |
| [0013](0013-pin-direct-dependencies.md) | Pin every direct dependency exactly | accepted | paired with 0012 | 0005 |
| [0014](0014-mcp-as-second-sidecar-protocol.md) | MCP as a second sidecar protocol | accepted | refined by 0015 | 0006 |
| [0015](0015-claude-code-primary-control-surface.md) | Claude Code (MCP) as primary control surface | accepted | refines 0014 | 0007 |
| [0016](0016-standalone-sidecar-mode.md) | Standalone sidecar + idempotent attach | accepted | refines 0011; refined by 0020, 0022 | 0007 |
| [0017](0017-live-ui-updates-via-sse.md) | Live UI updates via SSE event stream | accepted | — | 0007, 0006 |
| [0018](0018-backtest-result-schema.md) | `BacktestResult` schema | accepted | extended by 0024 | 0008, 0002 |
| [0019](0019-external-http-adapter-resilience.md) | External HTTP adapter resilience (shared module) | accepted | — | 0009–0012 |
| [0020](0020-shared-data-dir-contract.md) | Shared data-dir contract (Python ↔ Electron) | accepted | refines 0016 (+ related 0006) | 0007 |
| [0021](0021-renderer-to-agent-feedback.md) | Renderer→agent feedback (MCP resources + notifications) | proposed | — | 0014 |
| [0022](0022-sidecar-shutdown-cleanup-in-lifespan.md) | Sidecar shutdown cleanup in app lifespan | accepted | refines 0016 (shutdown contract) | none (bug fix) |
| [0023](0023-technical-analysis-surface.md) | Technical-analysis surface in `analysis/` | accepted (Plan 0018 close 2026-05-30) | — | 0018 |
| [0024](0024-extended-backtest-metrics.md) | Extended backtest metrics (definitions + degenerate convention) | accepted (Plan 0020 close 2026-06-03) | extends 0018 | 0020 |
| [0025](0025-trade-execution-feasibility.md) | Trade-execution feasibility (posture + venue comparison) | proposed (exploratory — no plan, no close ceremony) | relates 0006/0007/0011 | none |
| [0026](0026-symbol-search-bound-to-ohlcv-provider.md) | Symbol search bound to the OHLCV provider | accepted (Plan 0024 close 2026-05-29) | relates 0007 | 0024 |
| [0027](0027-crypto-macro-regime-classification.md) | Crypto macro regime as an in-house neutral structural classification | accepted (Plan 0022 close 2026-06-03) | relates 0007/0009 | 0022 |
| [0028](0028-timeframe-resampling-and-expansion.md) | Canonical timeframe registry + in-house 4h resampling + per-timeframe history caps | accepted (Plan 0025 close 2026-05-30) | relates 0007/0009/0019 | 0025 |
| [0029](0029-advisory-recommendation-boundary.md) | Advisory recommendation boundary (the app may recommend, not act) | proposed — accepts at first advisor plan close | carves out of 0015; below 0025 | none yet |
| [0030](0030-forecasting-subsystem.md) | Forecasting subsystem (causal, validated, direction-as-probability) | accepted (Plan 0036 close, 2026-06-07) | mirrors 0018; reuses 0024 | 0036 |
| [0031](0031-data-source-adapter-contract.md) | Per-capability data-source Protocols + selector-registry dispatch | accepted (Plan 0028 close 2026-06-02) | relates 0007/0009 | 0028 |
| [0032](0032-data-layer-no-api-dependency.md) | Data layer imports no `api` modules (event bus in neutral `events/` core) | accepted (Plan 0028 close 2026-06-02) | relates 0007 | 0028 |
| [0033](0033-empty-ohlcv-response-by-recency.md) | Empty Yahoo OHLCV response classified by window recency | accepted (Plan 0031 close 2026-06-03) | refines 0013 heuristic; relates 0007 | 0031 |
| [0034](0034-defi-portfolio-aggregator.md) | DeFi portfolio aggregator (Zerion for discovery + tx history; swappable seam) | accepted (Plan 0032 live smoke, 2026-06-05) | reuses 0031 seam; reconciles 0009; relates 0019 | 0032 |
| [0035](0035-defi-domain-placement.md) | DeFi domain placement (`defi/` package; on-chain fetch as ADR-0031 sources) | accepted (Plan 0032 close, 2026-06-03) | reuses 0031; obeys 0032; relates 0007 | 0032 |
| [0036](0036-defi-pnl-reconstruction.md) | DeFi P&L by tx replay (block-time pricing, average-cost lots) | proposed — accepts at DeFi P&L plan close | mirrors 0018 determinism; relates 0034 | none yet |
| [0037](0037-defi-position-risk-forecast.md) | DeFi position risk/forecast (conditional facts, not market prediction) | proposed — accepts at DeFi risk plan close | distinct from 0030; report-side of 0029 | none yet |
| [0038](0038-third-party-api-key-storage.md) | Third-party API-key storage (`0600` secrets file, write-only to renderer) | accepted (Plan 0032 close, 2026-06-03) | extends 0011; relates 0006/0020 | 0032 |
| [0039](0039-renderer-theming-localstorage.md) | Renderer theming via `data-theme` + localStorage (no sidecar round-trip) | accepted (Plan 0033 close, 2026-06-04) | relates 0006 (carves presentation prefs out of config.json); constrained by 0008 (CSP) | 0033 |
| [0040](0040-forecasting-model-artifacts.md) | Forecasting model artifacts: deterministic library stack (sklearn `HistGradientBoosting`) + versioning & provenance | accepted (Plan 0036 close, 2026-06-07) | implements 0030 (fills its open library/determinism/persistence slots); mirrors 0018 for model artifacts | 0036 |
| [0041](0041-polymarket-odds-read-source.md) | Polymarket as a read-only prediction-market odds source | proposed — accepts at Plan 0040 close | adds a capability under 0031 (no supersede); relates 0009/0019; trading deferred to 0025 | 0040 |
| [0042](0042-cross-venue-portfolio-aggregation.md) | Cross-venue portfolio aggregation (read-only, tools-only, average-cost basis) | proposed — accepts at Plan 0041 close | adopts 0036 basis venue-wide; Binance read adapter under 0031, key in 0038; advice deferred to 0029 | 0041 |
| [0043](0043-execution-venue-protocol.md) | `ExecutionVenue` Protocol + persisted order/position state machine | proposed — accepts at Plan 0044 close | implements 0025 invariant 3/5; mirrors 0007 (write side); persists per 0006 | 0044 |
| [0044](0044-trade-secret-store.md) | Segregated trade-secret store (OS keychain via `keyring`) | proposed — accepts at Plan 0044 close | implements 0025 invariant 4; distinct from 0038 (read keys) + 0011 (IPC bearer) | 0044 |
| [0045](0045-candlestick-pattern-span-delivery.md) | Candlestick pattern delivery: span-bearing markers, derived not persisted | accepted | extends the 0017 highlight channel; relates 0023 (pattern surface) / 0006 (derived-not-persisted) | 0049 |
| [0046](0046-mcp-large-result-delivery.md) | MCP large-result delivery: bounded pages + typed `too_large`, not unbounded dumps | proposed — accepts at Plan 0050 close | extends the 0013 cache-honest shape; serves 0014/0015 (context-bounded reader); governs 0018 trades/equity | 0050 |
| [0047](0047-variable-duration-monthly-timeframe.md) | Monthly is a native, variable-duration timeframe (`bar_duration` = max month) | proposed — accepts at Plan 0050 close | extends 0028 (timeframe registry); relates 0007/0009 | 0050 |

**Standalone (no supersede/refine lineage):** 0012/0013 (a peer pair), 0017, 0019, 0021, 0023, 0025, 0026, 0027, 0028, 0031, 0032, 0039. Everything else sits in one of the chains below. ADR-0029 and ADR-0030 were decided together (2026-05-30) and compose (the advisor consumes forecasts) but are independent decisions; ADR-0029 carves out of ADR-0015's "conditions are facts" framing and sits one layer below ADR-0025 (execution), and ADR-0030 mirrors ADR-0018's determinism contract and reuses ADR-0024's walk-forward machinery.

**DeFi cluster (0034–0037, decided together 2026-06-03):** the four ADRs for the wallet-analysis program compose but are independent decisions. ADR-0034 picks the discovery/tx-history aggregator and reconciles the in-house-data-layer policy (ADR-0009) for an external interpreter; ADR-0035 places the domain (`src/market_analyser/defi/`) and routes on-chain fetch through the ADR-0031 per-capability Protocol seam while obeying ADR-0032 (no `data→api`); ADR-0036 reconstructs P&L from ADR-0034's decoded events, mirroring ADR-0018's determinism contract; ADR-0037 is deliberately **distinct from** ADR-0030 (it forecasts the *position under assumed moves*, not the *market*, so the walk-forward gate doesn't apply) and stays on the report side of the ADR-0029 advisory boundary. ADR-0038 (third-party API-key storage) was forced by the cluster's first authenticated source and extends ADR-0011's secret discipline. **Plan 0032 (the first DeFi plan) closed 2026-06-03:** ADR-0035 (domain placement) and ADR-0038 (API-key storage) were **accepted** at close — both fully realized and tested offline. **ADR-0034 accepted 2026-06-05** after Plan 0032's live smoke: scanning a real wallet returned correctly-valued positions reconciling to Zerion's reported net worth (no 2× double-count), and the smoke caught + fixed a `filter[positions]` bug that had excluded all DeFi positions; two non-blocking followups (staked-LP classification, Aave/Uni-v3 live coverage) feed the deep-adapter plan. ADR-0036/0037 stay `proposed` pending their own (P&L / risk) plans.

## Lineage

How decisions have replaced or evolved one another. Most ADRs are standalone and are not shown; only the ones with a supersede/refine/amend/extend edge appear.

```mermaid
flowchart LR
  %% Supersessions (a later ADR replaces an earlier one)
  a0001["0001 · Tauri shell"] -->|superseded by| a0005["0005 · Electron shell"]
  a0003["0003 · vendor upstream"] -->|superseded by| a0009["0009 · in-house data layer"]

  %% Refinements (earlier decision stands; later ADR narrows/extends it)
  a0002["0002 · IPC over HTTP"] -->|refined by| a0011["0011 · bearer transport"]
  a0011 -->|refined by| a0016["0016 · standalone sidecar"]
  a0016 -->|refined by| a0020["0020 · data-dir contract"]
  a0016 -->|refined by| a0022["0022 · shutdown cleanup"]
  a0014["0014 · MCP protocol"] -->|refined by| a0015["0015 · Claude primary"]

  %% Partial supersede / amend / extend (dashed: earlier ADR's prose still stands)
  a0008["0008 · Electron conventions"] -.->|tsconfig partly superseded by| a0010["0010 · tsconfig layout"]
  a0009 -.->|amends| a0004["0004 · strategy interface"]
  a0009 -.->|amends| a0006["0006 · persistence"]
  a0009 -.->|amends| a0007["0007 · MarketDataProvider"]
  a0018["0018 · BacktestResult"] -.->|extended by| a0024["0024 · extended metrics"]
  a0015["0015 · Claude primary"] -.->|carved out by| a0029["0029 · advisory boundary"]
  a0018 -.->|determinism mirrored by| a0030["0030 · forecasting"]
  a0030 -.->|implemented by| a0040["0040 · forecast model artifacts"]
  a0018 -.->|artifact determinism mirrored by| a0040
  a0031["0031 · data-source contract"] -.->|capability added by| a0041["0041 · Polymarket odds"]
  a0036b["0036 · DeFi P&L"] -.->|avg-cost basis adopted by| a0042b["0042 · portfolio aggregation"]
  a0025x["0025 · execution feasibility"] -.->|Protocol+FSM (inv 3/5)| a0043x["0043 · ExecutionVenue Protocol"]
  a0025x -.->|secret store (inv 4)| a0044x["0044 · trade-secret store"]
  a0007b["0007 · MarketDataProvider"] -.->|write-side mirror| a0043x
```

**Reading the edges:** solid = supersede or refine (the later ADR changes which decision is in force); dashed = amend / extend / partial-supersede (the earlier ADR's body still stands, the later one adjusts its interpretation or appends to it — e.g. ADR-0009 didn't rewrite 0004/0006/0007, it reinterpreted "vendored" as "in-house" across them).

## Conventions

- **Numbering** is sequential, zero-padded to four digits, never reused. Next free ADR number is **0045** (0043 + 0044 drafted 2026-06-05 — the execution pillar: `ExecutionVenue` Protocol + persisted order/position state machine (0043) and the segregated trade-secret store via OS keychain (0044), both paired with Plan 0044, both implementing ADR-0025's invariants; with these the trade/predict/portfolio program's ADRs 0040–0044 are all drafted; 0042 drafted 2026-06-05 — cross-venue portfolio aggregation: read-only, tools-only, average-cost basis venue-wide, paired with Plan 0041; 0041 drafted 2026-06-05 — Polymarket as a read-only prediction-market odds source, paired with Plan 0040, adds a capability under ADR-0031; 0040 drafted 2026-06-05 — forecasting model artifacts: deterministic sklearn `HistGradientBoosting` library stack + model versioning/provenance, paired with Plan 0036, fills the slots ADR-0030 left open; with 0043–0044 now drafted the program's ADR reservation (0040–0044) is fully consumed, so 0045 is the real next-free; 0039 drafted 2026-06-03 — renderer theming via `data-theme` + localStorage, paired with Plan 0033, accepted at its close (2026-06-04); 0034–0038 drafted 2026-06-03 — the DeFi wallet-analysis cluster: portfolio aggregator, domain placement, P&L reconstruction, position risk/forecast, and third-party API-key storage; 0035 + 0038 accepted at Plan 0032 close (2026-06-03), 0034 accepted 2026-06-05 after Plan 0032's live smoke, 0036–0037 `proposed` pending their P&L / risk plans; 0033 drafted 2026-06-03 — empty-OHLCV-response recency classification, paired with Plan 0031; 0031/0032 drafted + accepted 2026-06-02 at Plan 0028 close — data-source adapter contract + data-layer no-`api`-dependency; 0030 drafted 2026-05-30 — forecasting subsystem, no plan yet, Plan 0020 prereq; 0029 drafted 2026-05-30 — advisory recommendation boundary, no plan yet). The architect runs `Glob docs/architecture/adrs/*.md` before drafting to pick the next number, never trusting memory. ADR numbers and plan numbers are independent sequences.
- **Append-only after `accepted`.** Don't edit an accepted ADR's decision. Supersede it with a new ADR and mark the old one `superseded by NNNN`. The one sanctioned exception to date: the 2026-05-24 owner-authorized genericization of the upstream-project name across ADR bodies (a privacy edit that changed no decision).
- **Status vocabulary:** `proposed` → `accepted` → optionally `superseded by NNNN`. A `proposed` ADR paired with a plan flips to `accepted` at that plan's close ceremony (e.g. 0023 at Plan 0018 close, 0024 at Plan 0020 close).
- **Paired with a plan?** Most ADRs are written alongside the plan that forces the decision. The Plan(s) column links them; the plans index lives at [`../plans/README.md`](../plans/README.md).

## Index freshness

This index is refreshed by the architect on every ADR mutation it owns — same discipline as the plans index:

| Trigger | Update |
|---------|--------|
| New ADR written | Add a roster row; bump next-free-number; add a lineage edge if it supersedes/refines/amends/extends another ADR. |
| Status flip (`proposed → accepted`, or a supersede) | Update the row's Status; if a supersede, add the lineage edge and flip the superseded ADR's status. |
| Drift found (a row disagrees with the file header) | Fix the row in the same session and note it — the file header always wins. |
