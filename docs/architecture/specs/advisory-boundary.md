# Spec — Advisory boundary (conditions vs. calls)

> **Subsystem:** The fact/decision boundary — analyst skills report conditions and never recommend; the single sanctioned carve-out is the `advisor` layer, which may recommend (labeled, basis-backed) but never acts.
> **Source:** src/market_analyser/advisor/ (Recommendation model + fusion; consumed by the `advisor` skill via the `recommend`/`recommend_rebalance` tools), and the read-only analyst surfaces under `analysis/` and `defi/`
> **Reconciled-through:** Plan 0114
> **Governing ADRs:** 0029-advisory-recommendation-boundary, 0015-claude-code-primary-control-surface, 0025-trade-execution-feasibility, 0068-technical-read-advisory-tier, 0071-non-directional-forecasts-non-voting

The whole skill ecosystem is built on one load-bearing principle: *conditions are
facts, decisions are the user's.* This spec states where that boundary holds, where
it is deliberately relaxed, and the three rules that contain the crossing.

## Invariants

- **Analysts report conditions, never calls.** The analyst skills (`market-analyst`,
  `defi-analyst`) and the code they consume (`analysis/`, `defi/`) MUST report
  conditions only — trend, momentum, patterns, levels, pool/lending health — and MUST
  NOT emit buy / sell / short / exit / hold / size / rebalance recommendations. A
  recommend path added to an analyst surface is a boundary violation.  (ADR-0015; ADR-0029; CLAUDE.md)

- **Exactly one carve-out layer may recommend.** The `advisor` layer
  (`src/market_analyser/advisor/`, surfaced by the `advisor` skill) is the *only*
  component permitted to turn conditions into a directional call. The crossing lives
  in that one layer and MUST NOT leak back into the analysts, which keep their
  read-only contract unchanged.  (ADR-0029 Decision)

- **Every recommendation is labeled advisory.** A recommendation the advisor emits
  MUST be labeled as advisory output the user acts on manually — never framed as an
  instruction or an executed action.  (ADR-0029 carve-out rule 1)

- **Every recommendation carries rationale + basis with honest uncertainty.** A
  recommendation MUST carry the conditions/signals/forecasts that fired (rationale)
  and its backtested / walk-forward / forecast basis, with honest uncertainty — no
  bare calls, no false precision. A basis-free or unexplained recommendation is a
  review finding, enforced at the artifact shape, not by good intentions.  (ADR-0029 carve-out rule 2)

- **The user remains the decision-maker; the advisor never acts.** The advisor MUST
  NOT place, prepare, or simulate an order, and MUST NOT hold or touch a
  trade-permissioned secret. It reads data and writes advisory artifacts; execution
  is [ADR-0025](../adrs/0025-trade-execution-feasibility.md)'s separate, untaken
  decision. Any advisor code that submits an order, or logs/serializes a trade key,
  is an immediate blocker.  (ADR-0029 carve-out rule 3; ADR-0025)

- **The advisor consumes analyst *outputs*, not internals.** The advisor is a
  downstream fusion consumer of `analysis/`, `strategies/`, `backtest/`, and
  `forecast/` outputs; it MUST NOT reach into an analyst skill's internals or grant
  an analyst a recommend capability as a side effect.  (ADR-0029)

- **The lesser advisory tier obeys the same containment.** The single-indicator
  `technical_read` tier (ADR-0068) extends the carve-out without widening it: it may
  give a bounded technical read but carries no conviction/levels of a full
  recommendation and still never acts.  (ADR-0068, extending ADR-0029)

