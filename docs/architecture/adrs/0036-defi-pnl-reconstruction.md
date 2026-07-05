# ADR-0036 — DeFi P&L by transaction replay: event taxonomy, block-time pricing, average-cost lots

> **Status:** accepted (Plan 0035 close, 2026-07-05) — the gating `human` live smoke ran 2026-07-05 against the test wallet: one position fully reconstructed (realized +$4,015.16 on a $873.50 basis), four honestly `incomplete` (the designed loud-failure paths), wallet totals honestly null, Zerion FIFO cross-check $29,670.07 with no gross-divergence warning, replay-derived figures byte-identical across runs
> **Date:** 2026-06-03
> **Related plan(s):** [Plan 0035](../plans/0035-defi-pnl-reconstruction.md) (implements this end to end; approved 2026-06-05)
> **Related ADRs:** [ADR-0034](0034-defi-portfolio-aggregator.md) (the decoded-tx-history source this replays), [ADR-0035](0035-defi-domain-placement.md) (the `defi/` home this engine lives in), [ADR-0018](0018-backtest-result-schema.md) (the determinism contract this mirrors), [ADR-0006](0006-persistence-layout.md) (SQLite cache for snapshotted prices + decoded events), [ADR-0019](0019-external-http-adapter-resilience.md) (resilience for the price source), [ADR-0037](0037-defi-position-risk-forecast.md) (the risk engine that consumes the cost basis this produces)

## Context

"Calculate their profitability" was a core ask, and the user explicitly chose to **reconstruct cost basis and P&L from on-chain transaction history** rather than trust an aggregator's computed number ([ADR-0034](0034-defi-portfolio-aggregator.md) Alternative D). That choice buys auditability and reproducibility but commits us to an accounting engine, and accounting engines are full of decisions that look like details and aren't: which on-chain events count as economic flows, what price each is valued at, and how partial exits realize gains. Future-you will absolutely ask "why is my P&L *this* number," and only a written methodology answers that.

The non-negotiables bite directly here. **No-lookahead** (CLAUDE.md: a decision at bar `i` sees only `bars[0..=i]`) has a P&L corollary: every historical event must be valued at *its own* block timestamp, never at "now" — pricing a two-year-old deposit at today's price is the lookahead sin in accounting clothing. **Determinism** ([ADR-0018](0018-backtest-result-schema.md)): the same wallet + same block range must produce byte-identical P&L modulo provenance — but historical prices come from an external API that can *revise* its numbers, so a naive re-fetch silently breaks reproducibility. **Validate at boundaries**: a missing price or a `None`/`NaN`/negative amount in a decoded event must fail loud, because a leg silently valued at zero produces confident, wrong P&L — the trading-domain version of "a bad bar treated as zero."

A complication unique to DeFi: profit is not just buy-low-sell-high. An LP position earns trading fees and (on Aerodrome) reward emissions; a lending position accrues supply interest and pays borrow interest; impermanent loss means the position can underperform simply holding the tokens. A credible P&L must account for fees and rewards as income and must be able to express performance *relative to HODL*, not just absolute.

Zerion's API ([ADR-0034](0034-defi-portfolio-aggregator.md)) offers a built-in FIFO PnL endpoint, which raises the question of method (FIFO vs average cost) and whether to lean on it at all.

## Decision

We will compute cost basis and P&L by **replaying the wallet's decoded transaction history per position**, valuing each economic leg at its block timestamp, under a fixed event taxonomy and average-cost lot accounting. The engine lives in `src/market_analyser/defi/` ([ADR-0035](0035-defi-domain-placement.md)).

**Event taxonomy** (each event carries a USD value at its block time): `add_liquidity` / `remove_liquidity` (LP), `supply` / `withdraw_supply` / `borrow` / `repay` (lending), `swap`, `fee_claim` (LP trading fees), `reward_claim` (emissions), `liquidation`. Events outside the taxonomy are surfaced as unclassified, never silently dropped.

**Block-time pricing.** Every leg is priced at its own block timestamp via the `HistoricalPriceSource` ([ADR-0034](0034-defi-portfolio-aggregator.md): DefiLlama keyless, Alchemy fallback) — never at current price. This is the no-lookahead rule applied to accounting.

**Average-cost lots for v1.** Cost basis per position is a running average; a partial exit realizes a proportional share of the average basis. Fees and rewards are booked as **realized income when claimed**, valued at claim-time price. **Realized P&L** = value extracted (withdrawals, swaps-out, claimed fees/rewards) − proportional cost basis released; **unrealized P&L** = current position value − remaining cost basis. For LP positions the engine also reports P&L **versus a HODL benchmark** (impermanent loss as a fact alongside raw P&L).

