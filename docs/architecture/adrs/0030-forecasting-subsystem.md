# ADR-0030 — Forecasting subsystem: causal, validated, direction-as-probability

> **Status:** proposed (accepts when the first forecasting plan closes — see Notes)
> **Date:** 2026-05-30
> **Related plan(s):** none yet — [Plan 0020](../plans/0020-backtest-metrics-walk-forward.md) (walk-forward) is a hard prerequisite (see Notes)
> **Related ADRs:** [ADR-0018](0018-backtest-result-schema.md) (the determinism contract this mirrors), [ADR-0024](0024-extended-backtest-metrics.md) (the walk-forward machinery this reuses for validation), [ADR-0023](0023-technical-analysis-surface.md) (the feature source), [ADR-0007](0007-market-data-provider.md) (bars come through the Protocol), [ADR-0012](0012-dependency-cooldown.md) / [ADR-0013](0013-pin-direct-dependencies.md) (the dep discipline that shapes the model-library choice), [ADR-0029](0029-advisory-recommendation-boundary.md) (the advisor consumes forecasts as a conviction input)

## Context

`market-analyser` is, by design, a **trailing, anti-lookahead** condition-detection and historical-backtest tool. The cardinal cross-cutting rule (CLAUDE.md): *"A decision at bar `i` only sees data from `bars[0..=i]`. Indicators must be trailing, not centered."* The app describes what *was* and what *is*; it has never made a forward statement. The user, driving their own app, has asked for **forward price forecasting / ML**.

A clarification that shapes the whole decision: **forecasting is not a violation of the no-lookahead rule.** The rule forbids a decision at bar `i` from seeing data *after* `i`. A model trained only on past data to predict the future respects that completely — causality is intact. What forecasting introduces is not lookahead but a *new failure surface*: **leakage** in feature construction (a centered indicator, a normalisation that uses full-series statistics, a label that bleeds into its features) and **overfitting** (a model that memorises in-sample noise and predicts nothing out-of-sample). Both are defeatable, but only with discipline the repo does not yet apply to a model: strict causal feature construction, mandatory out-of-sample validation, seeded determinism, and honest uncertainty.

Three constraints make the *shape* of this a real decision rather than a default. First, the **dependency discipline** ([ADR-0012](0012-dependency-cooldown.md) 14-day cooldown + [ADR-0013](0013-pin-direct-dependencies.md) exact pinning, applied to dev tooling too): a heavy deep-learning stack is high-friction to pin and cooldown, and brings GPU/runtime concerns onto a personal Windows desktop. Second, the **determinism non-negotiable** ([ADR-0018](0018-backtest-result-schema.md)): model training is a rich source of nondeterminism (parallelism, hash order, float reductions, library internals) that must be pinned down to keep results reproducible. Third, **markets are near-efficient** on liquid instruments — the honest prior is *modest directional skill at best, often none after costs* — so the subsystem must be built to detect and honestly report "no edge," not pressured to tune until it overfits.

## Decision

We will add a **forecasting subsystem** (`src/market_analyser/forecast/`) that predicts **next-bar (and configurable N-bar-ahead) directional movement as a calibrated up / down / flat probability**, built **phased**: start with **classical statistical models (ARIMA/ETS-class) and gradient-boosted trees over engineered features sourced from the [ADR-0023](0023-technical-analysis-surface.md) `analysis/` surface**; defer deep-learning sequence models to a later phase gated on a follow-up ADR. The subsystem is bound by four invariants, and a plan that violates any of them fails review:

