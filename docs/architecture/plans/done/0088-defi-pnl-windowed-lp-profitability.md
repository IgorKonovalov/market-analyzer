# 0088 — DeFi P&L: per-LP time-windowed profitability + partial wallet totals

> **Status:** done — closed 2026-07-12 (ADR-0082 accepted at close). Five phases on `main`, no branch, migration-free, no new dep, no new tool/route: `dev` ph1 `6a5d169` (partial wallet totals over complete positions — `partial`/`incomplete_position_count`, incomplete excluded not zeroed, reverses ADR-0036's any-incomplete⇒null) → `dev` ph2 `bef5541` (per-position 7d/30d/90d/all **exact** realized from per-event `mined_at` deltas, anchored to an injected per-run `now` the job captures once — engine never reads the clock) → `dev` ph3 `6707bdd` (per-window **labeled estimated** total-return via a contributed-lot mark at window-start block-time prices; `None` per-window when unpriceable — an honest gap that never marks the position incomplete or touches its exact realized) → `dev` ph4 `9240e83` (`is_lp` + stable LP-first ordering; non-LP incomplete never suppresses the LP view; apiref regen) → `ui-builder` ph5 `eaeaea5` (new top-level DeFi tab: paste-a-wallet read-only Wallet P&L view, `getWalletPnl` client + `useWalletPnl` hook, partial banner, LP-first table with muted estimated sub-row + `—` for `None`, recent-address chips). Clean Mode 4 — **no blockers/majors**; two non-blocking doc items (a stale claim-(e) header in `tests/defi/test_pnl.py:14-16` describing the old null-total rule; a theoretical `mined_at==cutoff` boundary nit, effectively impossible at block-second vs microsecond-`now` and labeled estimated regardless). Every done-when read at the assertion level: ph1 pins the partial sum equals the hand-summed complete-only total (not `None`, not a `0` for the incomplete leg), `partial`/count, no-regression on all-complete, and the incomplete-contributes-nothing removal test; ph2 pins the hand-worked `{7d:10,30d:30,90d:70,all:150}` bucket split with `all==realized_usd`, byte-identical with fixed `now`, empty windows on an incomplete position, and a source-grep that the engine never reads the wall clock; ph3 pins the hand-computed total-return per window (`7d:30/30d:40/all:40`), the unpriceable-window-start → `total_return=None` with `realized` and `incomplete=False` intact, and the `estimated` label; ph4 pins LP-first order + correct `is_lp` + a Wanderers-shaped non-LP incomplete leaving the LP figures + partial total intact. Gates re-verified on `main` at close: **886 Python** passed + 5 known Windows POSIX skips, **18 renderer jest** (`useWalletPnl`/`defiWallets`/`DefiPnlView`), and `apiref` regenerates with **zero drift**. The `portfolio_summary` caller was correctly updated to pass `now=as_of` (discards windows, keeps its basis replay deterministic). Implemented directly in this working tree — no branch/worktree to merge or prune. **Phase 6 (`human` live smoke on `0xae5b…9790`) reported passed by the user:** the windowed LP figures + non-null `partial` total (excluding the one incomplete Wanderers position) reconstruct as designed. **Plans-index + ADR-index refresh DEFERRED** at close: a concurrent architect session held both README indexes + new plan/ADR files (0090/0091/0092, 0083/0084) uncommitted in the shared tree, so refreshing them here would have absorbed that session's work — the index update is owed once that session lands. Followups (from the plan): configurable/arbitrary-range windows; a keyed price source for the `0xef0fd52e…` Wanderers token (carried from Plan 0087) to let it complete and re-enter the total; reconstruct historical CL composition to make the estimated total-return exact.
>
> **Created:** 2026-07-12
> **Owner skill(s):** dev, ui-builder, human
> **Related ADRs:** [ADR-0082](../adrs/0082-defi-pnl-partial-totals-and-windowed-lp-profitability.md) (paired — amends [ADR-0036](../adrs/0036-defi-pnl-transaction-replay.md); builds on [ADR-0079](../adrs/0079-defi-pnl-gauge-swaps-unclaimed.md)/[ADR-0081](../adrs/0081-defi-pnl-wallet-total-gap.md))

## TL;DR

The DeFi P&L tool reconstructs LP positions well (Plans 0084/0087) but answers the wrong question and hides good data: it reports **all-time** figures only, and one unpriceable exotic token nulls the entire wallet total. The user's primary need is *per-LP profitability over a time window*. This plan makes `compute_wallet_pnl` report, per position, **realized P&L over 7d/30d/90d/all** (exact) plus a **labeled estimated total-return** over the same windows, and replaces the null-everything rule with an honest **partial total** (sum over complete positions, flagged). LP positions are the headline; non-LP positions (including the unpriceable Wanderers) are muted and never suppress the LP view. First user-visible behavior: `POST /defi/pnl` on `0xae5b…9790` returns each LP position's 7/30/90/all realized figures and a non-null `partial` wallet total that excludes the one incomplete position.

## Context & problem

Per [ADR-0082](../adrs/0082-defi-pnl-partial-totals-and-windowed-lp-profitability.md): a live smoke (2026-07-12) showed 4/5 positions reconstruct cleanly, but (a) the fifth — a non-LP "Wanderers" position on unpriceable token `0xef0fd52e…` — nulls the wallet `realized_usd`/`unrealized_usd` under ADR-0036's "any incomplete ⇒ null" rule, and (b) the tool has no time-windowed view, while the user's stated priority is LP-position profitability *within a certain amount of time*. Zeroing the unpriceable leg is unsafe (ADR-0036 loud-failure); the fix is a partial total plus windowed per-position views. The exact windowed-realized is tractable from the existing replay (`PositionEvent.mined_at` + the per-event realized accumulation); total return over a window needs a window-start mark and is inherently estimated for concentrated-liquidity positions.

## Decision

Extend `compute_wallet_pnl` (not a new tool) per ADR-0082:

1. Partial wallet totals over complete positions + `partial`/`incomplete_position_count` — never zero.
2. Per-position rolling-window **realized** (7d/30d/90d/all), exact, anchored to a per-run `now` input.
3. Per-position rolling-window **estimated total return** over the same windows, labeled, honest per-window gap when the window-start mark is unpriceable.
4. `is_lp` signal + LP-first ordering; non-LP positions muted and never suppressing LP figures.

We rejected zeroing the leg, the null-everything status quo, a separate tool, and anchoring windows to the last-tx `as_of` (rationale in ADR-0082).

## Architecture diagram

```mermaid
flowchart LR
    subgraph sidecar[Python sidecar]
        JOB[pnl_job.run_wallet_pnl<br/>captures `now` once] --> ENG
        subgraph ENG[compute_wallet_pnl — ADR-0036 replay]
            REPLAY[per-position replay<br/>emits per-event realized deltas] --> WIN[window bucketer<br/>7d/30d/90d/all vs `now`]
            REPLAY --> RET[estimated total-return<br/>contributed-lot mark @ window-start prices]
            PRICE[HistoricalPriceSource] --> RET
        end
        WIN --> OUT[PositionPnl.windows<br/>realized + estimated total-return + is_lp]
        RET --> OUT
        OUT --> ROLL[WalletPnl<br/>partial total over complete positions + flags]
    end
    ROLL --> TOOL[compute_wallet_pnl tool + POST /defi/pnl]
    TOOL --> UI[renderer: LP-headline table<br/>7/30/90/all columns + muted others]
```

## Implementation phases

### Phase 1 — Partial wallet totals (never null-everything)
- **Owner skill:** dev
- **What:** Replace ADR-0036's "any incomplete ⇒ null total" with a partial total. `WalletPnl.realized_usd`/`unrealized_usd` = sum over the **complete** positions only; add `partial: bool` (true iff any position is incomplete) and `incomplete_position_count: int`. An incomplete position is excluded from the sum, never zeroed. Mirror the new fields onto `PnlResponse` (route) and the tool output; regenerate `docs/reference/`.
- **Files touched:** `src/market_analyser/defi/pnl.py` (`WalletPnl` model + the roll-up in `compute_wallet_pnl`, currently lines ~154-166), `src/market_analyser/api/routes/defi.py` (`PnlResponse`), `src/market_analyser/api/mcp_tools/compute_wallet_pnl.py`, `docs/reference/` (regen), `tests/defi/test_pnl.py`, `tests/api/test_pnl_route.py`, `tests/api/test_compute_wallet_pnl_tool.py`.
- **Done when:** a wallet with 1 incomplete + N complete positions returns `realized_usd`/`unrealized_usd` = the sum over the N complete ones (asserted equal to the hand-summed value, **not** `None`, **not** including a `0` for the incomplete one), `partial=true`, `incomplete_position_count=1`; a fully-complete wallet returns `partial=false` and the same totals as before this change (no regression to the all-complete path); the determinism golden still re-runs byte-identical. A test asserts the incomplete position contributes nothing (removing it from a complete wallet leaves the total unchanged).

### Phase 2 — Per-position rolling-window realized P&L (exact)
- **Owner skill:** dev
- **What:** Add a `now: datetime` analysis anchor to `compute_wallet_pnl`, captured once by `run_wallet_pnl` (a wall-clock read at analysis time, passed as an input — never read inside the engine, mirroring `as_of`). During replay, emit a per-event **realized delta** (the fee/reward claim value; the `extracted - released` on an extraction; the block-time execution delta on a swap), each tagged with the event's `mined_at`. Add a `windows` structure to `PositionPnl` reporting realized P&L for `7d`/`30d`/`90d`/`all`, where a window's figure sums the deltas whose `mined_at ≥ now - window` (`all` = every delta). Exact and deterministic given `(events, now)`.
- **Files touched:** `src/market_analyser/defi/pnl.py` (`PositionPnl` gains `windows`; `_replay_position` emits per-event realized deltas; `compute_wallet_pnl` gains `now`), `src/market_analyser/defi/pnl_job.py` (capture + pass `now`), `src/market_analyser/api/routes/defi.py`, `src/market_analyser/api/mcp_tools/compute_wallet_pnl.py`, `docs/reference/` (regen), `tests/defi/test_pnl.py`, `tests/defi/test_pnl_job.py`.
- **Done when:** a hand-built position with realized events dated across several months reports per-window realized figures that match the hand-summed deltas for each of 7d/30d/90d/all relative to a **fixed** `now` (worked on paper in the test); the `all` window equals the existing all-time realized; a re-run with the same `now` is byte-identical (`model_dump_json` equality with `now` fixed). The engine never reads the wall clock (a grep/test asserts `now` flows only as an argument).

### Phase 3 — Per-position rolling-window estimated total return (labeled)
- **Owner skill:** dev
- **What:** For the same windows, add an **estimated** total-return figure per window: `realized_in_window + (unrealized_now − unrealized_at_window_start)`, where `unrealized_at_window_start = value(contributed lots as of window-start) at window-start block-time prices − basis as of window-start`. Compute the window-start basis + contributed lots by replaying events with `mined_at ≤ window_start`. Every total-return figure is tagged `estimated`; a window whose start-mark cannot be priced reports `None` for that window's total-return (an honest per-window gap that does **not** set the position `incomplete` and does **not** affect its exact realized figures). Keep it inside the byte-identical guarantee (deterministic given `now` + price snapshots).
- **Files touched:** `src/market_analyser/defi/pnl.py` (the window structure gains `total_return_usd` + an `estimated` marker; a window-start mark helper), `tests/defi/test_pnl.py`.
- **Done when:** a fixture LP position with a known contributed history and a priced window-start reports a total-return equal to the hand-computed `realized_in_window + unrealized drift` for that window; a window whose start-mark token is unpriceable reports `total_return_usd=None` for that window while its `realized` figure and the position's `incomplete=False` are unaffected; the figure is labeled estimated in the model; determinism holds with fixed `now`.

### Phase 4 — LP-first reporting + surfacing
- **Owner skill:** dev
- **What:** Add `is_lp: bool` to `PositionPnl` (derived from `position.kind == "lp"`), and order the response with LP positions first (stable, deterministic order). Ensure the tool description + route docs explain the windows, the estimated-total-return label, and the partial-total flags; regenerate the apiref. Confirm the full-toolset registration + route contract tests still pass with the widened shape.
- **Files touched:** `src/market_analyser/defi/pnl.py` (`is_lp` + ordering), `src/market_analyser/api/mcp_tools/compute_wallet_pnl.py` (tool description), `src/market_analyser/api/routes/defi.py`, `docs/reference/` (regen via the apiref generator), `tests/api/test_pnl_route.py`, `tests/api/test_compute_wallet_pnl_tool.py`.
- **Done when:** the response lists LP positions before non-LP ones deterministically; `is_lp` is correct per position; a non-LP incomplete position (the Wanderers shape) leaves every LP position's figures and the partial total intact; `docs/reference/` regenerates clean (apiref gate green); the full-toolset count test still passes.

### Phase 5 — Renderer view: paste-a-wallet DeFi P&L (new "DeFi" tab)
- **Owner skill:** ui-builder
- **What:** The first DeFi renderer surface — a new top-level **"DeFi"** tab (room to grow: Wallet P&L is its first panel, with space reserved for future pool/health views) containing a read-only **Wallet P&L** view. This is a renderer-originated **read-only lookup**, consistent with the existing `SymbolPicker` → OHLCV fetch precedent (not a control/execution action; no new ADR — it does not cross ADR-0015, mirroring `api.getQuote`/`scanPatterns`). Design (settled with the user 2026-07-12):
  - **Address input:** a paste box for a `0x…` EVM address + an **Analyze** button, with client-side address validation (`^0x[0-9a-fA-F]{40}$`, matching the route's `EVM_ADDRESS_PATTERN`). Remember the **last ~5 addresses** as quick-pick chips persisted in `localStorage['ma.defiWallets']` (ADR-0039 `ma.*` convention). A **refresh** control issues `refresh=true` (re-pull from Zerion, slower) vs the default cached replay.
  - **Fetch:** add `api.getWalletPnl({ address, refresh })` → `POST /defi/pnl` to `desktop/renderer/api/client.ts` (same shape as the existing `scanPatterns` POST); a `useWalletPnl` hook with loading / error / empty states. Map the typed error bodies to actionable messages — `no wallet-positions source configured` / `no historical price source configured` → point the user to Settings (set the Zerion key); `agent mode is off` if the route gates on it.
  - **Display:** a **partial banner** ("Partial — N of M positions excluded (couldn't be priced)") shown only when `partial=true`; a totals summary line (partial `realized_usd`/`unrealized_usd` + "K complete"); an **LP-first table** with columns `Position | 7d | 30d | 90d | all | unclaimed`, each LP position followed by a **muted `est. return` sub-row** carrying the labeled estimated total-return per window (parenthesized; render `—` where a window's estimate is `None`); a **muted "Other"** section listing non-LP positions with their incomplete reason. Exact realized figures are visually dominant; the estimated sub-row is clearly secondary.
- **Files touched:** `desktop/renderer/api/client.ts` (`getWalletPnl` + `PnlResponse`/window types via the generated `types/sidecar/*`), `desktop/renderer/hooks/useWalletPnl.ts` (new), `desktop/renderer/views/DefiPnlView.tsx` (+ `.module.css`, new), a small recent-addresses lib (`desktop/renderer/lib/defiWallets.ts`), `desktop/renderer/App.tsx` (register the DeFi tab), locales (`en`/`ru` strings), renderer tests for each.
- **Done when:** pasting a valid address fetches and renders from a fixture response; an invalid address is rejected client-side before any fetch; recent addresses persist and re-analyze on click; the LP table renders 7d/30d/90d/all realized with the muted estimated-total-return sub-row (and `—` for a `None` window), non-LP rows muted in the Other section, and the partial banner + excluded count appear only when `partial=true`; a `no wallet-positions source configured` error renders the actionable Settings hint, not a raw 500; renderer test suite green. (The MCP tool output remains the primary agent surface per ADR-0015; this view is the human paste-and-scan surface.)

### Phase 6 — Human live smoke
- **Owner skill:** human
- **What:** Run `POST /defi/pnl` (with `refresh=false` after the sidecar reload) on `0xae5b…9790` and confirm the windowed LP figures + partial total against the known reconstruction.
- **Files touched:** none (verification).
- **Done when:** each LP position reports 7d/30d/90d/all realized figures (the `all` matching the current all-time value); the wallet reports a **non-null `partial` total** excluding the one incomplete Wanderers position (`incomplete_position_count=1`); the estimated total-return is present and labeled where the window-start priced, `None` where it didn't; the Wanderers position no longer nulls the LP view. Record the verdict in the plan close notes.

## Data shapes

```python
# illustrative — not the final interface
Window = Literal["7d", "30d", "90d", "all"]

class WindowPnl(BaseModel):
    window: Window
    realized_usd: float            # exact, from per-event deltas in the window
    total_return_usd: float | None # ESTIMATED (realized + unrealized drift); None if window-start unpriceable
    estimated: bool                # always True for total_return_usd; labels the estimate

class PositionPnl(BaseModel):
    position_id: str
    is_lp: bool                    # NEW — headline vs muted
    realized_usd: float | None     # all-time (unchanged)
    unrealized_usd: float | None
    cost_basis_usd: float | None
    vs_hodl_usd: float | None
    windows: list[WindowPnl]       # NEW — 7d/30d/90d/all
    unclaimed_rewards: list[RewardAmount] | None
    incomplete: bool
    notes: list[str]

class WalletPnl(BaseModel):
    # ... realized_usd/unrealized_usd now SUM OVER COMPLETE positions (partial), never null-everything
    partial: bool                  # NEW — true iff any position incomplete
    incomplete_position_count: int # NEW
```

## Risks & open questions

- Risk: **the estimated total-return misread as exact.** Mitigation: the `estimated` flag on every figure, per-window `None` when unpriceable, prominent UI marking; the exact windowed-realized is the headline. If review deems it too approximate, phase 3 is cuttable without touching 1/2/4.
- Risk: **CL composition at a past date is unmodeled**, so the window-start mark uses contributed lots, not the true on-chain amounts — the estimate diverges for out-of-range positions. Documented in ADR-0082; the label carries the honesty.
- Risk: **determinism relaxation.** Windowed figures are deterministic only given the run's captured `now`. Mitigation: `now` is an injected input (never a wall-clock read in the engine); the cross-process golden fixes `now`. Same category as `usd_value`.
- Risk: **`now` vs `as_of` confusion.** They are different anchors (`now` = analysis time for windows; `as_of` = last-tx time for the vs-HODL mark). The plan keeps both; a comment/test pins the distinction.
- Open question: are 7/30/90/all the right windows, or should the set be configurable? Fixed set first (matches the chosen "standard rolling set"); a configurable window is a possible follow-up.

## What this plan does NOT do

- **A configurable/arbitrary date-range window** — fixed 7/30/90/all only (the chosen shape); explicit ranges are a follow-up.
- **Exact total return** — impossible without historical CL composition; the windowed total-return is a labeled estimate (ADR-0082).
- **A new price source for the Wanderers token** — `0xef0fd52e…` stays unpriceable; this plan makes it *not block* the LP view, it does not price it (that remains the separate follow-up from Plan 0087).
- **Any execution / rebalance action** — read-only, per ADR-0025/0029.
- **A persisted-schema/migration change** — the tx cache + price snapshots are unchanged; this is a response-shape enrichment only.

## Followups (after this lands)

- Configurable/arbitrary-range windows if the fixed set proves limiting.
- A third/keyed price source for exotic tokens like `0xef0fd52e…` (carried from Plan 0087) — would let the Wanderers position complete and re-enter the total.
- If the estimated total-return proves valuable, revisit whether historical CL composition can be reconstructed (subgraph / on-chain event log) to make it exact.