**Determinism via price snapshots.** Every resolved `(token, timestamp) → price` is **cached in SQLite** ([ADR-0006](0006-persistence-layout.md)) on first lookup and re-read thereafter, so a re-run is byte-identical even if the upstream price API later revises — mirroring [ADR-0018](0018-backtest-result-schema.md)'s "same inputs → same outputs modulo provenance." Decoded events are likewise cached (tx history is immutable). Event ordering is deterministic (block number, then log index), never set-iteration or wall-clock.

**Loud failure on bad data.** A missing price for a required leg, or a `None`/`NaN`/negative/implausible amount in a decoded event, fails the position's P&L with an explicit flag — it is never coerced to zero. A partial wallet history (provider gap) yields a P&L marked *incomplete*, not a confident wrong total.

We will **not** consume Zerion's PnL endpoint as the source of truth. Its FIFO number is retained only as an optional **cross-check**: an order-of-magnitude divergence from our average-cost reconstruction flags a likely bug (expected method-driven differences aside).

## Consequences

### Positive
- P&L is auditable end to end: every number traces to decoded events priced at named block timestamps, not an opaque vendor figure.
- The price-snapshot cache makes reconstruction both **reproducible** (revision-proof) and **affordable** (a re-scan re-reads cached prices/events instead of re-fetching).
- Booking fees/rewards as income and reporting-vs-HODL makes the P&L *honest about DeFi* — it captures yield and impermanent loss, the two things a naive "value now minus value in" misses.
- Loud failure on missing/bad data keeps a provider gap from masquerading as a real loss or gain.

### Negative
- **Event classification is the schedule risk and the correctness risk.** Decoding deposits/withdrawals/fees/rewards correctly per protocol (Aave vs Uni v3 vs Aerodrome) is fiddly, and a misclassified or missed event type produces wrong P&L that *looks* plausible. This is the heaviest piece of the whole DeFi program; if it slips, discovery + deep-state + risk still deliver a "what do I hold / how healthy is it" product without P&L (a deliberate fallback the plan keeps explicit).
- **Average-cost is a real accounting choice with consequences.** It diverges from FIFO on partial exits and is not a tax-lot method; a user expecting FIFO realized gains (or doing taxes) will see different realized figures. We accept this for v1 as the simpler, more-deterministic, less-surprising default for a "how am I doing" tool; FIFO is deferred, not foreclosed.
- **Historical-price coverage gaps.** Long-tail or newly-listed tokens may lack a historical price at a given timestamp; those legs fail loud and mark the P&L incomplete rather than guessing. Correct, but it means some positions won't get a full P&L until a price source covers them.
- **More external price calls.** Per-event valuation multiplies price lookups; the SQLite snapshot cache amortizes re-runs but the first scan of a long history is call-heavy and must respect source rate limits ([ADR-0019](0019-external-http-adapter-resilience.md)).

### Neutral
- Cross-checking against Zerion's FIFO PnL is advisory only; the two methods are not expected to match exactly, so the check flags gross divergence, not small differences.
- The cost basis this engine produces is the input to the risk engine's scenario valuations ([ADR-0037](0037-defi-position-risk-forecast.md)); the two share the position model but are separate computations.

## Alternatives considered

### Alternative A — Trust the aggregator's computed PnL
Use Zerion's (or another provider's) PnL endpoint directly. **Rejected** (the user's explicit choice): opaque methodology, non-reproducible, provider-dependent, and unauditable — it fails the determinism/honesty posture the whole data layer is built on. Kept only as a sanity cross-check.

### Alternative B — FIFO lot accounting in v1
Match Zerion's method and standard crypto tax-lot convention. **Rejected for v1, deferred not foreclosed:** FIFO adds lot-tracking state and complexity for a first version whose job is the "how am I doing" question, which average cost answers deterministically with less machinery. FIFO becomes a later option (and the natural choice if tax reporting is ever a goal).

### Alternative C — Mark-to-market only (current value, no cost-basis reconstruction)
Skip history; report only what positions are worth now. **Rejected** because that is *valuation*, not *profitability* — it cannot say whether you're up or down, which was the explicit ask. Current value is a component (the unrealized leg), not the whole answer.

## Notes
- The no-lookahead corollary is the load-bearing rule here: **price every event at its block, never at now.** A reviewer should check the pricing path first.
- Pairs with [ADR-0034](0034-defi-portfolio-aggregator.md) (event + price sources) and [ADR-0037](0037-defi-position-risk-forecast.md) (consumes cost basis). Lives in the `defi/` domain per [ADR-0035](0035-defi-domain-placement.md).
