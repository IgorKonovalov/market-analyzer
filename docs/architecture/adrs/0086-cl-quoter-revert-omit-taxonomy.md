# ADR-0086 — Concentrated-liquidity Quoter reverts: omit the pool on a quote-leg revert, raise on structural-read / decode failure

> **Status:** accepted (2026-07-13, at Plan 0094's close)
> **Date:** 2026-07-12
> **Related plan(s):** 0094-cl-quoter-revert-omit-taxonomy (accepts at close)
> **Related ADRs:** **refines [ADR-0080](0080-executable-quote-pricing-concentrated-liquidity.md)** (the executable-quote read-error taxonomy — one clause was self-contradictory; this ADR resolves it, it does not supersede); [ADR-0031](0031-data-source-adapter-contract.md) (the `ExecutableQuoteSource` Protocol whose contract this sharpens); [ADR-0019](0019-external-http-adapter-resilience.md) (transport-failure taxonomy is unchanged); [ADR-0072](0072-bounded-autonomy-and-prediction-market-execution.md)/[ADR-0074](0074-edge-selection-criteria-for-execution.md) (the BA-7 evidence this unblocks against un-curated venue sets)

## Context

ADR-0080 gave the `ExecutableQuoteSource` contract two clauses that turn out to conflict in practice: *"a pool that cannot source the size is **omitted** rather than fabricating a number"* and *"a shape-broken on-chain read or **Quoter revert raises** the typed error taxonomy."* The constant-product adapter can honour both because it detects "cannot source the size" *before* any revert (`_cp_executable_legs` returns `None` when `trade_size ≥ base reserve`). The concentrated-liquidity adapter cannot: for a CL pool, "cannot source the size" only *manifests* as a Quoter revert, so the two clauses collapse onto the same event and the adapter picked "raise."

The Plan 0086 phase-4 live evidence run (2026-07-12, Base WETH/USDC) exposed the cost. The Uniswap-v3 / Aerodrome-Slipstream `QuoterV2` reverts `Error(string) "Unexpected error"` when the underlying pool swap fails for insufficient liquidity. On real venues, dust tiers are ubiquitous — Slipstream WETH/USDC at tick-spacing 50 and 200 hold cents of liquidity (selling 1 WETH returns $0.30 / $0.05) and revert `quoteExactOutputSingle`. `ConcentratedPoolPriceAdapter._eth_call → _result_bytes` raises `ConcentratedPoolError` on any JSON-RPC `error`, which propagates up and **aborts the whole scan**: a single dust pool in a configured venue set makes `scan_pool_discrepancies` return `error:"malformed_response"` with zero observations. Since the tool exists to *discover* which pools are deep, requiring the operator to pre-curate out every thin tier defeats its purpose. The unit test `test_quoter_revert_raises_typed_error` modelled a revert only as a shape-broken read and asserted it raises, so it encoded the gap rather than catching it (fake transports only; no live Quoter was ever exercised).

## Decision

We refine ADR-0080's read-error taxonomy for the concentrated-liquidity adapter along a **structural** boundary — *which* call failed, not *what string* it returned:

- A **JSON-RPC execution-revert on a quote leg** (`quoteExactInputSingle` / `quoteExactOutputSingle`) means the pool has no executable price for the size → **omit that pool** (return no quote for it), exactly as the CP adapter omits a depth-exceeded pool. If either leg reverts, the pool is omitted (a round trip needs both legs).
- A **revert on a structural read** (`factory.getPool`, `slot0`, `decimals`, `fee`) means a misconfigured venue/pool → **raise** `ConcentratedPoolError` (an operator-visible config failure, not a thin pool).
- A **decode failure on a *successful* response** (result too short, non-hex) and a **successful-but-out-of-range value** (e.g. a zero Quoter output) → **raise** / `ValidationError` as today — that is genuine shape drift or a fabrication guard, never silently omitted.
- **Transport failures** (429 / 5xx / exhaustion) remain the ADR-0019 `RateLimitedError` / `UpstreamUnavailableError` — unchanged.

The classification is by call context, with **no dependence on the revert string**, so it holds across Quoter forks (Uni-v3, Slipstream, and future venues). A genuinely wrong quoter address reverts on *every* tier, so the venue yields zero quotes — a visible, diagnosable outcome, not a fabricated number.

## Consequences

**Positive**
- `scan_pool_discrepancies` is usable against un-curated, realistic venue sets: dust tiers are omitted, deep tiers priced. The BA-7 evidence layer (ADR-0072/0074) works without the operator pre-knowing which tiers are deep.
- CL and CP adapters now honour the same Protocol clause identically ("cannot source the size → omit"), so the `ExecutableQuoteSource` contract stops being self-contradictory.
- Robust across DEX forks — no revert-string allowlist to maintain.

**Negative (the price we pay)**
- A quote-leg revert caused by something *other* than thin liquidity (a transient node quirk, an exotic pool state) is now silently omitted rather than surfaced. Mitigation: a venue that yields zero quotes across all its tiers is the visible signal that something is wrong at the venue level; the operator sees an empty venue, not a fabricated price.
- The "revert always raises" simplicity is gone; the adapter now branches read handling by call site, a little more code and a matching pair of tests (omit-on-quote-revert vs raise-on-structural-revert).

## Alternatives considered

- **Match the revert string** (omit only on the Quoter's `"Unexpected error"` / SPL signature, raise otherwise). Rejected — brittle: each Quoter fork reverts with different wording, so the allowlist grows and silently mis-classifies a new venue's revert as a hard error (or a real bug as "just thin").
- **Depth pre-filter** (read `liquidity()` and skip pools below a threshold before quoting). Rejected — adds a magic threshold and an extra read, and CL "depth for a size" is not a single number (the exact reason the Quoter exists), so the threshold under- or over-skips versus the real executable fill.
- **Leave it and require operator curation** (only configure known-deep tiers). Rejected — it makes the tool assume the answer it exists to find, and any missed dust tier still aborts the whole scan.
