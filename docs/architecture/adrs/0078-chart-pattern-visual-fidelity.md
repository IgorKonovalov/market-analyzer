# ADR-0078 — Chart-pattern visual fidelity: converging-envelope anchors and apex/breakout rendering

> **Status:** proposed
> **Date:** 2026-07-11
> **Related plan(s):** 0083-chart-pattern-visual-fidelity (this ADR gates it)
> **Related ADRs:** refines the anchor-selection clause of [0048](0048-classical-chart-pattern-detection.md) (connect-the-extremes); extends the render surface of [0049](0049-chart-trendline-overlay-primitive.md) (trendline primitive) and [0061](0061-trendline-pattern-identity-and-colour.md) (pattern identity/colour); the analyst boundary of [0029](0029-advisory-recommendation-boundary.md) is unchanged.

## Context

Classical trendline-family patterns (symmetrical / ascending / descending triangle, rising / falling wedge) are detected by [ADR-0048](0048-classical-chart-pattern-detection.md)'s **connect-the-extremes** rule: the upper boundary is drawn through the *two highest swing highs* in the window and the lower boundary through the *two lowest swing lows* (`analysis/chart_patterns.py:346-353`). That rule lands the lines on real touched prices (no regression — its deliberate, correct property) but it is **blind to which pivots form the converging coil**. On real data a single spike-low pivot is, by definition, one of "the two lowest lows," so it becomes a lower-boundary anchor and drags the line into a deep "V" that no chartist would draw — instead of tracing the *higher-lows* boundary that defines a symmetrical triangle. ADR-0048's own "Negative" section flagged this precisely: *"a single outlier pivot can tilt a trendline."* We are now paying that cost visibly.

Two further gaps separate the app's rendering from a textbook drawing:

1. **No apex.** The renderer (`desktop/renderer/lib/trendlines.ts::computeTrendlineSegments`) draws each boundary only between its two anchors, so the two lines stop short of the apex and never visibly converge — the "coil" shape is lost.
2. **No breakout projection.** The detector already computes a breakout line and a measured-move `target` (`_Formation.break_*`, `measured_height`; `ChartPatternHit.target`), but `_hit_trendlines` sends only the two boundary lines over the wire. The projection arrow a chartist expects (and that the user's reference image shows) is never drawn.

Net effect: a detected pattern does not read as its named shape. The user is walking the pattern set pattern-by-pattern against reference drawings; the symmetrical triangle is the first, and its defects are all three of the above.

## Decision

We adopt three changes, keeping ADR-0048's core commitment intact — **boundary lines sit on real pivots the market touched; no regression fits** (Alternative C of ADR-0048 stays rejected).

1. **Converging-envelope anchor selection (detection).** For the trendline family, the upper boundary is chosen as the pivot-high pair whose line forms the **descending envelope** that keeps (nearly) all window highs at or below it; the lower boundary is the pivot-low pair whose line forms the **ascending envelope** keeping (nearly) all window lows at or above it. A bounded outlier tolerance (a named constant — a small allowed fraction / price-band by which a pivot may sit outside the envelope) lets the selection reject spike pivots the way a human ignores a false poke. Anchors remain **real pivots**; selection is deterministic, strictly trailing, and truncation-invariant, exactly as ADR-0048 requires. This supersedes only the *"two highest highs / two lowest lows"* mechanism of ADR-0048, not its model.

2. **Apex rendering.** The two converging boundaries of a single pattern are extended to their intersection (the apex) and clipped there, so the pattern renders as a coil. When the lines are near-parallel and the apex falls beyond a maximum forward horizon, the extension is omitted (draw the plain segments) rather than shooting a line off-screen.

3. **Breakout / measured-move projection.** The detector's existing breakout direction and measured `target` are plumbed to the renderer as a distinct `projection` line segment and drawn with an **arrowhead**, styled apart from the boundaries. The projection is emitted **only on `confirmed` hits** — direction is a fact only after price breaks, so a `forming` pattern renders the converging apex and dashed boundaries but *no* arrow. This is geometry, not advice: it is the textbook measured-move projection ADR-0048's Notes already sanction as a condition fact; no buy/sell/action field is added and the analyst boundary ([ADR-0029](0029-advisory-recommendation-boundary.md)) is untouched.

## Consequences

### Positive
- Patterns read as their textbook shape: the symmetrical triangle's boundaries trace lower-highs / higher-lows and converge to an apex; the spike-driven "big V" artifact is gone for every trendline-family pattern at once (the fix is in shared anchor selection, not per-pattern).
- The `forming` → `confirmed` distinction becomes visually meaningful: apex always, arrow only once the break is a fact — reinforcing the no-lookahead posture rather than papering over it.
- The measured-move target, already computed and already a sanctioned condition fact, finally surfaces on the chart where the user can see it.

### Negative — the price we pay
- **Another named threshold.** The outlier tolerance is an opinion, like every ADR-0048 constant. Mitigation: it is a named module constant with fixture tests documenting the chosen value; tuning is an edit, not a rewrite.
- **More detection code.** Envelope-with-outlier-rejection is more logic than picking two extremes, and must stay deterministic and truncation-invariant. Mitigation: pinned by the existing truncation-invariance and determinism tests plus new outlier-fixture tests.
- **Apex can be far off-screen** for barely-converging lines; the max-horizon clip is itself a tunable heuristic. Mitigation: named constant, and the fallback (plain segments) is always safe.

### Neutral
- Pattern geometry remains **derived, never persisted** (consistent with ADR-0048 / ADR-0045); no migration, no schema-on-disk change. The wire types (`LineSeg.role`, `TrendlineSpec.role`) gain an additive `"projection"` value.

## Alternatives considered

### Alternative A — Rendering-only polish, keep the extreme-anchor detection
Fix only presentation (extend to apex, add arrow) and leave `_trendline_formation_at` picking the two global extremes. Rejected: the "big V" is an *anchor* defect — the wrong pivots are chosen — so no amount of render polish removes it; the lines would still originate from the spike.

### Alternative B — Regression-fit the boundaries through all pivots
Least-squares a line through all highs and all lows. Rejected again (as in ADR-0048 Alternative C): a fitted line sits at prices the market never traded, misrepresenting the boundary and the breakout level. The envelope keeps anchors on real pivots.

### Alternative C — Fill / shade the pattern region (scoped, not blanket)
Shade the area a pattern encloses. This is **per-pattern, not blanket**, decided against each pattern's reference drawing:
- **Converging coils** (triangle / wedge): **no fill.** The reference is lines + arrow only, and a fill fights the candles for the narrowing space. Rejected here.
- **Pivot-matched humps** (head & shoulders, double top / bottom): **filled.** The reference fills each hump between the pattern skeleton and the neckline, and the fill is what makes the three-hump shape legible. Adopted for these patterns (Plan 0083's head & shoulders phase); the enclosed region and the skeleton polyline that bounds it are the added geometry. Text labels on the pivots (LS / Head / RS / Target) are *not* adopted — pedagogical clutter on a live chart; deferred to the hover tooltip as a followup.
