# ADR-0049 — Chart trendline overlay primitive (sloped multi-point geometry)

> **Status:** proposed — accepts at the Plan 0052 close
> **Date:** 2026-06-08
> **Related plan(s):** 0052-classical-chart-patterns
> **Related ADRs:** extends the chart event schema of [0017](0017-live-ui-updates-via-sse.md); paired with [0048](0048-classical-chart-pattern-detection.md); follows the `ISeriesPrimitive` precedent set by [0045](0045-candlestick-pattern-span-delivery.md)

## Context

The chart's overlay vocabulary today (`events/__init__.py::OverlaySpec`) covers two shapes: **indicator lines** (`ema`/`sma`/`rsi`/`macd`/`bbands`/`supertrend`, each a per-bar scalar series) and a horizontal **`price_line`** (a single `price` with a label and an optional support/resistance role — the channel Plan 0047 added and the renderer already draws). [ADR-0045](0045-candlestick-pattern-span-delivery.md) added a third drawing channel: a translucent **vertical span band** over a multi-bar pattern, implemented as a lightweight-charts `ISeriesPrimitive` (`desktop/renderer/lib/spans.ts`) that rides the chart's coordinate system and tracks pan/zoom for free.

[ADR-0048](0048-classical-chart-pattern-detection.md)'s classical patterns need a shape none of these can express: a **sloped line segment anchored at two or more `(time, price)` points** — a head-and-shoulders neckline, the two converging trendlines of a triangle, the bounding lines of a wedge. `price_line` is horizontal-only (a single scalar `price`); the span band is a vertical *region*, not a line. A diagonal segment between two anchors has no home.

The forces: keep the **additive-schema discipline** the chart payloads already follow (adding a drawing kind must not bump `chart.show` / `chart.update` payload versions — the `OverlaySpec` docstring and the Plan 0049 `Marker` span fields both established this with optional, `exclude_none`'d fields); reuse the **`ISeriesPrimitive` precedent** so the geometry pans/zooms without a manual visible-range subscription; and anchor the line in the chart's own **time→x / price→y** coordinate space so it stays glued to the candles.

## Decision

We will add a **`trendline`** drawing primitive carried as a new optional field on the chart payloads, rendered client-side as an `ISeriesPrimitive` modelled on `PatternSpanPrimitive`.

- **Wire shape — a dedicated multi-point type, not a strained `OverlaySpec`.** A new `TrendlineSpec` (frozen, `extra="forbid"`) carries an ordered list of ≥2 `TrendPoint(ts, price)` anchors, plus `role`/`label`, a `style` discriminator (`solid` | `dashed` — the forming-vs-confirmed cue), and an optional `pattern` identity. It rides a new optional `trendlines: list[TrendlineSpec] | None` field on `ChartShowPayloadV1` and `ChartUpdatePayloadV1`. The field is optional and `exclude_none`'d, so existing chart payloads are byte-unchanged on the wire and the payload **version does not bump** — exactly how the `Marker` span fields landed.
- **Render via an `ISeriesPrimitive`** (`desktop/renderer/lib/trendlines.ts`): map each anchor through `timeScale().timeToCoordinate(time)` and the candle series' `priceToCoordinate(price)`, then stroke the polyline; clip a segment whose endpoints map off-screen, following the `computeSpanRects` precedent (pure pixel math, canvas-free, unit-tested with a stubbed time/price scale). `style` maps to solid/dashed; `role`/direction maps to a theme color token. The primitive is attached once and fed specs/visibility, recomputing on pan/zoom because the chart re-reads `paneViews()`.
- **A single legend row** governs trendline visibility, mirroring the span layer's `SPAN_LAYER_ID` pattern.

## Consequences

### Positive
- Faithful rendering of necklines and triangle/wedge bounding lines — the visual fidelity the user chose over the horizontal-approximation fallback.
- Reuses the proven `ISeriesPrimitive` path: pan/zoom tracking, theming, and the canvas-free pure-math/unit-test split all come for free from the `spans.ts` template.
- Additive and version-stable: no payload-version bump, no migration, existing overlays untouched on the wire.
- Keeps `OverlaySpec` single-purpose (per-bar scalar overlays + the one horizontal line) rather than bolting multi-point geometry onto a model whose validator already works to keep its two families disjoint.

### Negative — the price we pay
- **A second geometry channel** on the chart payloads (`overlays` *and* `trendlines`), and a second custom primitive family the renderer reconciles alongside spans. More moving parts in `CandlestickChart.tsx` — which is *already* the flagged god-component (the 0047/0049 decomposition follow-up). Mitigation: land the trendline reconcile as a hook from the start (`useTrendlines`), pushing *toward* the decomposition the follow-up wants rather than regrowing the component inline.
- **`priceToCoordinate` ties the line to a specific series.** Unlike the span band (time-only), a trendline needs the price axis, so it depends on the candle series being attached and non-empty. Mitigation: the primitive returns no pane view until the series is ready (the `currentRects`-returns-`[]`-until-attached pattern spans already use).
- Two more wire types (`TrendlineSpec`, `TrendPoint`) to keep mirrored in the hand-maintained `desktop/renderer/types/events.ts`, guarded by the existing TS↔pydantic parity test.

### Neutral
- Trendlines are derived geometry (recomputed from `ChartPatternHit`s), never persisted — consistent with spans and the pattern sweep.

## Alternatives considered

### Alternative A — Cram multi-point geometry into `OverlaySpec`
Add a `points: list[...]` field to the existing overlay model. Rejected: `OverlaySpec` is a single-scalar model whose `model_validator` exists precisely to keep its indicator/`price_line` families disjoint; a points-array would be `None` on every other kind, break that validator's intent, and mix a fundamentally different (multi-point) shape into a scalar model.

### Alternative B — Approximate with `price_line` + point markers
Draw the neckline as a horizontal `price_line` at the break level and mark the pivots with existing point markers. Rejected — and rejected explicitly by the user in the Plan 0052 interview: a sloped neckline and converging triangle lines are not horizontal, and the approximation misrepresents the formation's defining geometry.

### Alternative C — Draw on a separate HTML canvas outside lightweight-charts
Overlay an absolutely-positioned canvas and paint lines onto it. Rejected: it would not track pan/zoom without a manual `subscribeVisibleTimeRangeChange` loop and bespoke coordinate math — the exact problem `ISeriesPrimitive` already solves, and which `spans.ts` already demonstrates solving cleanly in this codebase.

## Notes

`TrendPoint.ts` is a timestamp, not a bar index — consistent with `Marker.event_ts` / `span_*_ts`, so the renderer maps it the same way (`Math.floor(new Date(iso).getTime() / 1000)`), and the line survives a bar-set change as long as the anchor times stay in range.
