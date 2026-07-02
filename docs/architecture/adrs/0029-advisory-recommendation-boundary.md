# ADR-0029 — Advisory recommendation boundary: the app may recommend, not act

> **Status:** accepted (2026-07-02 — at [Plan 0038](../plans/done/0038-advisor-layer.md)'s close, per the Notes' condition)
> **Date:** 2026-05-30
> **Related plan(s):** [Plan 0038](../plans/done/0038-advisor-layer.md) (the advisor layer — implements this ADR; closed 2026-07-02), [Plan 0039](../plans/0039-advisor-ui-surface.md) (the advisory UI surface)
> **Related ADRs:** [ADR-0015](0015-claude-code-primary-control-surface.md) (the conditions-are-facts framing this carves out from), [ADR-0025](0025-trade-execution-feasibility.md) (execution — the layer *above* this one), [ADR-0004](0004-strategy-interface.md) (strategy signals the advisor fuses), [ADR-0023](0023-technical-analysis-surface.md) (the condition surface the advisor reads), [ADR-0024](0024-extended-backtest-metrics.md) (the backtested basis a recommendation carries), [ADR-0030](0030-forecasting-subsystem.md) (forecasts the advisor consumes as a conviction input)

## Context

`market-analyser` was built on a deliberate, load-bearing principle, stated plainly in CLAUDE.md and framed in [ADR-0015](0015-claude-code-primary-control-surface.md): *"Conditions are facts, decisions are the user's. Analyst skills report conditions; they never recommend buy/sell/exit/rebalance."* The `market-analyst` and `defi-analyst` skills are pure condition-reporters by contract. The app can detect candlestick patterns, classify regime, run and walk-forward-validate backtests — but it has never synthesised any of that into an actionable directional call. The user, driving their own app for their own use, has now asked for exactly that: **actionable buy/sell alerts.**

This is a different line from the one [ADR-0025](0025-trade-execution-feasibility.md) maps. ADR-0025 covers *execution* — placing an order — and names three lines it crosses (principle, security, correctness) plus six invariants (assisted-first, testnet-first, segregated keys, idempotency, risk guard, audit). A **recommendation** crosses only the first of those: the principle line. It moves no money, holds no trade-permissioned key, runs no order state machine. ADR-0025's invariant 1 ("the agent *prepares and sizes* an order; the user confirms") already gestures at a recommendation as the input to assisted execution — but ADR-0025 stops short of saying the app may *produce* that recommendation as a first-class, advisory artifact for an analysis product whose user executes manually elsewhere. That is the gap this ADR fills.

The decision is genuinely two-sided: emitting "go long AAPL, stop here, target there" is a real reversal of the principle the whole skill ecosystem was designed around, and it cannot be slipped in as a quiet feature. The question is not only *whether* to cross the line but *how to contain the crossing* so the fact/decision boundary survives everywhere except where we explicitly relax it. This is the user's own app, own funds, own machine; that is a reason to design the carve-out deliberately, not a reason to refuse it.

## Decision

We will add a **separate advisor layer** — a new `src/market_analyser/advisor/` package and a new owner skill (`advisor`, via `skill-creator`) — distinct from the read-only analyst skills, that fuses **conditions** (the [ADR-0023](0023-technical-analysis-surface.md) analysis surface), **live strategy signals** ([ADR-0004](0004-strategy-interface.md) strategies evaluated on the current bar), **backtested edge** ([ADR-0024](0024-extended-backtest-metrics.md) walk-forward stats), and **forecasts** ([ADR-0030](0030-forecasting-subsystem.md)) into an explicit, labeled **trade recommendation**: direction (long / short / flat) + entry zone + stop + target(s) + conviction + the rationale (which conditions/signals/forecasts fired) + the backtested/validated basis. The recommendation is **advisory output the user acts on manually**.

The analyst skills (`market-analyst`, `defi-analyst`) keep their pure read-only condition-reporting boundary **unchanged** — no analyst skill gains a recommend path; the advisor is a downstream consumer that imports their *outputs*, not their internals. The advisor **stops short of order placement**: no trade-permissioned secret, no order layer, no money movement is introduced by this ADR. Execution remains [ADR-0025](0025-trade-execution-feasibility.md)'s open decision; if it is ever taken, the advisor's recommendation is precisely the input ADR-0025's assisted-first invariant expects.

The carve-out is contained by three rules every recommendation must obey: it is **labeled advisory**; it **carries its rationale and its backtested/forecasted basis with honest uncertainty** (no bare calls, no false precision); and **the user remains the decision-maker** — the app recommends, the user acts. An unexplained or basis-free recommendation is a review finding.

## Consequences

