# ADR-0057 — Forecast feature sets tier by historical depth; the richest eligible tier trains

> **Status:** accepted (Plan 0062 close, 2026-07-07)
> **Date:** 2026-07-06
> **Related plan(s):** [0062](../plans/0062-tiered-forecast-feature-sets.md) (implements)
> **Related ADRs:** [ADR-0054](0054-exogenous-forecast-features-multi-horizon.md) (the lag-1 join + row-drop rules this tiers — not an amendment: both rules hold unchanged *within* every tier), [ADR-0030](0030-forecasting-subsystem.md) (the four invariants, all preserved), [ADR-0051](0051-historized-metric-series-contract.md) (the per-series history-depth asymmetry this responds to), [ADR-0056](0056-self-warming-metric-store.md) (the accrual clock whose warm-up this stops waiting on), [ADR-0040](0040-forecasting-model-artifacts.md) (per-tier `feature_set_id` flows into `model_version` unchanged)

## Context

The v2 forecast feature set ([ADR-0054](0054-exogenous-forecast-features-multi-horizon.md)) consumes five exogenous series conjunctively: a training row is dropped when **any** exogenous feature is missing (the honest no-imputation rule). But the five series have structurally different historical depth ([ADR-0051](0051-historized-metric-series-contract.md)): F&G backfills to 2018-02, funding to 2019-09, MVRV to 2011-12 — while **BTC dominance has no free historical source at all** (accrual-only from deployment) and **open interest's upstream serves only ~30 days** (seed-then-accrue). The two accrual-only series therefore veto every bar before deployment day, regardless of how deep the other three go.

The consequence is live, verified at Plan 0061's close and again 2026-07-06: a `forecast BTC-USD 1d` call reports `fallback_reason: "v2 unavailable: exogenous store has insufficient history (0 of 3109 bars survived the join)"` and silently runs the v1 OHLCV-only set. The cycle features (halving clock, Mayer, 200W distance — computable for every bar from constants + cached bars) and three fully-backfilled exogenous series never reach the model. Waiting doesn't fix this on a useful timescale: one year of accrual yields ~365 v2-eligible daily rows against the ~2,400 available to a feature set that simply excludes the two accrual-only series. The empirical question ADR-0054 deliberately left open — *do exogenous features add out-of-sample skill?* — is unanswerable until some exogenous feature set actually trains.

## Decision

We will make forecast feature sets a **fixed, ordered ladder of tiers**, selected per call by data availability:

1. **`v2-full`** — the existing 27-feature ADR-0054 set (all five exogenous series). Unchanged.
2. **`v2-deep`** (new) — v2-full minus the three features fed by accrual-only series (`btc_dominance`, `dominance_delta_7`, `oi_delta_7`): 24 features over three exogenous series (F&G, funding, MVRV) plus the four cycle features. Its own frozen feature-name tuple and `feature_set_id`; v1's and v2-full's ids do not move.
3. **`v1`** — the 16-feature OHLCV-only set. Unchanged; remains the terminal fallback.

Selection walks the ladder **richest-first** and trains the first tier whose post-join surviving-row count clears its eligibility floor: `max(2·n_splits, MIN_TIER_ROWS)` for the exogenous tiers, where `MIN_TIER_ROWS = 500` (a named constant; rough estimate of a meaningful training population — deliberately far above the existing 2·n_splits crash-floor so a technically-joinable-but-tiny tier cannot shadow a deep one). v1 keeps its existing floor. `ForecastProvenance.fallback_reason` states the full skip chain — every richer tier skipped, each with its surviving-row count — so a v2-deep run says *why* v2-full didn't train, not just that it didn't.

ADR-0054's two rules are untouched **within** each tier: exogenous features still join lag-1 as-of, and a missing value still drops the row (never imputed). The tier boundary is the only new degree of freedom, and it is fixed at three rungs — no per-series dynamic subsets.

## Consequences

