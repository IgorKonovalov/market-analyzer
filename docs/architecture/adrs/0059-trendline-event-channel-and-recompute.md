# ADR-0059 — Trendlines on a dedicated event channel, recomputed on chart load

> **Status:** proposed
> **Date:** 2026-07-08
> **Related plan(s):** 0064-chart-pattern-trendline-rendering
> **Related ADRs:** partially supersedes the wire-shape decision of [0049](0049-chart-trendline-overlay-primitive.md) (trendlines carried on `chart.show`/`chart.update`); follows the dedicated-channel precedent of `chart.highlight` markers ([0045](0045-candlestick-pattern-span-delivery.md)); rides the event stream of [0017](0017-live-ui-updates-via-sse.md)

## Context

[ADR-0049](0049-chart-trendline-overlay-primitive.md) gave classical chart patterns a faithful drawing primitive (sloped multi-point lines: necklines, triangle/wedge bounds) and chose to carry the geometry as an optional `trendlines` field on the existing `ChartShowPayloadV1` / `ChartUpdatePayloadV1`. That wire choice was reasonable in isolation but produced two defects once the feature was exercised live (BTC-USD 1h, 2026-07-08):

1. **A wipe race.** `detect_chart_patterns` publishes a `chart.show` *carrying* trendlines; `show_chart` publishes a `chart.show` *without* them. The reducer's `applyChartShow` replaces the whole chart state (`trendlines: payload.trendlines ?? []`), so a plain `show_chart` arriving after a `detect` — SSE ordering is not guaranteed — resets the lines to `[]`. In the live session the first "show + detect" call pair drew nothing; only re-issuing `detect` alone made lines appear.

2. **A durability asymmetry.** Pattern *markers* persist as annotation rows and are re-hydrated by a poll, so they survive a viewer reload. Trendlines are derived-only and vanish on reload or on any subsequent `chart.show`. The `detect_chart_patterns` docstring claims "reopening re-runs detection," but nothing re-runs it — the claim was aspirational.

Markers already solve exactly this shape of problem with a **dedicated channel** (`chart.highlight`, gated to the active chart) plus a **recompute trigger** (`POST /scan_patterns`, which the renderer can fire on demand). Trendlines were the odd one out, coupled to the chart-mount event instead of owning their own channel.

The forces: keep the drawing primitive from ADR-0049 unchanged (it is correct — the bug is upstream of the draw); stop coupling derived geometry to the chart-reset event; and make "the patterns I see reflect the bars I'm looking at" true without inventing a persistence table for derived data.

## Decision

We will move trendlines off `chart.show`/`chart.update` onto a **dedicated `chart.trendlines v1` event**, and make the renderer **recompute them from the current bars** on chart load and on a settled visible-range change — never persisting them.

- **Dedicated channel.** A new `ChartTrendlinesPayloadV1{ symbol, timeframe, trendlines: list[TrendlineSpec] }` mirrors `chart.highlight`'s shape and semantics: the reducer applies it **only when `symbol`+`timeframe` match the active chart**, layering the lines on without touching overlays, range, or markers. The `trendlines` field is **removed** from `ChartShowPayloadV1` and `ChartUpdatePayloadV1` (only `detect_chart_patterns` ever populated it, and it moves to the new event), so a `chart.show` can no longer wipe lines. `applyChartShow` clears trendlines **only on a symbol/timeframe change** (a genuinely new chart context); a same-chart `chart.show` (e.g. an overlay or range change) leaves them intact for the recompute to refresh.
- **Recompute, don't persist.** The renderer fires `POST /scan_chart_patterns` (a sibling of `/scan_patterns`) on chart mount and on a debounced visible-range settle; the endpoint runs the existing detector and publishes `chart.trendlines`. Reopening the viewer, switching symbols and back, or panning into new bars all re-derive the geometry from whatever bars are loaded. `TrendlineSpec` itself is unchanged from ADR-0049.
- **`detect_chart_patterns` becomes layer-only.** It publishes `chart.trendlines`, not `chart.show`; like `highlight_pattern`, it draws onto the chart already showing that symbol/timeframe rather than mounting one. The common agent flow (`show_chart` then `detect`) no longer races, and a user-driven chart gets its lines from the recompute path.

`TrendPoint.ts` stays an ISO timestamp mapped through `timeScale().timeToCoordinate` / `priceToCoordinate` exactly as ADR-0049 specified.

## Consequences

### Positive
- The wipe race is structurally impossible: `chart.show` no longer carries or clears trendlines except on a real context switch, and the trendline channel is independent of chart-mount ordering.
- Durability without a schema: reload/re-open/pan all recompute from current bars, so lines are never stale relative to what's on screen and no persistence table, migration, or poll is added for derived data.
- Symmetry with markers: trendlines now match the `chart.highlight` + `/scan_patterns` pattern the codebase already proves, so the mental model ("dedicated channel, active-chart-gated, recompute to refresh") is one model, not two.

### Negative — the price we pay
- **A new event type** (`chart.trendlines`) to keep mirrored in the hand-maintained `desktop/renderer/types/events.ts` and guarded by the TS↔pydantic parity test, plus a new reducer path and its tests. One more entry in the envelope union.
- **Recompute is chattier than a one-shot push.** Mount + every settled range change issues an HTTP call. Mitigation: debounce the range-change trigger (the lazy-history trigger's precedent) and keep detection cheap (it already reads cached bars only).
- **A wire change that removes a field.** `trendlines` leaves `ChartShowPayloadV1`/`ChartUpdatePayloadV1`. Because the field was optional, `exclude_none`'d, and populated by exactly one producer (which moves to the new event), no other caller is affected — but the parity test and any fixtures referencing it must be updated in lockstep.

### Neutral
- Trendlines remain derived geometry, never persisted — this ADR reaffirms ADR-0049's "Neutral" note and makes it operationally true (something now actually re-derives them).

## Alternatives considered

### Alternative A — Preserve-on-omit in the reducer
Leave trendlines on `chart.show`, but have `applyChartShow` only replace them when the payload carries the field (a plain `show_chart` with no `trendlines` key leaves existing lines intact). Rejected: it fixes the race with the smallest diff, but leaves the durability half unsolved (still no recompute) and keeps two coupled meanings on one event, so the "why did my lines disappear" failure modes stay latent. The dedicated channel costs a little more now and removes the whole class of coupling.

### Alternative B — `detect` uses `chart.update` (merge) instead of `chart.show`
Switch the detector to a merge event that layers trendlines onto the current chart. Rejected: it fixes the race but keeps trendlines welded to the generic chart payloads (so `chart.update` can still carry/clear them elsewhere), and still provides no recompute-on-load path — half the problem.

### Alternative C — Persist trendlines like markers (new table + poll)
Give detected geometry a persistence table and a poll so it survives reload exactly like annotations. Rejected (and rejected by the user in the Plan 0064 interview): persisted geometry can go stale against the current bars, and it adds a schema + migration + poll for data that is cheap to recompute and is explicitly "derived, never persisted" (ADR-0049). Recompute is both simpler and fresher.

## Notes

This ADR is proposed alongside Plan 0064 and accepts at that plan's close ceremony (the ADR-0029/0054 cadence). ADR-0049's primitive, `TrendlineSpec`/`TrendPoint` types, and legend-row model are untouched — this ADR changes only *how the specs travel to the renderer and how often they are regenerated*, not how they are drawn.
