# ADR-0075 — Recommendation outcome attribution: how we score the advisor's live track record

> **Status:** accepted (2026-07-11) — [Plan 0080](../plans/0080-recommendation-track-record.md) implemented and closed
> **Date:** 2026-07-11
> **Related ADRs:** [ADR-0074](0074-edge-selection-criteria-for-execution.md) (this is the live instrument for its ES-3 honest-validation + ES-5 decay-monitoring gates), [ADR-0029](0029-advisory-recommendation-boundary.md) (what a `Recommendation` is), [ADR-0058](0058-forecast-recommendation-explainability.md) (recommendations already persisted as `runs/advice` artifacts), [ADR-0030](0030-forecasting-subsystem.md)/[ADR-0057](0057-forecast-feature-set-tiers.md) (beats-baseline honesty, mirrored here), [ADR-0018](0018-backtest-result-schema.md) (disk-artifact + SQLite-index persistence pattern + determinism), [ADR-0056](0056-self-warming-metric-store.md) (lifespan-managed background-job pattern for the scheduled scorer), [ADR-0006](0006-persistence-layout.md) (SQLite), [ADR-0046](0046-mcp-large-result-delivery.md) (bounded results)
> **Related plan(s):** [Plan 0080](../plans/0080-recommendation-track-record.md) (implements + accepts this ADR)

## Context

The `advisor` skill's `recommend` tool produces directional recommendations ([ADR-0029](0029-advisory-recommendation-boundary.md)) — direction, entry zone, stop, targets, conviction, and a forecast basis — as-of a specific bar and horizon, and persists the full verdict as a JSON artifact under `runs/advice/` ([ADR-0058](0058-forecast-recommendation-explainability.md)). **Nothing ever revisits those calls to score them against what price actually did.** The user observed a live example (a DOGE long call on one day, price higher the next) and asked that we "note it."

[ADR-0074](0074-edge-selection-criteria-for-execution.md) makes this non-optional: ES-3 (honest validation before capital) and ES-5 (decay monitoring) require knowing whether the live calls hold up — and ES-3's honesty clause plus the whole "self-deception is the killer" thesis require that we **not fool ourselves by remembering only the wins.** A single correct call is one coin-flip landing heads; a *track record* is the evidence. The forces:

- **Anecdote is not evidence.** "Up the next day" vindicates nothing on its own — it's one sample, and the memorable ones are disproportionately the wins.
- **A call carries a stop and targets, not just a direction.** "Stand in long" with a stop that would have been hit is a *loss*, even if price ended higher. Scoring must honor the actual ticket.
- **Accuracy without calibration hides overconfidence.** If the advisor says 80% and is right 55% of the time, the hit-rate looks fine while the probabilities are badly miscalibrated.
- **A hit-rate without a baseline is meaningless.** In an uptrend, "always long" is right most days; the advisor's calls only matter insofar as they beat that trivial alternative.

This ADR fixes the scoring methodology so the resulting track record is **honest by construction**, not by good intentions.

## Decision

We build a recommendation track record whose honesty is structural:

