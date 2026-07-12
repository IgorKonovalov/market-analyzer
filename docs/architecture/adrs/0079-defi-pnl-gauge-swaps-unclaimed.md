# ADR-0079 — DeFi P&L completeness: gauge-event resolution, swap booking, and a bounded unclaimed-reward augmentation

> **Status:** accepted (Plan 0084 close 2026-07-12)
> **Date:** 2026-07-11
> **Related plan(s):** [Plan 0084](../plans/done/0084-defi-pnl-gauge-swap-completeness.md) (implements this end to end)
> **Refines:** [ADR-0036](0036-defi-pnl-transaction-replay.md) (DeFi P&L by transaction replay) — does not supersede it; the taxonomy, block-time pricing, average-cost lots, determinism, and loud-failure principles all stand. This ADR closes three v1 gaps ADR-0036 named or deferred.

## Context

ADR-0036 shipped `compute_wallet_pnl` as a transaction-replay engine and was accepted with its live smoke reconstructing **1 of 5** positions, the other four honestly `incomplete`. A re-run of that tool on 2026-07-11 against the same test wallet (`0xae5b…9790`) reproduces the state unchanged: **4 of 5 positions incomplete**, wallet totals `null`, with the position notes breaking down as **35 "unbooked unclassified event"**, **23 "unbooked swap event"**, and **1 missing block-time price**. A parallel manual reconstruction (Zerion tx-history + Base RPC `eth_call`) recovered the real numbers by hand for the wallet's Aerodrome AERO/WETH and AAVE/WETH positions, which tells us exactly what the engine is missing.

Three distinct forces:

1. **Gauge indirection breaks the position join, not the vocabulary.** The classifier (`pnl_events.py`) already lists `getreward`/`claimrewards` as `reward_claim` methods and `stake`/`unstake` as add/remove hints. The events still fail because a gauge `getReward` transaction carries `contract_address = <gauge>`, which is **not** the position's `pool_address` (join rule step 1 fails), and its AERO-only inbound transfer is contained by **several** of the wallet's positions at once (AERO/WETH ×2, USDC/AERO, plus loose AERO), so the token fallback is ambiguous and joins nothing — or joins wrong and surfaces as `unclassified`. Aerodrome routes emissions through a per-pool **gauge** contract distinct from the pool; without a gauge→pool mapping the replay cannot attribute a reward to the position that earned it. This is the dominant bucket (35 events) and is where the *rewards* live.

2. **Swaps were deliberately left unbooked in v1.** ADR-0036 classified `swap` but the engine (`pnl.py`) puts `swap`/`liquidation`/`unclassified` in the loud-fail set rather than inventing arithmetic under deadline. A single unbooked swap fails its whole position. Concentrated-liquidity entry/exit routes value through swaps (zap in/out), so any active LP lifecycle contains them.

3. **One long-tail token has no DefiLlama block-time price**, and under ADR-0036's "any incomplete position ⇒ null wallet total" rule, one unpriced leg suppresses the entire wallet figure.

