# ADR-0040 — Forecasting model artifacts: deterministic library stack + versioning & provenance

> **Status:** proposed — accepts at the first forecasting plan close ([Plan 0036](../plans/0036-forecasting-subsystem-foundation.md))
> **Date:** 2026-06-05
> **Related plan(s):** [Plan 0036](../plans/0036-forecasting-subsystem-foundation.md) (forecasting subsystem foundation)
> **Related ADRs:** [ADR-0030](0030-forecasting-subsystem.md) (the forecasting posture this implements — library/determinism/persistence were left open there), [ADR-0018](0018-backtest-result-schema.md) (the determinism contract this mirrors for model artifacts), [ADR-0012](0012-dependency-cooldown.md)/[ADR-0013](0013-pin-direct-dependencies.md) (the cooldown + exact-pin discipline that shapes the library choice), [ADR-0024](0024-extended-backtest-metrics.md) (the walk-forward machinery the validation reuses)

## Context

[ADR-0030](0030-forecasting-subsystem.md) decided the *posture* of the forecasting subsystem — direction-as-probability, classical statistics + gradient-boosted trees first (deep learning deferred), four invariants (causality / determinism / mandatory walk-forward / honest uncertainty). It deliberately left three sub-decisions to the implementing plan, and Plan 0036 cannot be drafted without them settled:

