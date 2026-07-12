# Consolidated live smoke — closing the human-smoke gap

> **Purpose.** Plans here close on *code* gates (tests green, apiref clean); the final "phase N = human live smoke" is explicitly **not a code gate**, so plans ship before anyone eyeballs them live. Those smokes have mostly been run but never written back, so the close notes still say "outstanding." This is the **single pass** that re-verifies every still-unconfirmed surface in one sitting and records the verdict, so the docs stop lying.
>
> **How to use.** Bring the app up (`pnpm dev:all` from the repo root — spawns the sidecar + Electron viewer), open the viewer, then walk the three parts top to bottom. Part A is agent-driven (an MCP-connected agent runs the tool calls); Part B needs a human at the viewer; Part C needs secrets. **After each item, tick it and fold the verdict into the ledger at the bottom** — then a follow-up architect touch mirrors the ledger into the affected `done/` plan close notes + the plans-index rows.
>
> **This is a verification artifact, not a plan.** It reports conditions; it recommends nothing and moves no funds (ADR-0025/0029). Re-run it whenever a batch of smokes re-accumulates.

Derived from the `is the user's outstanding step` markers in [`plans/README.md`](plans/README.md) as of 2026-07-12: Plans 0065, 0066, 0071, 0074, 0076, 0077, 0078, 0079, 0080, 0082, 0087, 0089 — plus 0083's visual smokes and a re-confirm of 0088.

## Prerequisites

- **App running:** `pnpm dev:all` (sidecar on `127.0.0.1` + viewer). Confirm the viewer connects to the sidecar (SSE live).
- **Cached bars** for `BTC-USD` / `ETH-USD` `1d` (Part A backfills via `get_ohlcv` on a `no_bars` miss).
- **Part C only:** an Alchemy Prices key + Base/Ethereum RPC pointed at Alchemy in Settings (`alchemy_prices_key`, `base_rpc_url`, `eth_rpc_url`), and the **full** `0x…` address of the test wallet (`0xae5b…9790`, masked in the docs). Without these, Part C is skipped, not failed.

---

## Part A — agent-driven checks (MCP tool calls)

Run each call; check the result against the pass criteria. All reads are trailing/no-lookahead by construction. Use `range_start = 2025-01-01T00:00:00Z` unless noted.

### A1 · Plan 0074 — technical read (advisory lesser tier)
- **Run:** `technical_read` on `BTC-USD` `1d` for **each** `indicator_id` ∈ {`supertrend`, `ema_stack`, `macd`, `ichimoku`}.
- **Pass:** each returns a `direction` (long/short/flat) + the indicator's `regime_state` + the mechanical rule as rationale; **no** conviction and **no** entry/stop/target fields (structural omission); the direction matches the visible regime on the chart. (Viewer banner → **B2**.)

### A2 · Plan 0078 — Polymarket convergence screener
- **Run:** `find_convergence_opportunities` with a broad, currently-active `query` (try `"2026"`, `"fed"`, `"election"`), `min_confidence=0.90`, `max_days_to_close=7`.
- **Pass:** near-resolution high-confidence markets surface with `implied_return_if_right = (1-p)/p`; `resolution_risk {level, reasons}` + `liquidity_caution` + `capital_lockup_note` populate on the by-eye-flaggable markets; **no `direction`/`size`/`action` field on any object**; nothing reads as a buy call. A typed `error` with `opportunities: null` (rate-limited/upstream) is an environment miss, not a failure.

### A3 · Plan 0089 — market links + explicit sort (data half)
- **Run:** the same `find_convergence_opportunities` result as A2.
- **Pass:** each opportunity carries a `market_url` of the form `https://polymarket.com/event/<slug>` (or `null` when the slug is absent — never a raise); results are ordered **largest → smallest** by `implied_return_if_right`. (Link-opens-browser → **B4**.)

### A4 · Plan 0080 — advisor track record
- **Run:** `get_track_record` (no filter).
- **Pass:** returns `track_record` with directional hit-rate + mean R, a calibration read (Brier + reliability buckets), and a **baseline comparison** (vs buy-and-hold over-horizon), each with its sample size and marked `insufficient` below the floor. An empty/small-n record is an **honest pass** (nothing scored yet is a valid state, never dressed up as a conclusion).

### A5 · Plan 0077 — non-directional forecasts + advisor non-voting wiring
- **Run:** `forecast_volatility` and `forecast_regime` on `BTC-USD` `1d` (repeat on `ETH-USD`), `range_end` = today; then `recommend strategy_id=supertrend symbol=BTC-USD timeframe=1d`.
- **Pass:** vol forecast returns predicted per-bar vol + 1σ OOS band + `beats_baseline` gate + a surfaced `baseline_vol`; regime forecast returns the current regime + a next-period distribution + `beats_baseline` vs persistence (Brier); `recommend`'s basis shows the vol/regime legs consumed as **non-voting** inputs (they shape conviction/sizing, never cast a directional vote — the all-voting-legs-agree invariant is intact). **Go/no-go:** decide whether to keep the advisor vol/regime wiring active.

