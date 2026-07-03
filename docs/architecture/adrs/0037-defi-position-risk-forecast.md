# ADR-0037 — DeFi position risk & forecast: conditional facts, not market prediction

> **Status:** proposed — accepts at [Plan 0042](../plans/0042-defi-position-risk-forecast.md)'s close
> **Date:** 2026-06-03
> **Related plan(s):** [Plan 0042](../plans/0042-defi-position-risk-forecast.md) (implements this; approved 2026-06-05 — prereq Plan 0034 closed, Plan 0035 active)
> **Related ADRs:** [ADR-0030](0030-forecasting-subsystem.md) (the market-forecasting subsystem this is deliberately distinguished from), [ADR-0029](0029-advisory-recommendation-boundary.md) (the recommend-vs-report line this stays on the report side of), [ADR-0015](0015-claude-code-primary-control-surface.md) ("conditions are facts" charter), [ADR-0018](0018-backtest-result-schema.md) (the determinism contract the simulation mirrors), [ADR-0035](0035-defi-domain-placement.md) (the `defi/` home), [ADR-0036](0036-defi-pnl-reconstruction.md) (the cost basis scenarios value against)

## Context

"Forecast the future of positions and risks" was the most charter-sensitive part of the ask. The project's load-bearing principle ([ADR-0015](0015-claude-code-primary-control-surface.md), CLAUDE.md): *"Conditions are facts, decisions are the user's. Analyst skills report conditions; they never recommend buy/sell/exit/rebalance."* The `defi-analyst` is a read-only condition reporter by contract. "Forecast" reads dangerously close to prediction, and prediction is exactly what the analyst charter forbids — so the *shape* of this capability is a real decision, not a default.

There is a recently-decided neighbour: [ADR-0030](0030-forecasting-subsystem.md) (forecasting subsystem). It is tempting to treat DeFi risk forecasting as just an instance of it. But the two forecast fundamentally different things, and conflating them would either block harmless deterministic what-ifs behind an inapplicable validation gate, or dilute ADR-0030's gate to fit. The distinction is the crux of this ADR:

- **ADR-0030 forecasts *the market*** — *will the price go up?* — a directional prediction whose only honest defence is demonstrated out-of-sample skill, so it is gated by mandatory walk-forward validation that must beat a naive baseline.
- **This ADR forecasts *the position given assumed market moves*** — *if* the price does X, what happens to my impermanent loss, my Aave health factor, my liquidation distance? The scenario engine asserts **no market view**; it is a deterministic function of position math applied to a supplied price shock. There is no directional claim to validate — correctness is unit-testable position math, not predictive skill.

The user extended the ask past pure what-ifs to **probabilistic risk** (e.g. probability of liquidation within N days). That layer *does* make a forward likelihood statement — but it is explicitly conditional on a stated volatility model (realized vol from historical prices), not a claim of directional edge. It stays charter-safe by being honest about its assumptions, not by predicting direction.

The determinism non-negotiable ([ADR-0018](0018-backtest-result-schema.md)) applies with teeth: a Monte Carlo simulation is a textbook nondeterminism source (unseeded RNG, hash-order reductions) and must be pinned to stay reproducible.

## Decision

We will add a **DeFi risk/forecast engine** in `src/market_analyser/defi/` ([ADR-0035](0035-defi-domain-placement.md)) producing two output kinds, both framed as **conditional facts** about a position, never as a market view or an action recommendation:

> **1. Deterministic scenario sensitivity.** Given a supplied price move on the underlying asset(s) — e.g. ETH ±30% — recompute the position's impermanent loss, value, Aave health factor, and liquidation distance via the position math (the IL formula, the Aave HF formula). Pure deterministic functions of an *assumed* input; the engine asserts nothing about whether that input will occur.
>
> **2. Conditional probabilistic risk.** Using a historical-volatility model over the underlying assets, estimate likelihood quantities — probability of liquidation within N days, expected IL distribution — via **seeded** Monte Carlo / analytic methods. Every such number carries its volatility assumptions explicitly: "≈12% chance of liquidation in 30d *under realized-vol-from-the-last-90-days*," never a bare "12% chance."

Four invariants bind the engine; a plan that violates any fails review:

1. **No directional market view.** The engine never claims where prices will go. Scenarios are parameter-supplied; probabilities are conditional on an explicit, stated vol model. This is what keeps it on the [ADR-0015](0015-claude-code-primary-control-surface.md) "conditions are facts" side and *distinct from* [ADR-0030](0030-forecasting-subsystem.md).
2. **Determinism.** Seeded simulation, pinned libraries, snapshot inputs → reproducible outputs modulo provenance, mirroring [ADR-0018](0018-backtest-result-schema.md). The vol model is fit on trailing data only (causal — same anti-lookahead grain as the rest of the repo).
3. **Honest uncertainty.** Probabilities carry their model assumptions and are presented as conditional estimates; no false precision, no point forecasts dressed as certainty.
4. **Conditional-facts boundary.** Outputs are facts and conditional-facts about the position. The engine **never** emits exit / rebalance / buy / sell. If that crossing is ever wanted, it belongs to [ADR-0029](0029-advisory-recommendation-boundary.md)'s separate advisor layer consuming these facts — exactly as that advisor would consume ADR-0030's forecasts. The risk engine stays analyst-side.

