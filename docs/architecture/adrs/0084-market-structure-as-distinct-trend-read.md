# ADR-0084 — Price-action market-structure is a distinct second trend read, reported alongside the composed indicator trend

> **Status:** proposed
> **Date:** 2026-07-12
> **Related plan(s):** [0092-price-structure-and-levels](../plans/0092-price-structure-and-levels.md)

## Context

The condition snapshot exposes exactly one trend classification — the `trend` field: an EMA-stack + ADX read folded with an Ichimoku conjunctive veto ([ADR-0067](0067-ichimoku-in-trend-classification.md), refining [ADR-0023](0023-technical-analysis-surface.md)). It is an **indicator-derived** trend: it reads moving averages and directional strength, not the raw sequence of swing highs and lows.

The 2026-07-12 capability audit surfaced that we have no **price-action / market-structure** trend read at all — the higher-high/higher-low (uptrend) vs lower-high/lower-low (downtrend) sequence that traders actually mark on a chart, plus the "break of structure" (BOS, a swing extreme is taken out in the trend direction) and "change of character" (CHoCH, the first counter-trend structural break) events. This is a different lens from the indicator trend: it can confirm it (both say up), lead it (structure breaks down while EMAs still slope up), or lag it. We already have the primitive it needs — `swing_pivots` (confirmed, trailing) from Plan 0051.

The decision is *where this second trend read lives relative to the existing one*. It is a decision, not a no-brainer, because the tempting move — making the snapshot's single `trend` field "smarter" by folding structure into it — would quietly change the meaning of a field every downstream consumer (the analyst narration, `technical_read` (ADR-0068), the advisor's condition leg (ADR-0029), the multi-timeframe view) already depends on, and would entangle two methods that are valuable precisely *because* they can disagree.

## Decision

We will add market-structure as a **distinct, independently-reported trend read** — a `MarketStructure` model carrying its own `structural_trend` (`up` / `down` / `range`) derived purely from the labeled HH/HL/LH/LL swing sequence, plus the recent BOS/CHoCH events — and surface it **alongside** the existing `trend`, never merged into it. The snapshot's `trend` field keeps its exact ADR-0067 definition and value; `market_structure` is a new sibling field. The two are reported as two facts; when they disagree, that disagreement is itself the signal, and the analyst surfaces both rather than resolving them into one label. Market structure does **not** feed back into, override, or veto the ADR-0067 composed trend.

## Consequences

### Positive
- Adds the price-action trend lens the surface lacked, answering "trend indication" the way chart-markers actually read it (HH/HL structure, BOS, CHoCH).
- The existing `trend` field's contract is untouched — no silent redefinition, no regression for its current consumers (`technical_read`, advisor, multi-timeframe).
- Structure-vs-indicator disagreement becomes an explicit, surfaced condition (e.g. "EMA trend still up, but structure just printed a lower low — CHoCH") instead of being averaged away.
- Reuses the confirmed-only `swing_pivots` primitive, so the anti-lookahead guarantee carries for free (trailing by construction, per ADR-0023).

### Negative
- **Two trend reads can confuse a naive consumer** who wants a single answer. Mitigation: the models name their method explicitly (`trend` = indicator-composed; `market_structure.structural_trend` = price-action), and the analyst is instructed to report both, never to silently pick one.
- **Swing-level structure is noisier than the indicator trend** — the labeling depends on the pivot window, so a small window flip-flops. Mitigation: the pivot window and the BOS/CHoCH margin are named, tunable constants owned like the candlestick thresholds (ADR-0023); defaults are documented.
- Additive schema growth: a new snapshot field + a new model.

### Neutral
- Whether the *advisor* should weigh structure-vs-indicator agreement as a corroboration input is deliberately left open (an ADR-0029 question), out of scope here.

## Alternatives considered

### Alternative A — Fold market structure into the single `trend` field
Rejected. It would redefine a field every downstream consumer already depends on, entangle two distinct methods (moving-average state vs swing sequence), and destroy the ability to see them disagree — which is the whole value of the second lens. It also directly contradicts ADR-0067's specific, tested definition of `trend`.

### Alternative B — Let market structure override the Ichimoku veto (structure wins ties)
Rejected. Swing-level structure is the noisier of the two reads and there is no evidence it should be authoritative over the composed indicator trend. Making it a tiebreaker would bake an unvalidated precedence into the canonical field. The two stay independent.

### Alternative C — Ship structure as a chart annotation only, no snapshot field
Rejected. The HH/HL/BOS/CHoCH read is a condition fact the analyst and (later) the advisor should be able to consume programmatically, not just a drawing. It belongs in the snapshot/tool surface, with the rendering as its paired UI — the same backend-paired-with-UI shape the rest of this work follows.

## Notes
- The companion geometry in Plan 0092 — Fibonacci retracement/extension, classic pivot points, anchored VWAP — needs no ADR of its own: like clustered support/resistance (Plan 0051), these are chart-geometry facts derived from prices/anchors, carry no action semantics, and ride ADR-0023. Only the second-trend-read decision is ADR-worthy, because only it risks redefining an existing field.
- Evidence: capability audit 2026-07-12 — `swing_pivots` present, no HH/HL/BOS/CHoCH labeling or structural-trend read anywhere in `src/`.
