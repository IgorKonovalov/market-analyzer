# ADR-0048 — Classical chart-pattern detection (swing-pivot foundation, two-state, trailing)

> **Status:** accepted (Plan 0052 close, 2026-06-10)
> **Date:** 2026-06-08
> **Related plan(s):** 0052-classical-chart-patterns (detection + rendering); foundation extracted in 0051-support-resistance-levels; first traded in 0054-chart-pattern-breakout-strategy
> **Related ADRs:** amends the scope of [0023](0023-technical-analysis-surface.md) (candlestick-only); mirrors [0045](0045-candlestick-pattern-span-delivery.md) (derived, not persisted); paired with [0049](0049-chart-trendline-overlay-primitive.md)

## Context

[ADR-0023](0023-technical-analysis-surface.md) deliberately scoped the in-house analysis surface to **candlestick** patterns (`analysis/patterns.py`, 14 detectors). That choice was sound for what it covered: candlestick patterns are single- or few-bar formations whose recognition at bar `i` reads only `bars[0..=i]`, so they fall out of the no-lookahead non-negotiable for free. **Classical chart patterns** — head & shoulders, double top/bottom, triangles, wedges — are a different animal, and three forces make adding them a genuine decision rather than "more detectors in the same file":

1. **Lookahead risk is real here, not incidental.** A classical pattern is built on *swing pivots* (confirmed local extrema) and its canonical signal is a **breakout** — price closing through a neckline or trendline — that is not a fact until some bar after the formation is visible. A naive detector that scans a whole window "sees" the right shoulder, the neckline, and the break using bars that, relative to the formation, are in the future. Getting this wrong is the cardinal backtesting sin the project guards against everywhere.

2. **Recognition is inherently fuzzy.** How symmetric must two shoulders be? How flat must two tops be to be a "double top" rather than two unrelated peaks? How converging must two trendlines be to be a "triangle"? There is no canonical answer — these are *our* thresholds, the way the candlestick body/shadow ratios already are.

3. **Two recognition models, not one.** Head & shoulders and double top/bottom are recognized by **geometric relations among confirmed pivots** (peak/trough heights, symmetry tolerance, a neckline through the intervening extrema). Triangles and wedges are recognized by **two trendlines** (one over the swing highs, one under the swing lows) and classifying by their slopes and convergence. These need different machinery, and the user's scope (Plan 0052) includes both families.

The data we have to work with: the private `_support_resistance()` in `analysis/snapshot.py` already computes confirmed swing pivots (a bar is a pivot when its high/low is the strict extremum of a centred window with a full wing on each side). Plan 0051 promotes that to a public, reusable `analysis/levels.py::swing_pivots()`. That is the shared foundation both pattern families stand on, so this ADR assumes it exists.

## Decision

We will detect classical chart patterns in a **new** module `src/market_analyser/analysis/chart_patterns.py`, built on the shared `swing_pivots()` primitive, with two recognition models and an explicit two-state lifecycle — never reading a future bar.

- **Two recognition models over confirmed pivots:**
  - *Pivot-matched* — **head & shoulders**, **inverse head & shoulders**, **double top**, **double bottom**: recognized by geometric relations among an ordered run of confirmed swing pivots (relative extremum heights, a symmetry tolerance, a neckline drawn through the intervening troughs/peaks).
  - *Trendline-fit* — **ascending / descending / symmetrical triangle**, **rising / falling wedge**: recognized by **connect-the-extremes** — an upper line through the two highest recent swing highs and a lower line through the two lowest recent swing lows — then classifying by the two line slopes and whether they converge. We connect actual extreme pivots rather than least-squares-fitting all pivots, so the drawn line always sits on prices the market truly touched.

- **Two states, both computed strictly from `bars[0..=i]`:**
  - `forming` — the formation's geometry is present using only **confirmed pivots** (each of which already required a full right-wing of *past* bars relative to `i`, so a pivot is itself trailing), but the confirming break has not happened yet.
  - `confirmed` — in addition, `bars[i]` **closes through** the neckline / breakout trendline by the confirmation margin: a close beyond the line by `k · ATR` (default `k ≈ 0.5`). The margin is volatility-scaled so the same rule holds across symbols and timeframes without per-symbol retuning.

  No future bar is ever read in either state. The anti-lookahead corollary is explicit and pinned by a truncation-invariance test (truncating the series to `bars[0..=i]` reproduces the identical hit, exactly as the candlestick detectors are tested).

- **Thresholds are named, owned module constants** — pivot window, symmetry tolerance (%), neckline-flatness tolerance, min/max pattern width in bars, the `k · ATR` breakout-confirmation margin, and the trendline convergence/slope tolerance. Candlestick recognition already takes this stance (`patterns.py`): the tests assert *internal consistency* (the detector fires on a constructed fixture and respects its own tolerances), not agreement with any external library.