- **Append-only ledger; every call recorded.** Every recommendation — directional *and* flat "no actionable edge" — is written to a durable, queryable ledger row at production time (a SQLite index beside the existing `runs/advice` artifact, the [ADR-0018](0018-backtest-result-schema.md) disk-artifact + SQLite-index pattern). Nothing is deleted or excluded. Cherry-picking is made structurally impossible: you cannot fail to record a loser.
- **Path-dependent scoring.** A directional call is scored by simulating its actual ticket over its horizon: notional entry at the as-of bar's close, then the realized bars decide whether the **stop or a target was hit first** (using each bar's high/low). Outcome is one of `target_hit` / `stopped` / `timeout`, with a realized return and R-multiple (return ÷ initial risk-to-stop). When a single bar's range spans both the stop and a target, **assume the stop hit first** — the conservative, anti-optimistic tie-break.
- **No lookahead; maturity gate.** Scoring uses only bars strictly after the as-of bar, up to as-of + horizon. A recommendation whose horizon has not fully elapsed is `pending` and is not scored — no partial peeking.
- **Baseline-relative, always.** A hit-rate is reported only alongside a naive baseline over the same symbols/horizons (buy-and-hold / always-in-the-trend directional expectation). "Right" counts only insofar as it beats the trivial alternative — the [ADR-0030](0030-forecasting-subsystem.md)/[ADR-0057](0057-forecast-feature-set-tiers.md) beats-baseline discipline applied to live calls.
- **Calibration, not just accuracy.** The forecast probability attached to each call is scored for calibration — Brier score plus a reliability read (stated probability vs realized frequency across buckets) — surfacing overconfidence that raw hit-rate conceals.
- **Scheduled scoring.** A lifespan-managed background job ([ADR-0056](0056-self-warming-metric-store.md) pattern — constructed only when persistence is wired and the flag is on, tick-first boot, cancelled on shutdown) scores matured unscored rows automatically and emits a `recommendation.scored` event, so the track record stays current without being asked. (Distinct from [ADR-0055](0055-in-sidecar-watch-scheduler.md)'s deliberately watch-scoped scheduler — this is a separate duty on its own clock.)
- **Determinism.** Scoring is deterministic given the ledger row + realized bars (no wall-clock in the scoring math; the seam-routed clock is used only for the maturity check). Re-scoring a matured row is byte-identical.
- **Honest small-n.** Aggregates always state their sample size and refuse a conclusion below a stated minimum — a 3-call "67% hit rate" is noise, and the surface says so rather than implying skill.

Flat recommendations are recorded but excluded from the directional hit-rate (a flat call has no direction to score); the ledger keeps them so the denominator of "how often did the advisor commit" is honest.

## Consequences

### Positive
- Turns the advisor from "makes calls" into "makes calls with a measured, baseline-relative, calibrated track record" — the concrete instrument [ADR-0074](0074-edge-selection-criteria-for-execution.md) ES-3/ES-5 need. It also gives the forecaster's honesty a live counterpart: not just "does it beat baseline in walk-forward" but "did the shipped calls hold up."
- Path-dependent scoring kills the "price is higher so the call was right" illusion — a call whose stop was hit intraday is correctly a loss.
- Calibration + baseline-relativity catch the two failure modes raw accuracy hides (overconfidence; trivial-baseline mimicry).
- Append-only-every-call makes the record un-gameable by construction.

### Negative
- **The honest track record may show the advisor has no edge** — hit-rate ≈ baseline, poor calibration. That is the entire point, but it is a result the user must be willing to see and act on (ES-3's "a null result means do not deploy").
- **Bar-resolution scoring cannot perfectly reconstruct the intrabar path.** When stop and target sit inside one bar's range, true order is unknown; the conservative stop-first tie-break is a documented approximation that slightly *understates* performance rather than flattering it — the right direction to err, but an approximation nonetheless.
- **A migration.** The ledger table adds an Alembic migration, serializing against the migration chain (Plan 0044).

### Neutral
- Scope is the advisor's recommendations. Standalone `forecast`-tool probability calls are scoreable by the same machinery and are a natural followup, not part of this decision.

## Alternatives considered

- **Directional-only scoring (ignore the stop/targets).** Rejected as the primary method: it scores a *different, stop-blind* strategy than the one advised, and would call the stopped-out DOGE example a win if price ended higher. (Offered as the simpler option; the user chose path-dependent.)
- **Score on the fly from `runs/advice` artifacts, no table.** Rejected: aggregation, calibration, and baseline comparison over many JSON files are awkward and slow; a track record wants queries. The table indexes the artifacts rather than replacing them.
- **On-demand scoring only.** Rejected: a track record you must remember to refresh rots. Scheduled scoring keeps it current and honest.
- **Record only "notable" or "actioned" calls.** Rejected outright — that *is* the cherry-picking failure [ADR-0074](0074-edge-selection-criteria-for-execution.md) names. Every call is recorded.
- **Optimistic tie-break (target-first when a bar spans both).** Rejected — it inflates the record; conservative stop-first is the honest default.

## Notes
- No secrets, no market claim: this ADR defines *how* live calls are scored, and commits to reporting the result — including an unflattering one — faithfully. It does not assert the advisor has an edge; the track record decides that empirically.
