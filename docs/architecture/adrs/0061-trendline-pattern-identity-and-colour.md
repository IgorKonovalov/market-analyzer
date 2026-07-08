# ADR-0061 — Trendline colour by pattern type; identity via hover + grouped legend

> **Status:** proposed (accepts at Plan 0067 close)
> **Date:** 2026-07-08
> **Related plan(s):** 0067-trendline-pattern-identification
> **Related ADRs:** amends the colour-model decision of [0049](0049-chart-trendline-overlay-primitive.md) (trendline colour by role); follows the tooltip-interaction-posture precedent of [0060](0060-glossary-tooltip-interaction-posture.md); consumes the detection identity of [0048](0048-classical-chart-pattern-detection.md); the geometry/wire model of [0049](0049-chart-trendline-overlay-primitive.md) and the delivery channel of [0059](0059-trendline-event-channel-and-recompute.md) are untouched.

## Context

[ADR-0049](0049-chart-trendline-overlay-primitive.md) rendered classical-pattern trendlines and coloured them by **role**: a neckline reads accent-blue (`--marker-clicked`), an upper/resistance bound bearish-red (`--marker-bearish`), a lower/support bound bullish-green (`--marker-bullish`), a roleless line neutral-grey. Plan 0064 made the lines actually draw (they never had — a StrictMode lifecycle bug, fixed in commit `b94397f`).

Exercised live (BTC-USD weekly and daily, 2026-07-08), the feature surfaced a legibility gap the role-colour model can't close: a single `detect_chart_patterns` / `scan_chart_patterns` sweep commonly draws **15–35 overlapping lines**, and role colour tells you a line's *job* (bound vs neckline), not *which pattern it belongs to*. Two red upper-bounds could be from a rising wedge and a symmetrical triangle — indistinguishable. The user's report: "same colour and no indication of what pattern represents what." The forces:

- The pattern identity is **already on the wire**: every `TrendlineSpec` (ADR-0049) carries `pattern` (e.g. `symmetrical_triangle`), `style` (`solid`=confirmed / `dashed`=forming), `role`, and a `label`. No new data is needed to identify a line — only a way to surface it.
- The detector emits **both** a `forming` (dashed) and a `confirmed` (solid) `TrendlineSpec` for the same geometry (identical `points`), so the raw count is ~2× what a reader needs.
- A pattern instance's lines are **separate specs with no shared instance id** on the wire (a triangle = two independent bound specs). Grouping *instances* would require a wire change; grouping by pattern *type* does not.

## Decision

We will identify trendlines by **pattern type**, entirely in the renderer, along three axes:

- **Colour by pattern type**, not role. `trendlineColor` maps the spec's `pattern` field to a stable colour from a **categorical palette** (theme tokens, chosen for legibility on both the dark and light chart — the ui-builder consults the `dataviz` palette guidance). A pattern's own lines (e.g. a triangle's two bounds) share one colour, so they read as a single shape. `style` keeps encoding state (solid=confirmed, dashed=forming). This **supersedes ADR-0049's role→colour mapping**; everything else in 0049 (the primitive, the `TrendlineSpec`/`TrendPoint` wire types, the geometry, the delivery channel) stands.
- **De-duplicate forming+confirmed.** A pure renderer helper collapses specs of identical geometry (`pattern` + `points`): when a `solid` (confirmed) line exists for a geometry, its `dashed` (forming) twin is dropped. Forming-only and confirmed-only patterns both still show. This roughly halves the drawn count with no loss of information.
- **Surface identity via two affordances**, following the [ADR-0060](0060-glossary-tooltip-interaction-posture.md) hover posture:
  - a **hover tooltip** — the `TrendlinePrimitive` implements lightweight-charts' `ISeriesPrimitive.hitTest(x, y)` to report the line under the cursor, feeding its `pattern` + state into the chart's existing `ChartTooltip`;
  - a **grouped legend** — the single "Trendlines" `LayersPanel` row expands into one row **per (pattern type, state)** present (name + state + instance count), each with an individual show/hide toggle and hover-to-highlight (hovering a row emphasises its lines and dims the rest).

Identity is thus: colour = pattern type; the line's own hover names the specific pattern+state; the legend gives the roster, per-type visibility, and highlight.

## Consequences

### Positive
- Pattern identity becomes legible: same-coloured lines read as one shape, the legend lists what's present, and hovering any line names it — the "which pattern is this" question is answerable three ways.
- The forming+confirmed collapse roughly halves the on-screen line count with zero information loss, directly addressing the clutter.
- No wire change, no sidecar work, no detection change — the identity data was already present; this is a pure renderer surfacing (the handoff's "renderer-only" holds).

### Negative — the price we pay
- **We give up the support/resistance role colour semantics.** A line's role (neckline/upper/lower) is no longer visually encoded — it remains in the data but is not shown by default (the chosen tooltip content is pattern+state only). Accepted: the user prioritised telling patterns apart over the role cue, and style still encodes state.
- **A 9-type categorical palette must stay legible** on both themes — near the practical ceiling for distinguishable categorical hues. Mitigation: the ui-builder uses a proper categorical palette (dataviz guidance), and same-type instances are disambiguated by the legend + line hover, not by more colours.
- **More renderer interaction state** (a `hitTest` path, a highlight state, per-(type,state) visibility) added to what is already the flagged god-component (`CandlestickChart`). Mitigation: keep the logic in `lib/trendlines.ts` (dedupe, colour, hitTest, highlight) + `useTrendlines` + `LayersPanel`, pushing toward the 0047/0049 decomposition rather than regrowing the component inline.

### Neutral
- Trendlines stay derived, never persisted (ADR-0059) — this ADR changes only how they are coloured and identified, not how they travel or regenerate.

## Alternatives considered

### Alternative A — Colour per pattern instance
A distinct colour per detected line-set. Rejected: a sweep of 15–35 patterns needs 15–35 distinguishable colours — well past the ~8–10 a reader can tell apart — and it would require a wire **instance id** to group a pattern's separate line specs into one colour/entry. Per-type colour + hover/legend gives instance-level identity without either cost.

### Alternative B — Keep role colours; identify via hover + legend only
Leave 0049's role colours; add only the tooltip and legend. Rejected: the user explicitly wants colour to distinguish patterns, and role colour actively *conflates* types (every triangle upper-bound and every rising-wedge upper-bound is the same red) — it works against the goal.

### Alternative C — Always-on inline labels on the lines
Draw the pattern name as text at each line's end. Rejected: at 15–35 overlapping lines the labels collide into noise; hover + a grouped legend scale far better.

### Alternative D — Add a wire `instance_id` for per-instance legend rows
Give each `TrendlineSpec` a per-hit id (sidecar change to `_hit_trendlines`) so a pattern's lines group into one legend row and per-instance toggles. Rejected for now: per-**type** grouping is renderer-derivable and sufficient for the stated need, and keeps this a renderer-only change. Revisit if per-instance control (hide *one* of three triangles) is later wanted — that is the trigger for the wire id.

## Notes

Proposed alongside Plan 0067 and accepts at that plan's close (the ADR-0059 cadence). ADR-0049's primitive, `TrendlineSpec`/`TrendPoint`, and delivery via `chart.trendlines` (ADR-0059) are unchanged — this ADR changes only the colour model and adds the identity affordances.