**Direction-as-no-claim** (scenario) and **conditional probability** (risk) are chosen over a directional forecast because they answer the user's real question — *how exposed am I, and how bad could it get* — without the engine ever pretending to know the market, which would breach the charter outside the one place ADR-0029 sanctions it.

## Consequences

### Positive
- Delivers the forward-looking risk view the user wants **without crossing the prediction line** — the scenario engine makes no market claim, and the probabilistic layer is explicitly assumption-conditional.
- **Cleanly distinct from [ADR-0030](0030-forecasting-subsystem.md):** the walk-forward-beats-baseline gate (which presupposes a directional prediction) doesn't category-mismatch onto deterministic position math, and ADR-0030's gate stays undiluted for the market-forecasting work it governs.
- Reuses the determinism + honest-uncertainty discipline already established ([ADR-0018](0018-backtest-result-schema.md), [ADR-0030](0030-forecasting-subsystem.md)) — seeded simulation, trailing-only fits — so the rules are familiar, only the validation regime differs.
- Position math (IL, health factor, liquidation distance) is **unit-testable against known inputs** — correctness is provable, not merely validated statistically.
- Feeds [ADR-0029](0029-advisory-recommendation-boundary.md)'s advisor (if ever built) a clean conditional-risk input, the DeFi analogue of how the advisor consumes ADR-0030 forecasts.

### Negative
- **Probabilistic risk reads like prediction no matter how it's framed.** A user will hear "12% chance of liquidation" as a forecast, not as "conditional on this vol model." The honest-uncertainty invariant only partially offsets this; the framing discipline must hold in every output and every UI surface, or the charter erodes by presentation.
- **Garbage-in on the vol model.** A liquidation probability is only as good as the volatility assumption; a regime change (vol spike) makes a trailing-vol estimate stale and over-optimistic exactly when it matters most. The engine must state the assumption, but cannot make a backward-looking vol model see a future shock — a real limitation, not a tuning bug.
- **Scenario sensitivity needs accurate current state.** "If ETH −30%, your HF drops to 1.05" is only right if the *current* health factor, debt, and collateral are right — which depends on the deep on-chain adapters ([ADR-0034](0034-defi-portfolio-aggregator.md))'s precise reads, not the aggregator's approximations. Risk-grade numbers must come from the depth half of the hybrid.
- **Standing pressure toward recommendations.** "12% liquidation risk" invites "so should I de-risk?" — the exact slide invariant 4 forbids. Keeping the engine on the facts side is a discipline that must be defended at every review, because the advisory step looks like a small, helpful addition.

### Neutral
- `proposed` until the risk/forecast plan commits; accepts at that plan's close, at which point the four invariants become its acceptance criteria — same cadence as [ADR-0029](0029-advisory-recommendation-boundary.md)/[ADR-0030](0030-forecasting-subsystem.md).
- The probabilistic layer's library footprint (a stats/simulation dependency) is subject to the cooldown/pin policy ([ADR-0012](0012-dependency-cooldown.md)/[ADR-0013](0013-pin-direct-dependencies.md)); the plan picks a deterministic-friendly library.

## Alternatives considered

### Alternative A — Treat DeFi risk forecasting as an instance of ADR-0030 (same invariants, including the walk-forward gate)
Fold it under the existing forecasting subsystem. **Rejected** because ADR-0030's walk-forward-beats-baseline gate presupposes a *directional prediction with measurable out-of-sample skill*. Scenario sensitivity makes no prediction (it is deterministic math on an assumed input), so the gate is category-mismatched — applying it would either block harmless what-ifs or force a dilution of ADR-0030's gate. They are siblings sharing determinism/honesty invariants but with different validation regimes; keeping them distinct keeps both gates meaningful.

### Alternative B — Scenario-only, no probabilistic layer
Deterministic what-ifs only; drop liquidation probabilities and IL distributions. **Rejected** as the option the user explicitly extended past: what-ifs answer "what happens *if* ETH −30%" but not "how *likely* is liquidation." The probabilistic layer is kept charter-safe by the conditional-vol-model framing rather than dropped.

### Alternative C — Let the engine emit exit / rebalance recommendations directly
Have the risk engine say "de-risk this position." **Rejected** because a DeFi exit call is the same principle crossing as a TradFi buy call ([ADR-0015](0015-claude-code-primary-control-surface.md)), and [ADR-0029](0029-advisory-recommendation-boundary.md) already decided such crossings live in a *separate advisor layer*, not in an analyst-side engine. The risk engine produces the facts the advisor would consume; it does not become the advisor.

## Notes
- **Pre-existing tension flagged:** the `defi-analyst` skill's frontmatter advertises a "rebalance suggestion" mode, which sits across the [ADR-0029](0029-advisory-recommendation-boundary.md) line (rebalance is a recommendation, hence advisor-layer, not analyst). This ADR governs the *engine* (facts only); the skill-charter reconciliation is an ADR-0029 question to settle when/if the advisor layer is built. Calling it out here so it isn't lost.
- The scenario/probabilistic split mirrors a familiar risk-management distinction (stress test vs. VaR-style estimate); both are reporting, neither is advice.
- Pairs with [ADR-0035](0035-defi-domain-placement.md) (home), [ADR-0036](0036-defi-pnl-reconstruction.md) (cost basis scenarios value against), and [ADR-0034](0034-defi-portfolio-aggregator.md) (the deep-state source scenarios depend on for accurate current values).