- **A distinct result type, `ChartPatternHit`** (frozen, `extra="forbid"`), separate from the candlestick `PatternHit`. It carries the pattern id, the `state`, `direction`, the ordered pivot points (time + price), the defining line segment(s) (neckline, or the two trendlines), a measured-move `target`, a `strength` score in `[0,1]`, and the completing/confirming `bar_index`. This is both the analytics payload (folded into the condition snapshot per Plan 0052) and the source of the chart geometry that [ADR-0049](0049-chart-trendline-overlay-primitive.md)'s trendline primitive draws.

Like the candlestick sweep ([ADR-0045](0045-candlestick-pattern-span-delivery.md)), classical-pattern hits are **derived and never persisted** — the same bars always yield the same hits, so they are recomputed on demand.

## Consequences

### Positive
- Classical patterns become first-class in all three surfaces the user asked for: **analytics** (a typed `ChartPatternHit`, folded into `analyze_symbol`/`ConditionSnapshot` in Plan 0052), **chart** (geometry for the trendline primitive), and a **tradeable signal** (the confirmed breakout the Plan 0054 strategy consumes — in both directions, once shorts land via [ADR-0050](0050-short-selling-strategy-backtest.md)).
- The `forming` state gives the user the "I can see the setup building" view they explicitly wanted, *without* breaking the no-lookahead rule — a strictly-trailing capability the candlestick detectors don't offer.
- Reuses the swing-pivot primitive (one definition of "pivot" shared by S/R levels and pattern detection), and mirrors the established `patterns.py` convention (pure, trailing, owned thresholds, internal-consistency tests) so the module reads like its sibling.
- Clean separation from candlestick detection: a different recognition model lives in a different module rather than overloading `PatternHit`/`detect_patterns`.

### Negative — the price we pay
- **Fuzzy recognition means inherent false positives and negatives.** The constants encode opinions; some real formations will be missed and some marginal ones flagged. There is no "correct" tuning, only a defensible one. Mitigation: the thresholds are named constants with fixture tests that document the chosen tolerances; tuning is a constant edit, not a rewrite.
- **Forming patterns can disappear.** A formation that is `forming` at bar `i` may not be present at bar `i+k` once a later pivot invalidates the geometry. This is *correct* trailing behavior, but on a live chart it can read as flicker. Mitigation: document it; the renderer styles `forming` distinctly (dashed) so the user reads it as provisional, and the strategy only ever acts on `confirmed`.
- **The `k · ATR` confirmation margin is a real tradeoff.** A small `k` gives earlier signals but more whipsaws; a large `k` is robust but late. We pick `k ≈ 0.5` as a named constant; there is no free lunch, only a documented default.
- **Connect-the-extremes is sensitive to which pivots are picked.** Choosing the two highest highs / two lowest lows over the window is deterministic and lands on real touches, but a single outlier pivot can tilt a trendline. Mitigation: the pivot window and convergence tolerance are tunable constants with per-pattern fixtures; this is simpler and more auditable than a regression fit (the rejected alternative), which trades this sensitivity for a line that floats off the actual price.

### Neutral
- Classical-pattern markers/geometry are recomputed, not stored — consistent with the candlestick sweep, so no migration and no persistence schema.

## Alternatives considered

### Alternative A — Extend `PatternHit` / `detect_patterns` to carry classical patterns
Reuse the candlestick detector and its result type. Rejected: `PatternHit`'s shape (a scalar `strength`, a 1–3 bar `span`) cannot express rich geometry (an ordered pivot run, line segments, a measured target), and overloading one module couples two unrelated recognition models — the very god-module smell the architecture review guards against.

### Alternative B — Confirmed-only detection (drop the `forming` state)
Only emit a pattern after its break is a fact. Simpler, fewer false positives. Rejected: the user explicitly wants to see setups building, and the `forming` state is achievable trailing-clean (it reads only confirmed past pivots), so the simplification would forfeit a capability for no correctness gain.

### Alternative C — Least-squares-fit trendlines through all pivots
Fit a regression line through all swing highs and all swing lows. Rejected: a regression line can sit at a price the market never traded (a "trendline" no candle touches), which misrepresents the formation and the breakout level; connect-the-extremes lands on real pivots and is simpler and fully deterministic.

### Alternative D — Use an external TA pattern library / ML recognition
Adopt an off-the-shelf recognizer or train a model. Rejected: [ADR-0009](0009-rewrite-data-layer-in-house.md) / [ADR-0023](0023-technical-analysis-surface.md) commit to a pure-Python, in-house, no-pandas/numpy analysis layer; external libraries are rarely lookahead-audited, and an ML recognizer is non-deterministic/opaque, failing both the determinism non-negotiable and the explainability bar. Geometric rules over named thresholds are auditable; a model's "0.83 head-and-shoulders" is not.

## Notes

The strength score is relative conviction, not a probability — same disclaimer as `PatternHit.strength`. The measured-move `target` is the textbook projection (e.g. neckline ± head height) and is reported as a *condition/geometry fact*, never as a recommendation — the analyst non-negotiable holds: `ChartPatternHit` has no buy/sell/action field, and the trading signal (which *does* act) lives in the Plan 0054 strategy under `strategies/`, on the other side of the analyst/strategy split.
