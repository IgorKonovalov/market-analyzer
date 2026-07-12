# ADR-0082 — DeFi P&L: partial wallet totals + per-LP time-windowed profitability

> **Status:** proposed (accepts at Plan 0088 close)
> **Date:** 2026-07-12
> **Related plan(s):** [Plan 0088](../plans/0088-defi-pnl-windowed-lp-profitability.md) (implements this end to end)
> **Amends:** [ADR-0036](0036-defi-pnl-transaction-replay.md) (DeFi P&L by transaction replay) — on two points: the "any incomplete position ⇒ null wallet total" rule, and the "all-time, exact-or-honest-gap" figure set. Does not supersede it — block-time pricing, average-cost lots, determinism-by-snapshot, and loud-failure-never-zero all stand. Relates to [ADR-0079](0079-defi-pnl-gauge-swaps-unclaimed.md)/[ADR-0081](0081-defi-pnl-wallet-total-gap.md) (the completeness work this builds on).

## Context

`compute_wallet_pnl` (ADR-0036) reconstructs per-position and whole-wallet DeFi P&L, all-time, under two strict rules: every figure is either exact or `None` (never estimated, never zeroed), and **any single incomplete position nulls the whole-wallet total**. The completeness work (Plans 0084/0087) made the common LP shapes reconstruct, but a live smoke on the test wallet (`0xae5b…9790`) exposed two mismatches with how the tool is actually used:

1. **One unpriceable non-LP token nulls everything.** A "Wanderers" position holds a long-tail token (`0xef0fd52e…`) that neither DefiLlama nor Alchemy can price (HTTP 400 / no coverage). Under the null-total rule, that one exotic position suppresses the wallet's `realized_usd`/`unrealized_usd` even though all four **LP** positions — the ones the user cares about — reconstruct cleanly. The per-position numbers are all present; only the roll-up is `null`.

2. **The tool answers the wrong question.** The user's stated primary need is *"reconstruction of LP positions and profitability of those positions within a certain amount of time."* ADR-0036 delivers **all-time** realized/unrealized only. There is no "how did this LP do in the last 30 days" view, which is the actual decision-support question for an active farmer.

The tempting fix for (1) — treat an unpriceable leg as `0` — is wrong: not being able to price a token is exactly why we cannot know whether it is $10 of dust or $5,000 of value, so zeroing risks a large **silent** error, the precise failure ADR-0036's "loud failure, never zero" exists to prevent. The honest fix is a *partial* total that excludes the incomplete position and says so.

For (2), the tool already carries what the exact part needs — every `PositionEvent` has a `mined_at`, and the replay already accumulates realized P&L per event — so realized-within-a-window is tractable and exact. A *total return* over a window (which captures impermanent loss + fee accrual + price drift on still-open holdings) additionally needs the position's holdings **marked at the window's start**, and for a concentrated-liquidity LP the historical on-chain composition at a past date is not available (discovery gives only the current composition; the replay tracks average-cost basis lots, not the CL rebalanced amounts). So total return is inherently an **estimate**, not an exact figure.

One determinism constraint shapes the window anchor. The job sets `as_of = history[-1].mined_at` (the newest cached transaction) for byte-identical re-runs — but "the last 30 days" must mean 30 **calendar** days, not "30 days before the last transaction." Rolling windows therefore need an analysis-time `now` anchor, which cannot be the last-tx `as_of`.

## Decision

We amend ADR-0036 on two points and add the windowed views, keeping every other invariant intact.

1. **Partial wallet totals with provenance (never zero).** `realized_usd`/`unrealized_usd` become the sum over the **complete** positions only, carried alongside a `partial: bool` and an `incomplete_position_count`. An incomplete position contributes nothing to the total (it is excluded, not zeroed) and the flag makes the exclusion explicit. A fully-complete wallet reports `partial=false` exactly as before. This reverses "any incomplete ⇒ null total" while preserving "never fabricate a value for a leg we cannot price."

2. **Per-position rolling-window realized P&L (exact).** For a fixed set of windows — **7d / 30d / 90d / all-time** — each position reports the realized P&L attributable to the events dated inside the window. The replay emits a per-event realized delta (fee/reward claims, and the realize-on-exit delta of a remove/swap), each tagged with the event's `mined_at`; a window's realized figure is the sum of the deltas whose `mined_at` falls within it. This is exact and deterministic — no new pricing, no estimation.

