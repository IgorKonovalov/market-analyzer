# ADR-0067 — Ichimoku Cloud participates in composed trend classification

> **Status:** proposed
> **Date:** 2026-07-09
> **Related plan(s):** 0073-ichimoku-cloud-indicator
> **Refines:** [ADR-0023](0023-technical-analysis-surface.md) (pure trailing indicators + composed snapshot classification)

## Context

The analysis surface ([ADR-0023](0023-technical-analysis-surface.md)) computes pure trailing indicators in `analysis/indicators.py` and composes them into a single trend/momentum read in `analysis/snapshot.py::condition_snapshot`. Today `_classify_trend` fuses exactly two signals: the EMA stack (`ema20` vs `ema50`, plus `close` vs `ema20`) gated by an ADX strength floor (`ADX_TREND_MIN = 20`). That output — `Trend.{UP,DOWN,SIDEWAYS}` — is what `market-analyst` reports and what feeds the advisory context downstream ([ADR-0029](0029-advisory-recommendation-boundary.md)).

Plan 0073 adds Ichimoku Cloud. The Ichimoku system is, at its core, a **trend/regime classifier**: price above the cloud is a bull regime, below is a bear regime, inside is "no trend" — precisely the question `_classify_trend` answers. Adding it purely as extra numbers in the `indicators` dict (the low-blast-radius option) would leave the app computing a canonical regime signal and then ignoring it in the one place regime is decided. The user chose to let Ichimoku **feed the classification**, which is a behavior change to an output that advisor and analyst consume — a decision, not a no-brainer, because it changes what "the trend is UP" means and can flip existing snapshots.

The constraint that makes this delicate: Ichimoku's leading spans are **displaced**. The cloud drawn *under the current bar* is Senkou A/B **computed `displacement` (26) bars ago** and projected forward. A classifier that reads "price vs the cloud sitting under the current bar" must read `senkou_*[i - displacement]`, never the value computed *at* bar `i` (which is projected into the future and is not yet under any price). Getting this index wrong would be a lookahead bug in the load-bearing anti-lookahead path.

## Decision

We will make Ichimoku a **co-equal confirming input** to `_classify_trend`, combined conjunctively with the existing EMA/ADX signal. Concretely, the classifier derives an Ichimoku regime for the current bar from trailing, correctly-displaced values:

- **cloud under price** = `(senkou_a[i - displacement], senkou_b[i - displacement])`, the spans computed `displacement` bars back and displaced onto the current bar — a purely trailing read;
- **Ichimoku bullish** ⟺ `close > max(cloud)` **and** `tenkan[i] > kijun[i]` (price above cloud and a bullish TK relationship);
- **Ichimoku bearish** ⟺ `close < min(cloud)` **and** `tenkan[i] < kijun[i]`;
- **Ichimoku neutral** otherwise (price inside the cloud, or TK disagreeing with the cloud side).

The composed rule:

> `Trend.UP` requires the existing EMA/ADX path to read up **and** Ichimoku not bearish. `Trend.DOWN` requires the EMA/ADX path to read down **and** Ichimoku not bullish. When the two disagree (EMA-stack up while price is below the cloud, or vice versa), the result is `Trend.SIDEWAYS` — the honest "mixed signals" state. When Ichimoku is undefined (fewer than `span_b + displacement` bars), the classifier falls back to the pre-0073 EMA/ADX behavior unchanged, so short series and the existing tests keep their meaning.

Ichimoku is a **gate that can veto, not one that can manufacture** a trend: it can turn an EMA-stack UP into SIDEWAYS when the cloud disagrees, but it cannot turn SIDEWAYS into UP on its own. This keeps the change conservative — it makes the classifier stricter and `SIDEWAYS` more common, rather than inventing new directional calls the old classifier never made.

## Consequences

### Positive
- The regime signal the app already computes for display is now consistent with the regime signal it decides on — no "computed but ignored" canonical indicator.
- Strictly more conservative: a divergence between the two most-watched regime lenses (moving-average stack and cloud) resolves to `SIDEWAYS` rather than a confident directional label, which is the honest read and lowers the chance of a spurious advisory directional context.
- The displacement handling is settled and documented in one place, so future consumers of the Ichimoku values inherit the correct trailing read.

### Negative
- **This changes existing `ConditionSnapshot` output.** Snapshots that previously read `UP`/`DOWN` on a strong EMA stack while price sat in or across the cloud will now read `SIDEWAYS`. Existing `_classify_trend` tests must be re-tuned, and `market-analyst`/advisor behavior shifts on those symbols. This is intended, but it is a real behavioral delta, not a pure addition.
- The classifier gains a second displacement-indexed read, which is the subtlest correctness surface in the module. It must be pinned by a truncation-invariance test (a snapshot on a prefix equals the full-series snapshot as of the truncation bar) or a lookahead bug hides here.
- The fusion rule (conjunctive veto, TK + price-vs-cloud) is one defensible choice among several; the thresholds are not empirically tuned. It is deterministic and documented, but a future plan may want to revisit the exact combination against backtested regime accuracy.

### Neutral
- Momentum classification (`_classify_momentum`, RSI/MACD) is untouched — Ichimoku feeds trend only.
- The Ichimoku scalar values (`tenkan`, `kijun`, cloud spans under price) also land in the `indicators` dict regardless of the classifier change, so consumers that want the raw numbers get them.

## Alternatives considered

### Alternative A — Additive only (no classifier change)
Add Ichimoku values to the `indicators` dict and leave `_classify_trend` on EMA/ADX. Lowest blast radius, zero re-tuning, no ADR needed. Rejected because the user explicitly chose to have Ichimoku inform the trend, and because leaving a canonical regime classifier computed-but-unused is exactly the kind of latent inconsistency this project tries to avoid.

### Alternative B — Ichimoku as the primary classifier
Replace the EMA/ADX logic with the Ichimoku regime as the main signal. Rejected as too large a swing: it would discard a working, tested classifier and re-tune the whole trend surface against an unproven single lens, with a much larger behavioral delta than the conjunctive-veto approach.

### Alternative C — Additive scalars plus a separate `ichimoku_regime` field
Expose Ichimoku's own regime as a new field on `ConditionSnapshot` without touching `trend`. Rejected because it splits "what is the trend" across two fields that can disagree, pushing the fusion decision onto every consumer instead of settling it once — the opposite of what a composed snapshot is for.

## Notes

The displacement-correctness point (read `senkou_*[i - displacement]`, never `[i]`, for the cloud under the current bar) is the same trailing discipline ADR-0023 established for every other indicator; Ichimoku just makes the "as-computed vs as-plotted" distinction explicit because it is the only indicator whose plotted position differs from its computed bar. The chart *render* of the displaced cloud is a separate, display-only concern handled in Plan 0073 phase 4 and does not touch this decision path.
