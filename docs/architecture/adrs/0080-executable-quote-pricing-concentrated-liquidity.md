# ADR-0080 — Executable-quote pricing via on-chain Quoters; unify constant-product + concentrated liquidity behind one read-only source

> **Status:** proposed (2026-07-11)
> **Date:** 2026-07-11
> **Related plan(s):** 0086-concentrated-liquidity-executable-quotes (accepts at close)
> **Related ADRs:** refines the [Plan 0079](../plans/done/0079-cross-pool-discrepancy-scanner.md) cost model (the v1 `PoolPriceSource` marginal-price-plus-estimated-slippage shape); [ADR-0072](0072-bounded-autonomy-and-prediction-market-execution.md) (this makes the BA-7 arb-viability evidence *real* — the deep venues, not the dust tail); [ADR-0074](0074-edge-selection-criteria-for-execution.md) (edge-selection: after completeness, the target is neglected niches, never major-pair arb); [ADR-0031](0031-data-source-adapter-contract.md) (per-capability source contract + selector registry); [ADR-0038](0038-third-party-api-key-storage.md) (RPC read URL, not a trade key); [ADR-0019](0019-external-http-adapter-resilience.md) (resilient client); [ADR-0046](0046-mcp-large-result-delivery.md) (bounded results); [ADR-0035](0035-defi-domain-placement.md) (DeFi placement); [ADR-0052](0052-binance-exchange-data-source.md)/[ADR-0053](0053-onchain-valuation-source.md) (the read-source-ADR precedent Plan 0079's open-question named)

## Context

Plan 0079's Phase-4 live evidence run (2026-07-11, Base WETH/USDC) returned an honest **null** — no cross-pool discrepancy survived net-of-cost at any size. But the run also exposed *why the evidence was incomplete*: the v1 scanner can only price **constant-product** pools (`getReserves()` → `x·y=k`), and on Base the only deep constant-product WETH/USDC venue is Aerodrome's vAMM (~$8.25M TVL); every other CP pool is dust ($539–$18.6k). The liquidity that actually matters lives in **concentrated-liquidity** venues — Uniswap-v3 and Aerodrome Slipstream — which the v1 adapter cannot price. So the scanner measured the shallow tail, not the market. Without CL pricing the ADR-0072 BA-7 gate cannot be evaluated against real liquidity.

The v1 cost model is the blocker. The adapter returns a pool's **marginal (spot) price plus depth**, and the screener **estimates** slippage from the constant-product formula. A concentrated-liquidity pool has no single depth number — its executable price for a size depends on the liquidity distribution across ticks — so the v1 estimate is both impossible and, for the shallow tail, inaccurate.

Verified facts (2026-07-11):
- A v3-family pool exposes `slot0()` → `sqrtPriceX96` (marginal price = `(sqrtPriceX96 / 2^96)²`, decimals-adjusted) and `liquidity()` (active-tick liquidity). Marginal price is a cheap single `eth_call` — but marginal price alone ignores slippage, which is the entire Phase-4 finding.
- Every v3-family DEX ships a **Quoter** contract — Uniswap `QuoterV2.quoteExactInputSingle` / `quoteExactOutputSingle`, and the Aerodrome Slipstream CL quoter — that **simulates the real swap across ticks** and returns the exact `amountOut`/`amountIn` for a size. The Quoter is **non-view but staticcall-designed**: it is reached by `eth_call`, sends no transaction, signs nothing, changes no state — so it is read-only exactly like `getReserves()`.

## Decision

We will replace the v1 marginal-price-plus-estimated-slippage model with an **executable-quote** primitive, and unify constant-product and concentrated liquidity behind one read-only source contract.

**The primitive.** For a pool and a `trade_size`, an executable quote carries:
- `buy_cost` — quote-token in to **acquire** `trade_size` base (an exact-output swap), and
- `sell_proceeds` — quote-token out from **selling** `trade_size` base (an exact-input swap),

both **already net of that pool's fee and its slippage for the size**.

**Two implementations, one contract.** Constant-product pools compute `buy_cost`/`sell_proceeds` from the `x·y=k` formula (the math that already lives inside the v1 screener — it moves into the source). Concentrated-liquidity pools compute them from the DEX **Quoter** via `quoteExactOutputSingle` / `quoteExactInputSingle`, one `eth_call` each. Both satisfy the same `ExecutableQuoteSource` Protocol ([ADR-0031](0031-data-source-adapter-contract.md)); a new venue is one registry entry.

**The screener becomes exact.** `net = max(sell_proceeds) − min(buy_cost) − gas`, buying at the executably-cheapest venue and selling at the executably-dearest. Slippage and fee are *inside* the quotes, so slippage is now **measured** (the Quoter's real tick-walk), not **estimated** (the formula). Auditability — the property Plan 0079 insisted on — is preserved by reconstructing a slippage/fee breakdown against the `slot0`/`getReserves` marginal reference and carrying it on the observation.

**Read-only stays provable.** The only JSON-RPC method any adapter issues remains `eth_call`; the Quoter is a read simulation. The ADR-0041/0072 AST + source-scan proof extends unchanged (no signing, no key, no state-changing method).

**Purpose, per [ADR-0074](0074-edge-selection-criteria-for-execution.md).** This makes the BA-7 evidence real: Phase 4 measures the deep venues (expected to *prove* the no-go on majors, now against real liquidity), and Phase 5 redirects the scanner at neglected niches (ES-4) where the "you lose to a colocated searcher" prior is not already decisive. Major-pair concentrated-liquidity arb remains a refuse-niche; capture, if any niche edge is found, still requires the [ADR-0073](0073-execution-engine-topology-control-plane-data-plane.md) colocated engine. The scanner stays **evidence**, never execution.

## Consequences

**Positive**
- The BA-7 evidence reflects the real market (deep CL venues), not the dust tail — the scanner's entire purpose is finally served honestly.
- The net-of-cost number is cleaner and more honest: measured slippage, fewer assumptions, no CP-only formula approximating a CL curve.
- One abstraction spans both pool families; adding Uni-v3, Slipstream, or a future venue is a registry entry behind the same Protocol.
- Read-only and determinism invariants are preserved unchanged.

**Negative (the price we pay)**
- **Breaking change to the shipped v1 `ArbObservation` schema** — `buy_cost`/`sell_proceeds` (+ reconstructed breakdown) replace `gross_spread`/`est_slippage`. Plan 0079's tool output evolves; its tests are rewritten. This ADR refines that closed plan's cost model deliberately.
- **~2 Quoter `eth_call`s per pool per size** (exact-in + exact-out) → more RPC load than one `getReserves`. Pacing/batching matters, and public RPCs rate-limit and User-Agent-block (both observed in the Plan 0079 run) — a permissive/paid endpoint and a configurable adapter UA become real requirements.
- Quoter gas estimates are in gas units; converting to a quote-token gas cost still needs a gas-price × native-price step. Kept caller-supplied and conservative, as in v1.
- **It does not change the honest prior.** Major-pair CL arb is still a latency arms race we lose ([ADR-0074](0074-edge-selection-criteria-for-execution.md)). This ADR makes us able to *prove* that against real venues and to *look* at niches — not to win majors. The value is honest measurement, not a profit switch.

## Alternatives considered

- **Keep marginal-price + depth and bolt a separate slippage path onto CL.** Rejected — a CL pool has no single depth number, so you need the Quoter regardless; maintaining two different cost models (formula for CP, something else for CL) is strictly worse than unifying on the executable quote both can produce.
- **Reconstruct the CL curve in-house (tick-bitmap walk).** Rejected — the Quoter is the DEX's authoritative swap simulation; reimplementing tick-crossing math is a large, bug-prone surface for no accuracy gain over an `eth_call` to the Quoter.
- **Use a DEX-aggregator quote API (0x / 1inch).** Rejected again (Plan 0079 already rejected it): an aggregator returns a *routed net* quote across venues, which hides the *per-pool* discrepancy the scanner exists to measure, and adds a key + ToS.
- **Concentrated-liquidity marginal price only (`slot0`, no Quoter).** Rejected — marginal price ignores slippage, which is precisely what the Phase-4 null showed to be decisive. A marginal-only CL source would repeat the v1 blind spot in a new venue.