3. **Per-position rolling-window total return (estimated, labeled).** For the same windows, each position reports a total-return figure = `realized_in_window + (unrealized_now − unrealized_at_window_start)`, where `unrealized_at_window_start` marks the replay's contributed lots at the window-start block-time prices against the basis as of that time. It is **labeled `estimated`** and is `None` for any window whose start-mark cannot be priced (an honest per-window gap that does **not** block the position). This is a deliberate, clearly-flagged departure from ADR-0036's exact-or-gap stance: an estimate is neither exact nor a gap, so it must never be presented unlabeled.

4. **Windows anchor to a per-run `now` input.** `compute_wallet_pnl` gains a `now: datetime` analysis anchor, captured **once** by the job (a wall-clock read at analysis time, treated as a run input in the same category as `as_of` and discovery's `usd_value`) and never read inside the engine. Windows are computed relative to `now`, so re-running the *same* run reproduces byte-identical windowed figures, while a *later* analysis naturally sees the windows advance with calendar time. The windowed figures sit in the same "current-state, anchored to analysis time" category as `usd_value`/`unclaimed_rewards` — deterministic given the run's inputs, not part of the cross-time byte-identical guarantee.

5. **LP-first reporting.** Each position exposes an `is_lp` signal (derived from its `kind`) and the report orders LP positions first. The headline is the LP positions' reconstruction + windowed profitability; non-LP positions (lending, loose tokens, unpriceable exotics like the Wanderers token) are still reported but de-emphasized, and — because of the partial-total rule — an incomplete **non-LP** position never suppresses the LP figures.

## Consequences

**Positive:**
- The tool answers the user's actual question: per-LP realized profit over 7d/30d/90d/all, with a labeled total-return estimate layered on top.
- One unpriceable exotic position no longer hides a fully-reconstructed portfolio; the wallet total is usable and honestly flagged partial.
- The exact windowed-realized figures reuse the existing replay and pricing — no new data source, no migration.

**Negative / the price we pay:**
- **An estimated figure enters a tool that was exact-or-null.** Read as exact, a total-return estimate could mislead — the CL composition at a past date is not modeled, so the estimate diverges from the true value for out-of-range positions. Mitigated by the mandatory `estimated` label and per-window honest gaps; if review finds it too approximate to be useful, it is a cuttable phase (the exact windowed-realized stands alone).
- **A second determinism category.** Windowed figures are deterministic given the run's `now`, but not across calendar time — the same relaxation `usd_value` already carries. The engine still forbids reading the clock; `now` is an injected input. The cross-process golden pins `model_dump(exclude=…)` with a **fixed** `now`.
- **A wider response surface.** `PositionPnl` gains per-window realized + estimated-total-return + `is_lp`; `WalletPnl`/`PnlResponse` gain `partial`/`incomplete_position_count`. Documented via the apiref regen.
- **Partial totals can be misread as complete.** Mitigated by the explicit `partial` flag and count, which the tool description and UI surface prominently.

## Alternatives considered

- **Treat an unpriceable leg as `0`.** Rejected: it silently includes the position with a fabricated value, risking a large hidden error exactly when the token is not dust — the failure ADR-0036's loud-failure rule exists to prevent.
- **Keep "any incomplete ⇒ null total."** Rejected: it hides a fully-reconstructed portfolio behind one exotic dust position, defeating the tool for the real use case.
- **A separate dedicated LP-report tool.** Rejected: it would duplicate the replay; the windowed views are an enrichment of the same reconstruction, so we extend `compute_wallet_pnl`.
- **Anchor windows to `as_of` (last-tx time).** Rejected for total return: "last 30 days" must be calendar time, and the current-value drift since the last transaction would be invisible. (Realized-in-window alone would tolerate an `as_of` anchor, but the total-return layer forces a calendar `now`, so both use it for consistency.)
- **Exact total return (no estimation).** Rejected: impossible without historical on-chain CL composition, which we do not have; an honest labeled estimate beats no windowed-return view at all.