1. **Strict causality / no leakage.** Every feature at bar `i` is computed from `bars[0..=i]` only — the same anti-lookahead rule as backtests, enforced inside the feature pipeline (no centered indicators, no full-series normalisation, no label bleed).
2. **Determinism.** Seeded training, pinned library versions, reproducible inference. A model retrained from the same data + seed produces byte-identical predictions modulo documented provenance, mirroring [ADR-0018](0018-backtest-result-schema.md)'s contract.
3. **Mandatory walk-forward validation.** No forecast ships without rolling out-of-sample evaluation (reusing [ADR-0024](0024-extended-backtest-metrics.md)'s walk-forward machinery). Reported skill must **beat a naive baseline** (persistence / majority-class) or the model is rejected, not shipped.
4. **Honest uncertainty.** Outputs are calibrated probabilities carrying the baseline and the validation skill alongside them. No point-price forecasts; no false precision.

**Direction-as-probability** is chosen because it is the most honest about noise, maps directly to the advisor's conviction ([ADR-0029](0029-advisory-recommendation-boundary.md)), and is the hardest target to dress up as false certainty. **Classical + trees, phased** is chosen because it is deterministic, lightweight, and explainable (feature importances, interpretable parameters), and it fits the cooldown/exact-pin discipline; deep learning's dep weight, runtime needs, and determinism difficulty make it the wrong day-one bet for uncertain marginal accuracy.

## Consequences

### Positive
- Delivers the forward-looking capability the user wants **on the causal grain**, not against it — leakage and overfitting are the named risks, and the invariants target them directly.
- **The [ADR-0023](0023-technical-analysis-surface.md) indicator surface doubles as the feature library** — reuse of already-validated, already-trailing math instead of a parallel feature stack.
- **The walk-forward gate ([ADR-0024](0024-extended-backtest-metrics.md)) means we ship validated edge or nothing.** A model that beats no baseline is rejected before it can mislead — the discipline is structural, not aspirational.
- **Explainable models keep "no invented certainty" enforceable** — tree feature importances and classical parameters make a forecast inspectable, so a confident-but-spurious output is visible at review.
- Feeds [ADR-0029](0029-advisory-recommendation-boundary.md)'s advisor a calibrated probability that maps directly onto recommendation conviction — the two ADRs compose.

### Negative
- **Overfitting is the dominant failure mode and it is seductive.** A model that scores beautifully in-sample and predicts noise out-of-sample *looks* like success. The walk-forward + baseline-beating gate is the defence, but the discipline must hold every single time — one un-validated model shipped is a confident lie that can cost real money via the advisor.
- **It changes the product's epistemic stance** from "here are the conditions" to "here is a prediction," and users over-trust predictions — even a calibrated 0.55 gets read as a certainty. That is a permanent communication tax the honest-uncertainty invariant only partially offsets.
- **Determinism is harder here than anywhere else in the repo.** Training pipelines pull in nondeterminism from parallelism, hash ordering, float-reduction order, and library internals. Pinning it down (seeds, single-threaded or determinism-flagged training, pinned libs, frozen feature order) is real, ongoing work and a standing regression risk that the golden-determinism discipline must now cover model artifacts too.
- **New dependency footprint under the cooldown/pin policy.** Even classical + trees means `statsmodels` / `scikit-learn` / a gradient-boosting library, each exact-pinned, each subject to the 14-day cooldown and the weekly-bump chore; model persistence adds a serialization-format-versioning concern. The dep surface grows materially.
- **The honest expected payoff is small.** On liquid instruments the subsystem may correctly conclude "no edge after costs." That must be an **acceptable, non-embarrassing result** — the existence of a forecasting module must not become pressure to keep tuning until something overfits its way to a good-looking number.

### Neutral
- `proposed` until a forecasting plan commits; accepts at that plan's close. [Plan 0020](../plans/0020-backtest-metrics-walk-forward.md) (walk-forward) is a hard prerequisite — its out-of-sample machinery is invariant 3's backbone.
- The deep-learning door is left open behind a future ADR — deferred, not foreclosed.
- Model artifacts persist as data (under a gitignored `models/` or `runs/`-style root; exact layout deferred to the plan). They are data, not credentials — no secret-handling concern.

## Alternatives considered

### Alternative A — Deep learning (LSTM / Transformer) from day one
Sequence models on the raw series from the start. **Rejected** because the heavy dependency stack clashes head-on with the cooldown/exact-pin policy, GPU/runtime expectations sit poorly on a personal Windows desktop, determinism is materially harder to guarantee, and explainability drops to near zero — all for uncertain marginal accuracy on noisy financial series where feature-based gradient-boosted trees are competitive. Deferred behind its own ADR, not foreclosed: if the classical/tree phase proves there is signal worth chasing, DL becomes a deliberate next decision.

### Alternative B — Build classical/tree *and* deep learning together
Both paths in v1. **Rejected** because it doubles the validation, determinism, and dependency surface for a first version whose only real job is to prove that *any* out-of-sample edge exists at all. Phase it: prove edge with the lighter, deterministic, explainable stack first; escalate to DL only if warranted.

### Alternative C — Predict price level or return magnitude instead of direction
Regress the future price, or the size of the next move. **Rejected** because price-level and magnitude regression are the most prone to spurious precision and the easiest to present with false confidence — the exact opposite of the honest-uncertainty posture this subsystem must hold. Direction + calibrated probability is the epistemically defensible target; a magnitude output can be an additive later feature *if* direction first demonstrates real skill.

### Alternative D — No forecasting; rely on backtested edge only
Stay backtest-only. **Rejected as the product decision** (noted as the prior default it replaces): a backtest describes how a *fixed rule* performed historically — it is not a forward statement. The user explicitly wants a forward view. Remaining backtest-only is the anti-lookahead-purist reading of [ADR-0015](0015-claude-code-primary-control-surface.md)'s grain, but the user has chosen to cross into (causal) forecasting; this ADR's job is to make that crossing disciplined rather than ad hoc.

## Notes
- **Causality, restated for the record:** forecasting does *not* breach the no-lookahead non-negotiable. The rule bars a decision at bar `i` from seeing data after `i`; a causal model trained on the past to predict the future honors it. The live risk is *leakage* during feature construction — same rule, enforced at a new point (the feature pipeline), which is why invariant 1 is first.
- **Prerequisite ordering:** [Plan 0020](../plans/0020-backtest-metrics-walk-forward.md) (extended metrics + walk-forward, [ADR-0024](0024-extended-backtest-metrics.md)) provides the rolling out-of-sample evaluation that invariant 3 mandates. Forecasting plans sequence *after* it.
- **What committing would require, in order:** (1) user go/no-go on this posture; (2) Plan 0020 closed (walk-forward available); (3) a forecasting plan defining the feature pipeline, the model registry/persistence, the `forecast` MCP tool, and a forecast UI surface (its own `ui-builder` phase), with the four invariants as acceptance criteria.
- Feeds [ADR-0029](0029-advisory-recommendation-boundary.md)'s advisor as a conviction input; the two were decided together (2026-05-30) and compose but are independent decisions.
