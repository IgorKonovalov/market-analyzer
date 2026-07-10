# ADR-0070 — Non-directional forecast targets: volatility + regime-transition as distinct forecast kinds

> **Status:** proposed — accepts at Plan 0077's close
> **Date:** 2026-07-10
> **Related plan(s):** 0077

## Context

The forecasting subsystem ([ADR-0030](0030-forecasting-subsystem.md)/[ADR-0040](0040-forecasting-model-artifacts.md)) forecasts exactly one thing: next-period **direction** as a calibrated up/down/flat probability, gated by a walk-forward-beats-baseline test. Its own honesty artifacts show this target has essentially no edge: the v1 OHLCV-only feature set beats no baseline at any horizon on 11.4 years of BTC-USD, and even the richest evaluable tier (v2-deep — [ADR-0057](0057-forecast-feature-set-tiers.md)) only clears the gate at h=21, and only in some fold layouts ([Plan 0062](../plans/done/0062-forecast-feature-set-tiers.md)). This is not an implementation defect — **direction-of-return on a liquid asset is close to a coin flip**, which is exactly what the gate keeps honestly reporting.

Two targets are far more forecastable and more useful to the crypto-first ([ADR-0069](0069-crypto-first-asset-class-positioning.md)) trader this tool serves:

- **Volatility.** Volatility clusters — realized vol is autocorrelated in a way returns are not. A model that predicts next-N-bar realized volatility can clear a deterministic baseline, and the output directly drives position sizing, stop distance, and "expect a big move" context.
- **Regime.** Whether the market is trending vs ranging, quiet vs volatile, conditions strategy selection and conviction. A trailing regime classification plus a *transition* forecast (how likely the regime changes next period) is a genuinely predictive, non-circular target — distinct from the current-state crypto-macro nowcast of [ADR-0027](0027-crypto-macro-regime-classification.md).

The subsystem already owns the machinery these need — purged walk-forward validation, tiered feature sets, determinism, OOS permutation-importance explainability ([ADR-0058](0058-forecast-recommendation-explainability.md)). The decision is whether to expand the subsystem's *targets* beyond direction and how to keep each target's honesty story clean, not whether to rebuild the harness.

## Decision

We expand the forecasting subsystem to support **non-directional forecast targets**, each a distinct forecast *kind* under the same ADR-0030 contract (causal features, purged walk-forward, must-beat-a-deterministic-baseline gate, per-forecast provenance + explainability):

1. **Volatility forecast** — a regression predicting realized volatility over the horizon (from log returns / OHLC-based estimators), scored against a **deterministic** baseline (EWMA and naive-persistence realized vol). The ML tier reuses the existing sklearn `HistGradientBoostingRegressor`; it is reported as edge-bearing only when it beats the baseline out-of-sample, otherwise the baseline is surfaced as the honest answer.
2. **Regime-transition forecast** — a **deterministic, trailing** current-regime classification (from trend + volatility primitives, no lookahead) plus a classifier predicting the next-period regime, scored against a **persistence** baseline (regime unchanged). Reuses `HistGradientBoostingClassifier`.

The existing **direction** forecaster is **kept but demoted** (its advisory role is handled in [ADR-0071](0071-advisor-non-directional-inputs-and-direction-demotion.md)); it is surfaced only where it honestly beats baseline. Each kind has its own baseline, its own beats-baseline verdict, and its own artifact — they are **not** blended into one score. No new runtime dependency is introduced: baselines are deterministic (EWMA/persistence), regime classification is rule-based, and the ML models reuse the sklearn stack already pinned. GARCH volatility and HMM regime models (which would add dependencies) are explicitly out of scope for this decision and left as possible future baselines.

## Consequences

### Positive
- **A forecast that can actually clear its gate.** Volatility gives the subsystem a target where an honest edge is attainable, turning "no edge, everywhere" into a real, usable output.
- **Directly actionable, non-directional signal.** Predicted vol drives sizing and stops; regime transition drives conviction and strategy context — both useful without pretending to call direction.
- **Reuses the honesty machinery wholesale.** Each new kind inherits the walk-forward gate, determinism, and explainability, so the "beats baseline or says so" contract holds uniformly across all three kinds.
- **No new dependencies.** Deterministic baselines + rule-based regime + the existing sklearn models keep the dependency surface (and the determinism contract) unchanged.

### Negative
- **More surface, more validation stories.** Three forecast kinds means three baselines, three edge verdicts, three sets of tests and provenance to maintain — more to keep honest.
- **Volatility scoring is subtler than accuracy.** A regression edge over an EWMA baseline needs a proper scoring rule (e.g. QLIKE / MSE on log-vol) and careful OOS comparison; a naive metric can flatter or bury the model. The plan must pin the scoring rule.
- **Regime taxonomy is a judgement call.** A deterministic rule-based regime is non-circular but its boundaries (what counts as "volatile" vs "quiet") are chosen, not learned; a poor taxonomy produces a well-validated forecast of an unhelpful label.

### Neutral
- The direction forecaster's code path stays; this ADR adds targets rather than removing one. The `forecast/` package grows a volatility and a regime module beside the existing direction path.

## Alternatives considered

### Alternative A — Keep direction-only, just add features / tune
Stay with direction and keep hunting for exogenous features that tip the baseline. Rejected because the target itself is the problem: three feature generations (v1, v2, v2-deep) have shown direction-of-return is near-random, and no feature set has produced a horizon-robust edge. Changing the target is the higher-leverage move.

### Alternative B — One unified "market-state" model emitting vol + regime together
Co-emit volatility and regime from a single pipeline/artifact. Rejected because it couples two different target types and two validation stories into one thing: a weak regime story would drag the vol story, and the per-kind beats-baseline honesty gets muddier. Distinct kinds sharing the harness keep each verdict clean (this mirrors the plan-level "two independent forecasters sharing the harness" choice).

### Alternative C — Supervised regime with defined "ground-truth" labels
Label historical regimes and train an ML classifier to predict the next label directly. Rejected as lookahead-prone and circular: defining "true" regime typically peeks at forward data, and the model then learns to reproduce a label that already encodes the answer. A trailing deterministic classification + a persistence-baselined transition forecast avoids the circularity.

## Notes

- The regime here is **per-symbol and technical** (trend + vol), predictive via transition probability — deliberately distinct from [ADR-0027](0027-crypto-macro-regime-classification.md)'s crypto-macro **current-state** neutral classification. Plan 0077 must document the relationship so the two don't drift into overlap.
- Cross-asset validation (BTC + ETH) is a Plan 0077 scope choice, not an ADR invariant: an apparent edge on one asset's one history is an overfit until it replicates.