### Positive
- The deep-history exogenous features and the cycle features reach the model **now** (~2,400 BTC-USD 1d training rows from 2019-09, the funding-series start), instead of years from now. The ADR-0054 empirical question becomes answerable.
- **Automatic takeover, no future migration:** as dominance/OI accrue past `MIN_TIER_ROWS`, v2-full becomes eligible and wins the richest-first walk without any code change. On hourly bars the accrual series warm in weeks, so the takeover is not hypothetical.
- The skip chain in `fallback_reason` keeps the honesty property Plan 0061 established: a fallback is stated, never silent, at every rung.
- Per-tier `feature_set_id` keeps every model auditable to its exact inputs (ADR-0040 hashing unchanged); v1 and v2-full remain byte-reproducible under their existing ids.

### Negative
- **The takeover moment is a blind switch.** When v2-full first crosses 500 rows it displaces v2-deep even though deep trains on ~5× the rows at that point; the richer tier is not necessarily the more skilled one there. The walk-forward gate still guards every *shipped* probability (a weak v2-full ships no-edge, not garbage), but a skilled v2-deep result may be shadowed by a no-edge v2-full one. Accepted for v1 with a named follow-up: when v2-full first becomes eligible, re-run the comparison artifact and revisit (the evaluate-both alternative below is the escape hatch).
- **Three feature sets to maintain** instead of two — three frozen name tuples, three ids in the pin tests, a matrix of fallback paths. Bounded by sharing one column/join machinery.
- v2-deep's training window starts at the funding series (2019-09), surrendering ~600 v1-era bars v1 itself trains on. If deep underperforms v1, the row difference is a confound the comparison artifact must call out.
- `MIN_TIER_ROWS` is timeframe-agnostic (rows, not days). 500 hourly rows is only ~3 weeks of regime coverage — thin in calendar terms even when numerically eligible. The gate remains the arbiter; the constant may need per-timeframe revisiting.

### Neutral
- Whether v2-deep actually beats v1 (or baseline) is exactly the question Plan 0062's comparison phase answers; this ADR only guarantees the attempt happens and is honestly reported. "Still no edge" is an acceptable outcome (user-confirmed 2026-07-06, reaffirming ADR-0030's posture).
- The advisor (`recommend`, ADR-0029) consumes whatever the forecast leg produces; the tier is visible in the provenance it already carries. No advisor change.

## Alternatives considered

### Alternative A — NaN-native model, no row drops
Switch to the estimator's native missing-value handling (`HistGradientBoostingClassifier` learns a "missing" branch — not imputation) and train one model on all rows and all 27 features. Rejected: missingness of dominance/OI is perfectly correlated with era (everything pre-2026-07 is missing), so the model can learn "which era is this" through the missingness pattern — a leakage-adjacent confound that walk-forward cannot separate from genuine feature signal, since it exists identically in train and test folds.

### Alternative B — Buy or derive the missing history
Source dominance history (derived from per-coin market-cap histories, or a paid aggregator) and OI history (paid: Coinglass-class vendors) so conjunctive v2-full works as designed. Rejected for now: new vendor risk, cost, and an *approximated* dominance series diverging from the accrued true one — all spent on two features whose predictive value is unproven. Revisit only if v2-deep demonstrates edge and there's a concrete hypothesis that dominance/OI add more.

### Alternative C — Evaluate every eligible tier, ship the best validated skill
Train all eligible tiers per horizon and ship the highest out-of-sample skill. Rejected for v1: selecting the max across several walk-forward gates reintroduces a multiple-comparisons bias the single-gate design avoids, and multiplies training compute (already 3 horizons) by the tier count. Named as the escape hatch for the takeover-moment risk above — worth its own evidence when v2-full first becomes eligible.

### Alternative D — Wait for accrual (status quo)
ADR-0056's clock eventually warms everything. Rejected: "eventually" is years on daily bars (the timeframe the multi-horizon design targets), and until then the entire Plan 0059 investment — and the ADR-0054 empirical question — sits idle behind two features nobody has shown to matter.
