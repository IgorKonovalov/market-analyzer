# ADR-0096 — Screening quality rank stays on the conditions side of ADR-0029

> **Status:** accepted (Plan 0101 closed 2026-07-15 — the composite ships as a screening rank with factor decomposition + liquidity gate and no call-shaped field, guarded at model + serialized-JSON level; the advisor may consume it)
> **Date:** 2026-07-13
> **Related plan(s):** [0101](../plans/0101-composite-quality-rank.md)

## Context

The inspiration project exposes a 100-point composite stock "quality/momentum score" with a grade ladder (Elite / Strong / Watchlist / Avoid) and a hard liquidity gate. A composite score that ranks a set of names by technical quality is genuinely useful as a screening aid — it collapses a dozen indicators into one comparable number.

But this project draws a hard line ([ADR-0029](0029-advisory-recommendation-boundary.md)): analyst-facing surfaces report **conditions**; only the `advisor` layer emits **directional calls**, always labeled advisory, always carrying rationale and a backtested/forecast basis. A grade like "Strong" or "Avoid" reads as "buy" or "sell" — it is a directional call wearing a score's clothing. We want the screening utility without opening a second directional surface that bypasses the advisor.

The tension is real and worth pinning: the score is high-value, and the obvious port (grade included) would breach the boundary silently, because a ranked list *feels* like a screen even when its labels are verdicts.

## Decision

We will ship the composite as a **screening rank, not a verdict**. It outputs a normalized composite score (0–100) decomposed into named factor contributions (trend, momentum, volume, volatility) plus a **liquidity gate** that flags/caps illiquid names — and nothing else. It carries **no buy/sell/hold grade, no action, no conviction, no entry/stop/target**. Any label it attaches describes the *quality of the technical setup* in condition terms (e.g. "aligned" / "mixed" / "weak-structure"), never a trade recommendation. The tool lives on the analyst-consumed `analysis/` surface and ranks a caller-supplied watchlist over cached bars (reusing the [ADR-0095](0095-watchlist-scan-fanout-harness.md) harness). The `advisor` **may consume** the rank as one more input to `recommend`; the rank itself never crosses into advice.

## Consequences

### Positive
- The screening/ranking utility lands without a second directional surface leaking past ADR-0029.
- The factor decomposition is transparent — a caller sees *why* a name scores high, not a black-box number.
- The advisor gains a richer, pre-computed condition input.

### Negative
- A user who wants a one-word "should I buy this" from the rank won't get it here — they must go to `advisor`. **This friction is intentional**, but it is friction, and it will occasionally annoy.
- Composite factor weighting is inherently opinionated; someone will disagree with the weights. Mitigation: weights are named module constants, documented and tunable — but that is a maintenance surface.

### Neutral
- The liquidity gate needs a per-asset-class threshold (crypto vs equity notional), tracked in config alongside the weights.

## Alternatives considered

### Alternative A — Port the upstream grade ladder verbatim (Elite / Strong / Avoid)
Rejected: those labels are buy/sell in disguise. Shipping them would breach ADR-0029's core invariant — that only the advisor speaks directionally — under cover of a "score".

### Alternative B — Put the score inside the advisor instead of the analyst surface
Rejected: it is a screening/condition read over *many* symbols, not a single-symbol fused call. It belongs with conditions, and folding a multi-symbol ranker into the advisor would distort the advisor's single-symbol `recommend` contract. The advisor consumes the rank; it does not own it.