1. **Which gradient-boosting library**, given the determinism non-negotiable and the cooldown/exact-pin policy. A 2026-06-05 research pass (adversarially verified) confirmed the live state: both **LightGBM** and **XGBoost** are only reproducible under a *fixed seed + single thread*, and both carry documented nondeterminism gotchas (LightGBM #6320, XGBoost #5023) — multithreaded training, certain `tree_method`/histogram paths, and subsampling can all break byte-identical reruns. Each is also a *separate native dependency* to exact-pin and cooldown-track. Meanwhile `scikit-learn` (already required by ADR-0030's "classical" half) ships `HistGradientBoostingClassifier` — a histogram gradient-boosting implementation that is reproducible given `random_state`, with **no extra dependency**.

2. **How a trained model is persisted and versioned.** A forecast is only reproducible if the exact artifact that produced it can be identified and re-loaded. ADR-0018's determinism contract ("same inputs + seed → byte-identical outputs modulo documented provenance") was written for the backtest engine; a *trained model* is a new kind of persisted artifact it never covered. Without a versioning scheme, a retrained model silently changes forecasts and no one can tell which model said what.

3. **What provenance a forecast carries.** The roadmap names "Model versioning and determinism" and "LLM provenance metadata" as ADRs that must exist before the first persisted model output ships. A forecast is the first model output; it needs a "produced by `<feature-set>@<model-version>` trained through `<cutoff>` with seed `<n>` on libs `<versions>`" trail to be auditable and to make the determinism claim checkable.

This is the user's own single-user desktop app; none of the above demands enterprise MLOps. It demands *enough* discipline that a forecast is reproducible and a model is identifiable — and no more.

## Decision

We will build the forecasting model stack on **scikit-learn (including `HistGradientBoostingClassifier`) + statsmodels as the day-one library set**, defer LightGBM/XGBoost behind a future escalation, persist models as **versioned, self-describing artifacts**, and attach a **provenance record to every forecast**. Concretely:

> **1. Library stack.** Day-one dependencies are `scikit-learn` and `statsmodels`, both exact-pinned (`==X.Y.Z`) under the 14-day cooldown ([ADR-0013](0013-pin-direct-dependencies.md)/[ADR-0012](0012-dependency-cooldown.md)). The gradient-boosted-tree model is sklearn's **`HistGradientBoostingClassifier`** — it satisfies ADR-0030's "trees" requirement with zero additional native dependency and is reproducible via `random_state`. LightGBM/XGBoost are **not adopted now**; if a later phase demonstrates signal worth chasing and the sklearn implementation is the bottleneck, adopting one is a deliberate follow-up (single-threaded + seeded, its own dependency-add commit) — not a day-one bet.
>
> **2. Determinism mechanism.** Every model is trained with an explicit `random_state`/seed, **single-threaded** in the financially-meaningful path (no thread-count-dependent float-reduction order), over a **frozen feature order**, against pinned library versions. A model retrained from the same training window + seed produces byte-identical predictions modulo documented provenance — the [ADR-0018](0018-backtest-result-schema.md) contract, extended to model artifacts. A golden determinism test pins this cross-run.
>
> **3. Model persistence + versioning.** Trained models persist as artifacts under a gitignored `models/` root (sibling to `runs/`), each carrying a **`model_version`** = a deterministic hash over `(feature-set id, model class + hyperparameters, training-window cutoff, library versions, seed)`. The same inputs always produce the same `model_version`; any change to features, hyperparameters, training window, or a pinned lib produces a new one. Artifacts are **data, not credentials** — no secret-handling concern.
>
> **4. Forecast provenance.** Every forecast output carries: the `model_version` that produced it, the feature-set id, the training-window cutoff, the seed, the pinned library versions, and the walk-forward validation skill + baseline it was accepted against ([ADR-0024](0024-extended-backtest-metrics.md) machinery, ADR-0030 invariant 3). A forecast without this trail is a review blocker.

**HistGradientBoosting-first** is chosen because it delivers ADR-0030's tree model with the *smallest* determinism-and-dependency surface — the two constraints that dominate this repo. **Hash-based `model_version`** is chosen because it makes "which model said this" answerable from the forecast alone and makes the determinism claim falsifiable (same inputs must hash the same).

## Consequences

### Positive
- **Zero new native dependency for the tree model.** sklearn is already in scope per ADR-0030; `HistGradientBoostingClassifier` rides it. The dependency surface under the cooldown/pin policy grows by `statsmodels` only (plus sklearn, already needed) — not by a separately-versioned C++ boosting library.
- **Determinism is achievable without fighting a library.** sklearn's `random_state` + single-thread is a well-trodden reproducibility path; we sidestep the LightGBM/XGBoost multithread/histogram gotchas the research surfaced entirely.
- **A forecast is self-describing and auditable.** `model_version` + provenance means any persisted or surfaced forecast can be traced to its exact model and validation basis — the precondition for the advisor ([ADR-0029](0029-advisory-recommendation-boundary.md)) to consume forecasts honestly.
- **Extends the ADR-0018 determinism discipline cleanly** to a new artifact class rather than inventing a parallel reproducibility story.

### Negative
- **We may leave accuracy on the table by not using LightGBM/XGBoost day one.** They are sometimes marginally stronger on tabular data. The bet is that on noisy financial series the gap is within the noise the walk-forward gate already filters, and that determinism + a smaller dependency surface is worth more than a marginal-and-possibly-illusory accuracy edge. If wrong, the escalation path is open — but it is a real, deliberate cut.
- **`model_version` hashing is load-bearing and easy to get subtly wrong.** If the hash omits an input that actually affects predictions (a hyperparameter, a lib version), two genuinely-different models collide on one version and the determinism guarantee silently breaks. The hash inputs must be audited against everything that touches training.
- **Single-threaded training is slower.** For a single-user desktop on liquid-instrument histories this is acceptable, but it caps model/feature scale; a future large-feature model may force a determinism-vs-throughput revisit.
- **A `models/` artifact root adds a persistence concern** (format versioning, stale-artifact cleanup) the repo did not have. It is gitignored data, but it is state that can drift from the code that reads it.

### Neutral
- `proposed` until Plan 0036 closes; accepts at that close, at which point these four points become Plan 0036's acceptance criteria — same cadence as [ADR-0023](0023-technical-analysis-surface.md)/[ADR-0024](0024-extended-backtest-metrics.md).
- This ADR does not change [ADR-0030](0030-forecasting-subsystem.md); it fills the three implementation slots ADR-0030 left open and is fully compatible with its four invariants.
- The deep-learning door ADR-0030 left open stays open behind its own future ADR; this ADR's library decision governs only the classical/tree phase.

## Alternatives considered

### Alternative A — LightGBM or XGBoost as the day-one tree library
The conventional tabular-ML pick. **Rejected for day one** because the 2026-06-05 research confirmed both are only deterministic under fixed-seed + single-thread and carry documented nondeterminism gotchas, and each is a separate native dependency to pin and cooldown-track — all to gain a marginal, possibly-illusory accuracy edge on noisy series the walk-forward gate already filters. sklearn's `HistGradientBoostingClassifier` gives the same model class with no extra dependency and a cleaner determinism story. The escalation is deferred, not foreclosed.

### Alternative B — No separate model-versioning scheme; rely on the training-window cutoff alone
Identify a model by "trained through date D." **Rejected** because the cutoff is necessary but not sufficient: two models trained through the same date but with different features, hyperparameters, or pinned-lib versions are different models that produce different forecasts. A hash over *all* prediction-affecting inputs is what makes "which model said this" answerable and the determinism claim falsifiable.

### Alternative C — Defer provenance to a later "LLM provenance metadata" ADR
Ship forecasts now, add the provenance trail when agent-written artifacts force it. **Rejected** because the forecast *is* the first persisted model output — the exact trigger the roadmap names for this ADR. Shipping a forecast without provenance means the first model output in the DB is unauditable, and retrofitting provenance onto already-persisted forecasts is the migration nobody wants. The trail is cheap to attach at creation and expensive to backfill.

## Notes
- **Determinism, restated:** the financially-meaningful path is feature construction → label → training → inference. Each link is a known nondeterminism source (hash-order reductions, thread-count-dependent float order, unseeded RNG). The mechanism above pins all four; the golden test in Plan 0036 is what keeps them pinned.
- **No secrets:** model artifacts are data. This ADR introduces no credential — unlike the execution pillar's trade-secret store, which is a separate future ADR.
- The `model_version` hash is the model-artifact analogue of ADR-0018's `model_dump(exclude={run provenance})` equality check: the determinism property is *defined* as "same inputs → same hash → same predictions."