### A6 · Plan 0066 — advisor tiered-forecast unification
- **Run:** the `recommend` call from A5 (or `strategy_id=ichimoku`).
- **Pass:** the recommendation's forecast leg rides the **tiered** forecast path — the basis/trace names the `feature_set_id`/tier (`v2-full → v2-deep → v1`); no crash; the fusion trace is present and replayable.

### A7 · Plan 0088 — DeFi P&L windowed (re-confirm; needs Part C wallet)
- **Run:** `compute_wallet_pnl {address: <full 0xae5b…9790>, refresh: false}` (piggybacks on C1's pulled history).
- **Pass:** each **LP** position reports `windows` with `7d/30d/90d/all` exact `realized_usd` (the `all` window equals the position's all-time realized) + a labeled `estimated` `total_return_usd` (`null` per-window where the window-start is unpriceable); the wallet reports a **non-null `partial` total** with `partial=true`/`incomplete_position_count≥1`; LP positions are listed first (`is_lp=true`). *(Already reported passed at close — this line just records it in the ledger.)*

---

## Part B — viewer-visual checks (human at the viewer)

An MCP-connected agent emits the chart/read where noted; the human confirms what renders.

### B1 · Plan 0076 — OBV overlay strip
- **Emit:** `show_chart BTC-USD 1d` with `overlays=[{kind: "obv"}]`.
- **Confirm:** an OBV strip renders in its own bottom pane, visibly tracking accumulation/distribution; toggling it in the layers legend adds/removes it.

### B2 · Plan 0074 — technical-read banner
- **Emit:** each `technical_read` from A1.
- **Confirm:** the viewer renders each read live with the technical-read banner (indicator, direction, the mechanical rule) — clearly the lesser/advisory tier, no levels shown.

### B3 · Plan 0082 — Bollinger Bands + user overlay form
- **Action (human):** open the add-overlay form, add `bbands` (set `period` + `k`).
- **Confirm:** three bands draw on the price pane; the overlay **persists across a viewer reload**; it **survives an agent `chart.show`** redraw; an agent-requested `bbands` also draws; user-remove clears it while an agent overlay only hides.

### B4 · Plan 0089 — "View on Polymarket ↗" link
- **Action (human):** open the Convergence panel (from A2's screen), click a card's **View on Polymarket ↗**.
- **Confirm:** the correct market page opens in the **system browser** (never in-app navigation); cards are ordered largest→smallest; an off-allowlist `market_url` renders no link; there are **zero trade controls** on the panel.

### B5 · Plan 0083 — chart-pattern visual fidelity
- **Emit:** `show_chart` on a symbol/window with an active classical pattern (use `detect_chart_patterns` / `scan_patterns` to find one — e.g. a triangle or H&S).
- **Confirm:** trendline-family boundaries follow a **converging envelope with outlier tolerance** (a spike pivot no longer drags the line into a big "V"); the two boundaries **extend to a clipped apex**; a **confirmed** breakout draws an arrowhead segment (forming shows apex + dashed, no arrow).

### B6 · Plan 0071 — candlestick legend declutter
- **Action (human):** open a chart with candlestick patterns detected.
- **Confirm:** markers **draw on select** (no 100-marker wall); the grouped legend is clean; selecting a pattern draws only its markers.

### B7 · Plan 0065 — glossary hover explanations
- **Action (human):** hover the glossary-marked terms on the advisory/analysis panels.
- **Confirm:** an informational tooltip appears (no-action, disclosure only); no interactive control is added to the panel.

---

## Part C — secrets-gated (DeFi on-chain)

Skip (do **not** fail) if the Alchemy key / RPC / full wallet address are not provisioned.

### C1 · Plan 0087 — DeFi P&L wallet-total gap
- **Setup:** provision `alchemy_prices_key`; point `base_rpc_url`/`eth_rpc_url` at Alchemy; restart the sidecar.
- **Run:** `compute_wallet_pnl {address: <full 0xae5b…9790>, refresh: true}`.
- **Pass:** **5/5** positions complete; **non-null** wallet totals; the `0xef0fd52e…` Wanderers token prices via Alchemy; custody transfers book as no-ops; `unclaimed_rewards` reads real from `earned()`. *(A null on that token's Alchemy coverage is a documented finding — re-open the price-source decision — not a phase failure.)*

### C2 · Plan 0079 — cross-pool arb BA-7 evidence
- **Run:** `scan_pool_discrepancies {pairs: ["WETH/USDC"], trade_size: <size>}` on the configured Base pools.
- **Pass:** ranked observations with `net_spread = gross - gas - slippage - fees` (never a bare gross), sub-threshold ones flagged `capturable_at_threshold=false` (not dropped), each carrying the `capturability_note` (RPC-poller = upper bound). **A null / no-capturable-edge result is the documented success**, written to `runs/defi/` as BA-7 evidence.

---

## Results ledger

### Run 1 — 2026-07-12 (agent-driven Part A + emitted Part B)

Part A was driven live against the running sidecar; every agent-callable check passed. Part B visuals were **emitted to the viewer** (chart events published) and await a human eyeball. Part C is blocked pending the Alchemy key + RPC + the full test-wallet address.

| Plan | Surface | Part | Verdict | Evidence / note |
|------|---------|------|---------|-----------------|
| 0074 | technical_read + banner | A1 / B2 | ✅ **pass** (A1); ⬜ eyeball (B2) | BTC-USD 1d: supertrend=short, ema_stack=flat, macd=long, ichimoku=flat — each with regime_state + mechanical rule, no conviction/levels. Banners emitted via `technical_read.completed`. |
| 0078 | convergence screener | A2 | ✅ **pass** | `"bitcoin"` → 26 near-resolution markets; `implied_return_if_right=(1-p)/p`, resolution_risk {level,reasons}, liquidity_caution (thin-book $ flagged), capital_lockup_note; **no** direction/size/action field; nothing reads as a buy call. |
| 0089 | market_url + link + sort | A3 / B4 | ✅ **pass** (A3); ⬜ click (B4) | Every opportunity carried `https://polymarket.com/event/<slug>`; ordered largest→smallest by return (0.047→0.042→0.023→…). Link-opens-browser is the human click. |
| 0080 | advisor track record | A4 | ✅ **pass** | `get_track_record`: n=3, sufficient=false (min_n=20), baseline (buy-and-hold) + reliability buckets present, 3 path-dependent scored calls (stopped/timeout, realized R). Honest small-n. |
| 0077 | vol/regime + advisor non-voting | A5 | ✅ **pass** | BTC-USD 1d vol: pred 0.0242, 1σ band, `beats_baseline=false`→baseline surfaced; regime: current `sideways_quiet`, next-period dist, `beats_baseline=false` vs persistence (both honest nulls, full OOS validation + tier provenance). `recommend` checks show vol+regime legs `gating:false` (non-voting). **Go/no-go: keep wiring active** (non-voting, cannot corrupt a call). |
| 0066 | advisor tiered-forecast unification | A6 | ✅ **pass** | `recommend BTC-USD supertrend` → honest `flat` / no-actionable-edge; forecast leg rides the tiered path (`feature_set_id=49c020…`, fallback_reason names v2-full/deep→v1); full fusion trace + reason_codes present. |
| 0088 | DeFi P&L windowed | A7 | ⬜ blocked | Needs the full `0xae5b…9790` address (+ Part C config). Re-confirm windows/partial/is_lp on the run. Already reported passed at close. |
| 0076 | OBV strip | B1 | ⬜ eyeball | `show_chart BTC-USD 1d obv` emitted (`chart.show` published). Confirm the strip renders in its own pane. |
| 0082 | Bollinger form | B3 | ⬜ human | Pure UI interaction (add bbands from the form, reload, survive agent redraw). |
| 0083 | chart-pattern fidelity | B5 | ⬜ eyeball | `detect_chart_patterns BTC-USD 1d` emitted **55 hits** (`chart.trendlines`) — wedges/H&S/triangles/double-bottoms; confirmed hits carry a `projection` apex line. Confirm envelope anchors + apex + confirmed-only arrow. |
| 0071 | candlestick legend declutter | B6 | ⬜ eyeball | `scan_patterns BTC-USD 1d` emitted **222 markers** (`chart.highlight`). Confirm draw-on-select, no marker wall, clean grouped legend. |
| 0065 | glossary hover | B7 | ⬜ human | Hover glossary terms on the advisory/analysis panels; confirm informational tooltip, no interactive control. |
| 0087 | DeFi P&L wallet-total gap | C1 | ⬜ blocked | Needs `alchemy_prices_key` + Base/ETH RPC + full wallet address → 5/5 complete, non-null totals. |
| 0079 | cross-pool arb BA-7 evidence | C2 | ✅ **pass** (documented null) | `scan_pool_discrepancies WETH/USDC` ran clean (no error), empty observations + the RPC-upper-bound `capturability_note`. Null / no-capturable-edge is the documented BA-7 success. |

**Still open after Run 1:** B1/B2/B5/B6 (emitted → your eyeball), B3/B4/B7 (human interaction), C1 + A7 (need Alchemy key + RPC + full wallet address). Everything else is confirmed passing.

**Write-back:** once B/C are ticked, a follow-up architect touch folds each verdict into the corresponding `done/NNNN-*.md` close notes and the [`plans/README.md`](plans/README.md) recently-closed rows (replacing "the user's outstanding step" with the recorded result); this ledger is the source for that pass.
