# ADR-0054 — Exogenous forecast features join lag-1 as-of; horizons validate independently

> **Status:** accepted (Plan 0059 close, 2026-07-06)
> **Date:** 2026-06-09
> **Related plan(s):** 0059-forecast-feature-set-v2 (implements), 0055/0056/0057 (supply the series)
> **Related ADRs:** [ADR-0030](0030-forecasting-subsystem.md) (the four invariants this extends to exogenous inputs — not an amendment: 0030 already reserves "configurable N-bar-ahead" horizons), [ADR-0040](0040-forecasting-model-artifacts.md) (model_version hashing the expanded feature set flows into), [ADR-0051](0051-historized-metric-series-contract.md) (the `as_of` read this builds on)

## Context

Plan 0036 shipped the forecasting foundation with 16 features, all derived from the target symbol's own OHLCV. The crypto program adds exogenous series — Fear & Greed, BTC dominance, funding rate, open interest, MVRV, plus computed cycle features (halving clock, Mayer Multiple, 200W-MA distance). Exogenous inputs introduce a leakage surface ADR-0030's invariant 1 names but the current pipeline never had to handle: **temporal alignment**. A daily metric "for" 2026-06-08 may be published hours into 2026-06-09; joining it onto the 2026-06-08 bar is lookahead that no single-series truncation test catches. Worse, publication lag varies by source and is not reliably documented.

Separately, the user wants horizons beyond next-bar (1w / 1mo direction). ADR-0030 already covers N-bar-ahead labels; the open question is whether horizons share a model (multi-output) or each get their own, and how the baseline-beating gate applies per horizon.

## Decision

Two rules, both enforced in `forecast/features.py` and testable at that seam:

1. **Exogenous features join lag-1 as-of.** A feature row for bar `i` (close time `T`) may read only metric points with `ts < T_open` — i.e. the `as_of` lookup is made against the bar's **open** time, not its close. For daily series on daily bars this degrades to "yesterday's value", deliberately surrendering up to one bar of freshness to make publication-lag lookahead structurally impossible, regardless of how sloppily a source timestamps its points. Missing exogenous value (series not yet warm) → the feature is NaN and the row is **dropped from training**, never zero-filled (a zero is a fabricated observation). Cycle features computed from constants + already-cached bars (halving clock, Mayer, 200W distance) are ordinary trailing features and need no lag — their inputs are `bars[0..=i]` already.

2. **Each horizon is its own independently-validated model.** `horizon_bars ∈ {1, 5, 21}` on daily bars (next-day / ~1w / ~1mo) each train, walk-forward-validate (with horizon-purged training labels, as Plan 0036's purge already does), and pass or fail the baseline-beating gate **separately**. A horizon with no edge reports no-edge for that horizon while others may ship probabilities. No multi-output model, no shared verdict.

The expanded feature list bumps `FEATURE_SET_ID` (and hence `model_version` per ADR-0040); forecast provenance grows a `series_inputs` list naming each exogenous `series_id` and the `ts` of the latest point consumed.

## Consequences

### Positive
- Publication-lag lookahead is closed **structurally** — by join geometry, not by per-source lag research that would rot.
- Per-horizon gating preserves ADR-0030's honesty: "edge at 1d, no edge at 1mo" is an expressible, honest output.
- Provenance through `series_inputs` makes a forecast auditable to its exact inputs — and makes a quiet feature-set change impossible (the hash moves).

### Negative
- **Lag-1 throws away freshness.** A genuinely same-day-available metric (funding rate settles intra-day) is consumed one bar late on daily bars. Cost accepted for v1; a per-series "publication-lag verified, may join same-bar" exemption would need its own evidence and a follow-up ADR.
- **Row-dropping shrinks training data** during each new series' warm-up (especially dominance, which only accrues from deployment — ADR-0051). Early v2 models may train on fewer rows than v1 did; the walk-forward gate decides whether that's net-negative, and v1's feature set remains reproducible via its pinned `FEATURE_SET_ID`.
- Three horizons triple validation compute per forecast call. Acceptable at personal-app scale; revisit if the tool gets slow enough to annoy.

### Neutral
- Whether the exogenous features actually add skill is an empirical question the walk-forward gate answers per symbol/timeframe — this ADR only guarantees the attempt is leak-free and honestly reported.

## Alternatives considered

### Alternative A — Same-bar as-of join (`ts <= T_close`)
Maximally fresh, and correct *if* every source's point timestamps reflect true availability. Rejected: availability timestamps are exactly what free APIs document worst; one optimistic timestamp turns the whole model into a lookahead artifact that walk-forward cannot detect (the leak exists in both train and test).

### Alternative B — One multi-output model across horizons
Single training pass, shared representation. Rejected: one gate for three claims — a model could ride next-bar skill to ship an unvalidated 1mo head. Per-horizon independence keeps each shipped probability individually defended.

### Alternative C — Zero/mean-fill missing exogenous values instead of dropping rows
Keeps training-set size. Rejected: imputation fabricates observations the market never produced, and for regime-ish series (F&G, funding) the mean is a substantive — wrong — claim. Dropping is honest; warm-up is temporary.
