# ADR-0071 — Advisor consumes non-directional forecasts as non-voting inputs; direction leg demoted to non-gating

> **Status:** accepted (Plan 0077 close 2026-07-11)
> **Date:** 2026-07-10
> **Related plan(s):** 0077

## Context

The advisory `recommend` tool fuses four legs — conditions, live signal, backtested edge, and forecast — into a labeled recommendation, under an **all-legs-agree** invariant: a directional call requires every gate to pass ([ADR-0029](0029-advisory-recommendation-boundary.md)). Since [Plan 0066](../plans/done/0066-advisor-tiered-forecast-unification.md) the forecast leg is the direction forecaster's own tiered output.

This creates two problems, both rooted in the direction forecaster's near-absent edge (see [ADR-0070](0070-non-directional-forecast-targets.md)):

- **A no-edge direction leg vetoes everything.** Because direction almost never beats baseline, the conjunctive gate means a well-corroborated setup (conditions + backtest both directional) gets blocked by a forecast leg that has no opinion — the weakest, least-reliable leg governs.
- **A marginal, fold-sensitive direction leg becomes the deciding vote.** When it does squeak past its gate (e.g. h=21 in some fold layouts), that fragile result can be the factor that tips a recommendation — an "edge" that is partly a validation artifact.

[ADR-0070](0070-non-directional-forecast-targets.md) adds two new, genuinely-edge-bearing forecast kinds — volatility and regime-transition — but they are **non-directional**: a volatility forecast is not long or short. They cannot be legs in an "agree on direction" gate. The question is how the advisor should consume them, and what to do about the direction leg now that a better signal exists beside it.

## Decision

We change how the advisor treats forecasts, in two coupled moves:

1. **Non-directional forecasts are non-voting advisory inputs.** The volatility and regime-transition forecasts feed **conviction magnitude, position sizing, and stop distance** — never a directional vote. Predicted volatility shapes size (inverse-vol) and stop distance; regime-transition shapes conviction and context. They can make a call smaller, wider-stopped, or lower-conviction, but they can **never flip or manufacture a direction**. ADR-0029's all-legs-agree **directional** invariant is therefore left fully intact — the set of legs that *vote* on direction is unchanged.

2. **The direction leg is demoted to non-gating below a skill-margin threshold.** When the direction forecast's own out-of-sample beats-baseline margin is below a pinned threshold, it becomes **advisory, not gating**: it no longer vetoes a directional call that the remaining voting legs (conditions + backtested edge + live signal) already corroborate, and it cannot be the sole deciding vote. It still cannot *manufacture* a call, and when it genuinely beats baseline by more than the threshold it votes as before. The full gate trace ([ADR-0058](0058-forecast-recommendation-explainability.md)) records the demotion explicitly, so a reader sees "direction leg present but non-gating (skill below threshold)" rather than a silent drop.

The net effect: a directional recommendation now rests on the legs that actually carry directional edge (conditions, backtest, signal), sized and moderated by the non-directional forecasts, with the honest-but-weak direction forecast unable to either block or decide it.

## Consequences

### Positive
- **The weak leg stops governing.** A corroborated setup is no longer vetoed by a direction forecast that has no reliable opinion; the recommendation reflects the signals that actually carry edge.
- **The strong new signals get used without overreach.** Volatility and regime improve sizing/stops/conviction — the things they can honestly inform — without being forced into a directional role they can't fill.
- **Honesty is preserved structurally.** The directional-safety invariant (a call requires the voting legs to agree) is untouched; the demotion and the non-voting inputs are both recorded in the replayable gate trace.

### Negative
- **`fuse()`'s contract changes — this amends ADR-0029.** The conjunctive gate is no longer "all four legs agree"; it is "all *voting* legs agree, with the direction leg conditionally voting." That is a real loosening of a safety property and must be reasoned about, not slipped in. The threshold is a tuning knob that can be set wrong.
- **A threshold is a new magic number.** "Skill margin below X ⇒ non-gating" needs a defensible default and a test pinning the behavior at the boundary; set too low it changes nothing, too high it demotes a genuine edge.
- **More inputs, more ways to be subtly wrong.** Sizing/stop math driven by a volatility forecast can misbehave (e.g. a bad vol prediction → absurd size); the plan must bound these and keep unpriced/degenerate forecasts from producing dangerous outputs.

### Neutral
- No order is ever placed — this is still advisory only ([ADR-0025](0025-trade-execution-feasibility.md) untaken). Sizing and stops are *recommended* numbers the user acts on manually.

## Alternatives considered

### Alternative A — Give volatility/regime veto or directional power
Let a vol spike or an adverse regime veto a directional call, or let regime imply a direction. Rejected: it over-empowers non-directional signals (a vol forecast genuinely isn't long/short) and would loosen the directional invariant far more than necessary. Non-voting sizing/stop influence captures their real value without letting them pretend to call direction.

### Alternative B — Keep direction fully gating, just add the new inputs
Add vol/regime as non-voting inputs but leave the direction leg's veto intact. Rejected because it leaves the core problem — a no-edge direction leg vetoing corroborated setups — completely unaddressed. The whole motivation was that the weak leg governs.

### Alternative C — Standalone forecasts, no advisor wiring at all
Ship the vol/regime tools + UI and never wire them into `recommend`. Rejected for this plan because the sizing/stop value is exactly where a trader wants them; deferring the wiring indefinitely wastes the new signal. (A phased delivery still ships the tools before the wiring — the plan sequences the edge verdict first — but the wiring is in scope, not deferred.)

## Notes

- This ADR **amends** [ADR-0029](0029-advisory-recommendation-boundary.md) (the fuse contract) rather than superseding it: the advisory boundary (app may recommend, not act) and the directional-agreement-of-voting-legs invariant both stand; only the direction leg's gating status and the addition of non-voting inputs change.
- The skill-margin threshold and the sizing/stop formulas are Plan 0077 deliverables with pinned tests; this ADR fixes the *shape* of the decision, not the constants.