### Positive
- The app becomes end-to-end useful for the user's actual workflow — analysis → recommendation — instead of stopping at condition reporting and leaving the synthesis entirely in the user's head.
- **The crossing is contained to one labeled layer.** The fact/decision boundary survives in every analyst skill; only the explicitly-named advisor relaxes it. The separation that makes the analyst skills reusable and reasoned-about is preserved.
- **Clean seam to execution.** A recommendation is exactly the artifact [ADR-0025](0025-trade-execution-feasibility.md)'s invariant 1 expects an agent to "prepare and size." If execution is ever built, the advisor feeds it with no rework; the advisory layer and the (heavier, key-holding) execution layer compose cleanly.
- **Reviewable by construction.** Because every recommendation must carry rationale + basis, a confident-but-groundless call fails review — the discipline is enforced at the artifact shape, not by good intentions.

### Negative
- **We are crossing the principle line, deliberately.** Once the app says "short here, stop there," the psychological weight changes: the user may anchor on the app's call and under-exercise their own judgment. Advisory labeling, mandatory rationale, and honest uncertainty are the mitigations, but the line is genuinely crossed and cannot be un-crossed quietly.
- **Risk-sizing brushes [ADR-0025](0025-trade-execution-feasibility.md)'s territory.** Entry/stop/target levels look like an order ticket even though they are advisory. There will be standing pressure to "just submit it" — that slide is exactly ADR-0025's assisted-first/kill-switch/segregated-key domain and must **not** happen by accretion inside the advisor. The advisor stays advisory; execution is a separate, invariant-gated decision.
- **A wrong recommendation costs more than a wrong condition report.** A mistaken "overbought" reading is information the user weighs; a mistaken "go long" the user follows loses real money. This raises the correctness bar on *everything* the advisor consumes — the analysis math, the live-signal evaluation, the backtest stats, the forecasts — and even when all of those are right, the user bears the loss of a recommendation that didn't work.
- **New skill + module + a wide coupling surface.** The advisor is a downstream consumer of `analysis/`, `strategies/`, `backtest/`, and `forecast/`, so it is sensitive to drift in all of them — the most integration-coupled component in the repo. That is inherent to its fusion role, but it is a real maintenance cost.

### Neutral
- Like [ADR-0025](0025-trade-execution-feasibility.md), this ADR sits `proposed` until a plan commits; it has no automatic close ceremony. It accepts if and when the user commits to an advisor plan, at which point these three carve-out rules become that plan's acceptance criteria.
- This ADR does not change [ADR-0025](0025-trade-execution-feasibility.md); it sits one layer below it and is fully compatible with ADR-0025's six invariants should execution ever be built.

## Alternatives considered

### Alternative A — Relax the "conditions are facts" rule project-wide
Let the analyst skills themselves emit buy/sell calls instead of standing up a separate layer. **Rejected** because it erases the fact/decision boundary *everywhere* — every analyst surface becomes an advice surface, the `market-analyst`/`defi-analyst` contracts lose their meaning, and the change is very hard to walk back. The separate-layer decision delivers the identical capability with the crossing contained to one component; there is no capability gained by polluting the analysts.

### Alternative B — Stay condition-only; let the agent synthesise a call ad hoc in conversation
The agent could already eyeball the conditions in chat and suggest a trade informally, with no new code. **Rejected as the product decision** (it is the status quo this ADR replaces): an ad-hoc chat suggestion is unrepeatable, unlabeled, not backed by a recorded backtest, and leaves no auditable artifact. A first-class advisor makes the recommendation reproducible, basis-backed, surfaceable in the UI, and reviewable — the difference between a casual opinion and a defensible output.

### Alternative C — Fold recommendations into the execution layer (ADR-0025)
Treat "recommend" and "place an order" as one feature. **Rejected** because it conflates two decisions with very different costs. The user wants advisory alerts now and has *not* committed to order placement; binding them forces ADR-0025's heavy invariants (trade-permissioned keys, order state machine, reconciliation, kill switch) onto a feature that needs none of them. Keeping them separate lets the advisory layer ship on its own and *optionally* feed execution later if ADR-0025 is ever taken.

## Notes
- **The live-signal evaluator is a prerequisite primitive, and is not itself a principle-line crossing.** Today the app can backtest a strategy historically but cannot ask "what does this strategy signal on the *current* bar." That evaluator (its own plan, possibly its own small ADR) produces a *signal* — still a condition — which the advisor then turns into a recommendation. Building the evaluator does not cross the line; only the advisor's synthesis does.
- **What committing would require, in order:** (1) user go/no-go on this posture; (2) the live-signal evaluator plan (the missing primitive); (3) a new `advisor` skill via `skill-creator` owning `src/market_analyser/advisor/`; (4) a phased advisor plan whose UI surface (a recommendations view) is its own `ui-builder` phase; (5) honest-uncertainty/labeling rules enforced as acceptance criteria.
- **No secrets, no execution, no auto-action are introduced by this ADR.** The advisor reads data and writes advisory artifacts; it holds no trade key and submits no order. Any future advisor code that places an order, or that logs/serialises a trade-permissioned secret, is an immediate review blocker and belongs to [ADR-0025](0025-trade-execution-feasibility.md), not here.