- **A directional call requires the *voting* legs to agree — and the direction leg
  votes only conditionally.** A `long`/`short` verdict MUST require every *gating*
  check to pass: at least one live strategy signal implying that direction with none
  opposing, and a positive walk-forward edge belonging to a strategy that actually
  voted. The direction forecast leg is a **conditional** voter: it gates only when its
  own out-of-sample beats-baseline margin clears the pinned skill-margin threshold
  (the constant lives in `advisor/fusion.py`; this spec deliberately does not restate
  its value). Below that threshold the leg is **demoted to advisory** — it MUST NOT
  veto a call the voting legs corroborate, and it MUST NOT be the sole deciding vote.
  A demoted leg still cannot *manufacture* a call. The demotion MUST be recorded
  explicitly in the replayable gate trace (`gating=False` rows plus a
  `reason.direction_leg_nongating` code), never silently dropped. Non-directional
  forecasts (volatility, regime-transition) are **non-voting**: they may shape sizing,
  stop distance and conviction magnitude, but MUST NOT flip or conjure a direction.
  (ADR-0071, amending ADR-0029's all-legs-agree gate)

- **Conviction is derived from what actually shipped, never invented.** Conviction MUST
  be the walk-forward edge credit (the aggregate out-of-sample `sharpe_mean`, clamped to
  full credit at its pinned ceiling) multiplied by the calibrated forecast probability of
  the called direction **only when the direction leg shipped one**, then scaled by the
  non-voting regime factor and bounded to `[0, 1]`. When the leg is demoted and ships no
  probability — the common case, since the direction forecaster rarely beats baseline
  (ADR-0070) — conviction rests on the backtested edge **alone**. A consumer narrating
  conviction MUST therefore say *which* branch produced it: a saturated `1.0` from edge
  credit alone is a statement about a backtest, not a probability of being right, and
  reporting it as certainty is a boundary violation of the honest-uncertainty rule above.
  Conviction MUST NOT be rounded up, re-derived, or editorialised downstream.
  (ADR-0071; ADR-0029 carve-out rule 2)

## Scenarios

- WHEN the user asks "what's the trend on SPY" / "check my Aave health" THEN an
  analyst answers with conditions only — no entry, stop, target, or rebalance move.
  (ADR-0015; `market-analyst` / `defi-analyst`)

- WHEN the user asks "should I buy AAPL" / "long or short here" / "rebalance my book"
  THEN the `advisor` layer answers, fusing conditions + live signal + walk-forward
  edge + forecast into a labeled recommendation *carrying rationale and basis* — or an
  honest "no actionable edge" flat.  (ADR-0029; `advisor/`)

- WHEN the advisor would emit a directional call with no backtested/forecast basis or
  no rationale THEN that recommendation is malformed and fails review — the artifact
  shape requires both.  (ADR-0029 carve-out rule 2)

- WHEN any request would have the app place, prepare, or simulate an order THEN it is
  refused — execution does not exist in this app (ADR-0025, untaken); the advisor
  stops at the advisory artifact.  (ADR-0025; ADR-0029)

- WHEN a plan proposes adding a recommend/advice path to `market-analyst` or
  `defi-analyst` THEN it is a boundary violation to be redirected to the advisor
  layer, not folded into the analyst.  (ADR-0029 Alternative A, rejected)

- WHEN the direction forecast ships no probabilities (`edge_strength: "no_edge"`,
  `edge_margin` below the pinned threshold) but the live signal and a positive
  walk-forward edge agree THEN the verdict is **directional**, not flat: the demoted
  leg does not veto it, the gate trace records the demotion as non-gating, and
  conviction equals the edge credit scaled by the regime factor — carrying no
  probability. A flat verdict here would be the pre-ADR-0071 behaviour and is a
  regression.  (ADR-0071)

- WHEN a consumer reports such a conviction THEN it MUST name the branch — "edge
  credit alone, no forecast probability" — rather than presenting a saturated value as
  confidence that the call is right.  (ADR-0071; ADR-0029 carve-out rule 2)

## Known gaps / honest nulls

- **Execution is deliberately absent.** The advisor produces the artifact
  ADR-0025's assisted-first invariant would consume, but ADR-0025 is untaken: there is
  no order layer, no trade key, no kill switch. "Advisory" is the ceiling by design,
  not a missing feature.

- **The advisor is the most integration-coupled component.** It depends on
  `analysis/`, `strategies/`, `backtest/`, and `forecast/`, so it is sensitive to
  drift in all of them; correctness of a recommendation is only as good as everything
  it fuses. This coupling is inherent to the fusion role, not a defect to remove.

- **The boundary is a contract, not a compiler check.** Nothing mechanically prevents
  an analyst surface from drifting into advice; the containment is enforced by ADR,
  skill contract, and Mode 4 review — a structurally-valid analyst response that
  slips in a call is caught by review, not by a gate.
