# ADR-0100 — On-chart legibility: persistent labels + primitive-hover tooltips

> **Status:** proposed
> **Date:** 2026-07-14
> **Related plan(s):** 0105-chart-legibility (accepts this ADR at close)
> **Extends:** [ADR-0060](0060-glossary-tooltip-interaction-posture.md) (glossary-tooltip posture); honours [ADR-0023](0023-technical-analysis-surface.md) (client-side indicator math), [ADR-0062](0062-user-chart-style-overrides.md) (static vs styleable colours), [ADR-0088](0088-lightweight-charts-v5-panes.md) (v5 panes), [ADR-0046](0046-mcp-large-result-delivery.md)/[ADR-0077](0077-user-originated-display-overlays.md) (nothing new on the wire)

## Context

The Plan 0096 declutter made the chart *tidier* but a phase-6 human smoke (AAPL 1d, most indicators on) surfaced a cluster of **legibility** gaps that are orthogonal to clutter: the four sub-panes (OBV, Williams %R, MFI, A/D line) carry no on-pane label so you cannot tell which pane is which; every Fibonacci level draws in one colour with no visible swing anchor; every pivot level draws in one colour; the HH/HL/LH/LL and BOS/CHoCH structure markers have no hover explanation the way candlestick markers do; and several overlays (Ichimoku, OBV, the RSI overlay key, the classical chart-pattern legend rows) have no glossary entry so their legend label is inert text.

Three facts constrain the fix. **(1)** lightweight-charts v5.2.0 has **no pane-title API** (`IPaneApi` exposes `setHeight`/`attachPrimitive`/`getHTMLElement` but no `title`), so a per-pane label must be drawn by us. **(2)** Everything these findings need is **already computed client-side** — `fibonacci.ts` and `pivots.ts` mirror the Python and carry each level's identity (`ratio`, `label`) and the resolvable swing anchor; the structure markers carry their `HH/HL/…` label; the glossary is build-time JSON. No new value has to ride the SSE/MCP wire. **(3)** The app already has **two hover mechanisms** that these findings should reuse rather than reinvent: the glossary `<GlossaryTerm>` tooltip (ADR-0060) for legend rows, and the `useChartTooltip` crosshair-move handler that hit-tests trendline/divergence primitives by pixel and matches candlestick markers by time.

The decision worth recording is the **posture**: are on-chart *labels* and hover *tooltips on non-glossary primitives* a first-class, repeatable pattern (with a stated colour/identity discipline), or one-off renderer tweaks? Given a fourth chart-annotation plan is imminent (0104) and a controller refactor after it (0098), the posture needs to be written once so later work conforms instead of re-deciding per primitive.

## Decision

We will treat **persistent on-chart labels** and **hover tooltips on chart primitives** as a first-class, renderer-owned legibility layer, governed by four rules:

1. **Labels and level identity are derived client-side, never fetched.** A per-pane label reads the indicator's existing legend name; a Fibonacci/pivot level label reads the `ratio`/`label` its client-side compute already produces; the swing anchor is exposed from `fibonacci.ts` (not put on the wire). This keeps ADR-0023's client-math boundary and ADR-0046/0077's "no glossary/label content on the wire" intact.

2. **Per-level colours are a static, per-element palette — not user-styleable.** Fibonacci and pivot levels gain a fixed `ratio→colour` / `level→colour` map (e.g. R-levels warm, S-levels cool, the central pivot neutral), living beside the existing `FIB_LINE_COLOR`/`PIVOT_LINE_COLOR` constants. Per ADR-0062 these structure overlays stay non-styleable; the change is monochrome → a fixed multi-hue palette, still theme-resolved, still not a `chartStyle` override.

3. **On-primitive hover reuses the one existing crosshair-move path.** Structure-marker tooltips feed the market-structure markers into `useChartTooltip`'s time-keyed lookup (the same mechanism candlestick markers use), reusing the glossary content (`hh`/`hl`/`lh`/`ll`/`bos`/`choch`). Pivot/fib level identity on hover reuses the crosshair `param.point.y`→price proximity test rather than converting the `IPriceLine`s into hit-testable primitives. There is exactly one hover handler; new hoverable things register into it.

4. **Glossary coverage is completeness-gated.** Every legend row the renderer emits a `glossaryKey` for must resolve to a glossary entry (enforced by a renderer test), so a new overlay/pattern row cannot ship as inert text. Content that is TradFi analysis semantics is authored by `market-analyst` (the owner of that meaning); the wiring is `ui-builder`.

## Consequences

### Positive
- A dense chart becomes legible without removing information: every pane is named, every level is identifiable, every marker explains itself, every legend row is a glossary handle.
- One hover mechanism and one labelling discipline — later chart work (0104 drawings, 0098 controller) inherits a stated pattern instead of re-deriving per primitive.
- Zero wire/schema/CSP surface change; the whole layer is renderer state over already-computed data, so it carries no determinism or security weight.

### Negative
- More DOM/canvas labels that must be **kept positioned across pan/zoom/rebuild** — a per-pane HTML-overlay label has to track pane resize and survive the candle-type chart rebuild, and a mispositioned label is its own legibility bug.
- A per-level colour palette is one more static, theme-aware palette to maintain (two themes × two structure families), and over-colouring can *reduce* legibility if the hues aren't disciplined.
- The pivot/fib "nearest-level-on-hover" proximity test is a heuristic (pixel threshold) — it can mis-identify when levels are packed tightly, and would then need the heavier primitive-conversion fallback.

### Neutral
- Chart-pattern glossary entries introduce a `chart_pattern` glossary category (the existing `candlestick` category is the wrong hat for classical H&S/triangle/wedge patterns); a one-line `GlossaryCategory` union addition, no behavioural weight.

## Alternatives considered

### Alternative A — Native pane titles / put label content on the wire
Rejected on two counts: v5 has no pane-title API to use, and the label/identity data is already computed client-side, so shipping it over MCP/SSE would duplicate state and cross the ADR-0023/0046 boundary for no gain.

### Alternative B — Convert pivot/fib price-lines into hit-testable primitives for hover
A custom `ISeriesPrimitive` per level would give exact pixel hit-testing (like trendlines). Rejected **as the default** because it is materially heavier than reusing the existing crosshair-Y-proximity path over the `IPriceLine`s we already draw; it stays documented as the fallback if the proximity heuristic proves imprecise in practice.

### Alternative C — Leave legibility to the layer legend only (no on-chart labels/tooltips)
The inline legend (0096) already lists every layer with its swatch. Rejected because the legend answers "what layers exist", not "which of these four identical-looking panes is MFI" or "what does this HH marker mean" — the questions the smoke actually raised are positional and per-primitive, which a single corner legend cannot answer.

## Notes
- Prime clipping-bug suspects for Plan 0105's live-repro phase (finding 6): the trendline (`lib/trendlines.ts`) and divergence (`lib/divergences.ts`) primitives deliberately extrapolate off-grid via `resolveTimeX` and rely on canvas clipping; the span band (`lib/spans.ts`) null-skips off-grid endpoints (vanishes rather than bleeds). This ADR does not decide the fix — the plan reproduces live first, then clips the identified primitive to the visible logical range.