Separately, the manual run surfaced a capability the replay model **structurally cannot provide**: **currently-accrued-but-unclaimed** gauge emissions (read on-chain via the CL gauge's `earned(account, tokenId)`), e.g. 34.2 AERO still owed on the open AAVE/WETH position. There is no claim transaction for unclaimed rewards, so tx-replay is blind to them — yet they are real, owed value and the first question a user asks of an open farming position.

## Decision

We will close the three replay gaps and add one bounded current-state augmentation, keeping every ADR-0036 invariant intact:

1. **Gauge→pool resolution as a first-class classification input.** We introduce an on-chain, snapshot-cached gauge-resolution seam (gauge address → pool address, reusing the existing `lp_detail.py` RPC path and the same determinism-by-snapshot discipline as the price source). The classifier consults it so a gauge transaction joins the pool position it belongs to; `getReward` then books as `reward_claim` and stake/unstake NFT transfers book as a position custody move with no basis change. Resolution is precision-first: an unresolved gauge still yields an honest `unclassified`, never a guess.

2. **Swaps are booked as average-cost lot conversions.** A `swap` realizes P&L on the sold leg against its average cost and opens a new lot on the bought leg at block-time value — the principled accounting v1 deferred. `swap` leaves the loud-fail set; `liquidation` and genuinely `unclassified` events remain loud failures.

3. **A fallback historical-price source sits behind DefiLlama** under the existing `HistoricalPriceSource` Protocol, snapshot-cached identically, consulted only when the primary returns no price for a (token, block-time) key. Determinism is preserved because the merged result is snapshotted.

4. **A bounded unclaimed-reward augmentation.** `compute_wallet_pnl` gains a clearly-separated, current-state `unclaimed_rewards` field per position (and a wallet roll-up), read on-chain via `earned()` at analysis time. It is **not** mixed into realized/unrealized replay figures and is **not** part of the deterministic byte-identical guarantee — it is a "now" read tagged as provenance, in exactly the same category as discovery's current `usd_value` that ADR-0036 already consumes for unrealized P&L. This keeps the replay core pure while answering the open-position question honestly.

We rejected leaving swaps/prices as accepted incompletes (the narrower option) because one unbooked swap or unpriced leg nulls the whole wallet total, defeating the tool for any active farmer. We rejected computing unclaimed rewards inside the replay (there is no event to replay) and instead isolate it as a labeled current-state field.

## Consequences

**Positive:**
- `compute_wallet_pnl` returns real numbers for gauge-staked Aerodrome positions — the dominant real-world DeFi shape on Base — instead of honest-but-useless `null`.
- Rewards are attributed to the correct position via a real gauge→pool mapping, not a token-set guess, preserving ADR-0036's precision-over-plausibility stance.
- Open farming positions report owed-but-unclaimed emissions, a question replay alone can never answer.
- The average-cost engine becomes swap-complete, so P&L reconciles across zaps.

**Negative / the price we pay:**
- The classifier now has an **I/O dependency** (gauge resolution) where phase 5 was a pure function. We contain it behind a snapshot-cached source so replay stays deterministic, but the classification step is no longer pure `(txs, positions) → events`; it is `(txs, positions, gauge_map) → events` with `gauge_map` fetched and snapshotted. This is a real complexity increase and a new failure surface (RPC down ⇒ unresolved gauges ⇒ honest `unclassified`, not wrong numbers).
- Swap booking widens the average-cost engine's blast radius; a mis-booked swap corrupts a position's basis. Mitigated by golden replay tests on fixture wallets and the vs-HODL cross-check.
- The `unclaimed_rewards` field is a determinism carve-out. We accept a second, clearly-labeled provenance-tagged reading alongside `usd_value`; the byte-identical guarantee continues to cover only the replay-derived fields (`model_dump(exclude=...)` extends to exclude the current-state augmentation, mirroring the run-provenance exclusion).
- A fallback price source may add a pinned dependency under the cooldown policy (ADR-0012/0013) if CoinGecko's existing adapter cannot serve block-time history and an alternative is needed.

## Alternatives considered

- **Accept swaps/prices as documented incompletes (no engine change).** Rejected: one unbooked leg nulls the wallet total, so the tool stays unusable for active positions — the exact failure we set out to fix.
- **Token-set-only gauge attribution (no gauge→pool resolution).** Rejected: AERO appears in multiple positions, so the token fallback is ambiguous by construction; attribution would be a coin-flip, violating ADR-0036's "an ambiguous join is worse than an honest gap".
- **Fold unclaimed rewards into unrealized P&L.** Rejected: there is no claim event to replay and it would contaminate the deterministic replay with a live read; isolating it as a labeled field keeps the boundary clean.
- **Supersede ADR-0036 wholesale.** Rejected: the taxonomy, pricing, lots, and loud-failure design are all still correct; this is a refinement, not a reversal.
